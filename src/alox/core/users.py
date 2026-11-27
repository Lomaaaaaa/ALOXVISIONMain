"""Users, passwords and rights (``alox_manager_users``; map Part 1 §4.3, §8 note 9, §9 question 4).

LabVIEW FGV methods (``alox_typedef_users_managers_method``): Init, Exit, Get User, Login, Logout, User Rights, Check
Password. Observed in ``alox_manager_users d0..d179``:

- Init, Exit and Logout set the current user to Operator (d3, d4, d11).
- Login / Check Password read the property ``user.password.<operator|engineer|administrator>`` (note: **administrator**
  for the Admin user, while the rights use ``admin``), Base64-decode it and compare it with the typed password; Login
  changes the current user only on success; a failure returns an error status (d0, d6, d7..d10, d175..d178).
- User Rights reads the Boolean property ``user.rights.<admin|engineer|operator>.<action>`` (default FALSE),
  ``<action>`` being the label of ``alox_typedef_user_action`` in lower case without spaces ("Auto Exposure Setup" →
  ``autoexposuresetup``, d12..d173). An unknown action or user gives error 9002 "Invalid Args" and no right.
- ``alox_default.ini`` also holds nine rights ``properties<tab>`` (admin, camera, chamber, datalogging, modules,
  monochromator, network, overlays, xyzstages) used by the properties UI (``alox_properties_ui`` frame 1):
  :meth:`Users.has_tab_right`. Deviation (owner, P1): the PYROMETER page has its own right ``propertiespyrometer``
  (LabVIEW used the monochromator right); when the key is missing, the user's ``propertiesmonochromator`` is used.

The rights are checked on UI actions only; the REST API has no authentication (map Part 5).

Passwords go through a :class:`PasswordStore`; :class:`Base64PasswordStore` reads and writes today's properties, so a
hashed store can replace it later without touching :class:`Users` (the owner has not decided, §9 question 4).

Not to confuse with ``typedef_user_action`` (54 actions such as "Start Vacuum") and ``typedef_user_type`` (Operator,
Engineer, Maintenance): they belong to the Pyro ``framework.llb`` (typedefs.md), not to this manager.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import logging
import threading
from enum import IntEnum, StrEnum
from typing import Any, Protocol

__all__ = ["TAB_FALLBACK", "Base64PasswordStore", "PasswordStore", "Properties", "PropertiesTab", "UserAction", "UserType",
           "Users"]

log = logging.getLogger(__name__)


class Properties(Protocol):
    """The application settings as this module needs them (``alox_manager_properties`` Get / Set Property): dotted
    names such as ``user.rights.operator.mesa``. :class:`alox.config.typed_ini.TypedIni` provides ``get``; tests
    can pass a dict-backed fake."""

    def get(self, dotted: str, default: Any = None) -> Any: ...

    def set(self, dotted: str, value: Any) -> None: ...


class UserType(IntEnum):
    """``alox_typedef_user_type`` (enum U16)."""

    OPERATOR = 0
    ENGINEER = 1
    ADMIN = 2

    @property
    def label(self) -> str:
        """The LabVIEW item text: ``Operator``, ``Engineer``, ``Admin``."""
        return self.name.capitalize()

    @property
    def rights_key(self) -> str:
        """The section part of ``user.rights.<user>.<action>``."""
        return self.name.lower()

    @property
    def password_key(self) -> str:
        """The key part of ``user.password.<user>`` (``administrator`` for Admin)."""
        return "administrator" if self is UserType.ADMIN else self.name.lower()

    @classmethod
    def parse(cls, value: UserType | int | str) -> UserType:
        """A user from its enum value, its label (``"Admin"``) or its key (``"administrator"``); ValueError (LabVIEW
        9002 "Unknown user") otherwise."""
        if isinstance(value, str):
            text = value.strip().lower()
            for user in cls:
                if text in (user.rights_key, user.password_key):
                    return user
            raise ValueError(f"unknown user {value!r}")
        return cls(value)


class UserAction(IntEnum):
    """``alox_typedef_user_action`` (enum U16, 32 items)."""

    EXIT_APP = 0
    CREATE_PROFILE = 1
    DELETE_PROFILE = 2
    EDIT_PROFILE = 3
    CHANGE_PROFILE = 4
    EDIT_PROPERTIES = 5
    GET_IMAGE = 6
    GET_IMAGE_SETUP = 7
    AUTOFOCUS = 8
    AUTOFOCUS_SETUP = 9
    REFOCUS = 10
    REFOCUS_SETUP = 11
    REFOCUS_OXYD = 12
    REFOCUS_OXYD_SETUP = 13
    DETECT_CIRCLE = 14
    DETECT_CIRCLE_SETUP = 15
    TRACKING = 16
    TRACKING_SETUP = 17
    MEASURE_OXYD = 18
    MEASURE_OXYD_SETUP = 19
    SEARCH_WAVELENGTH = 20
    SEARCH_WAVELENGTH_SETUP = 21
    SEARCH_OBJECT = 22
    SEARCH_OBJECT_SETUP = 23
    AUTO_EXPOSURE = 24
    AUTO_EXPOSURE_SETUP = 25
    BOWING = 26
    BOWING_SETUP = 27
    TILT = 28
    TILT_SETUP = 29
    MESA = 30
    MESA_SETUP = 31

    @property
    def label(self) -> str:
        """The LabVIEW item text, e.g. ``"Auto Exposure Setup"``."""
        return _ACTION_LABELS[self]

    @property
    def key(self) -> str:
        """The last part of ``user.rights.<user>.<key>``: the label in lower case without spaces."""
        return self.label.lower().replace(" ", "")

    @classmethod
    def parse(cls, value: UserAction | int | str) -> UserAction:
        """An action from its enum value, its label (``"Auto Exposure Setup"``) or its key (``"autoexposuresetup"``);
        ValueError (LabVIEW 9002 "Unknown user action") otherwise."""
        if isinstance(value, str):
            text = value.strip().lower().replace(" ", "")
            for action in cls:
                if text == action.key:
                    return action
            raise ValueError(f"unknown user action {value!r}")
        return cls(value)


_ACTION_LABELS = {
    UserAction.EXIT_APP: "Exit App",
    UserAction.CREATE_PROFILE: "Create Profile",
    UserAction.DELETE_PROFILE: "Delete Profile",
    UserAction.EDIT_PROFILE: "Edit Profile",
    UserAction.CHANGE_PROFILE: "Change Profile",
    UserAction.EDIT_PROPERTIES: "Edit Properties",
    UserAction.GET_IMAGE: "Get Image",
    UserAction.GET_IMAGE_SETUP: "Get Image Setup",
    UserAction.AUTOFOCUS: "Autofocus",
    UserAction.AUTOFOCUS_SETUP: "Autofocus Setup",
    UserAction.REFOCUS: "Refocus",
    UserAction.REFOCUS_SETUP: "Refocus Setup",
    UserAction.REFOCUS_OXYD: "Refocus Oxyd",
    UserAction.REFOCUS_OXYD_SETUP: "Refocus Oxyd Setup",
    UserAction.DETECT_CIRCLE: "Detect Circle",
    UserAction.DETECT_CIRCLE_SETUP: "Detect Circle Setup",
    UserAction.TRACKING: "Tracking",
    UserAction.TRACKING_SETUP: "Tracking Setup",
    UserAction.MEASURE_OXYD: "Measure Oxyd",
    UserAction.MEASURE_OXYD_SETUP: "Measure Oxyd Setup",
    UserAction.SEARCH_WAVELENGTH: "Search Wavelength",
    UserAction.SEARCH_WAVELENGTH_SETUP: "Search Wavelength Setup",
    UserAction.SEARCH_OBJECT: "Search Object",
    UserAction.SEARCH_OBJECT_SETUP: "Search Object Setup",
    UserAction.AUTO_EXPOSURE: "Auto Exposure",
    UserAction.AUTO_EXPOSURE_SETUP: "Auto Exposure Setup",
    UserAction.BOWING: "Bowing",
    UserAction.BOWING_SETUP: "Bowing Setup",
    UserAction.TILT: "Tilt",
    UserAction.TILT_SETUP: "Tilt Setup",
    UserAction.MESA: "Mesa",
    UserAction.MESA_SETUP: "Mesa Setup",
}


class PropertiesTab(StrEnum):
    """The tabs of the properties UI that have a right ``user.rights.<user>.properties<tab>`` (the nine of
    ``alox_default.ini``, and ``pyrometer``, added: owner P1)."""

    ADMIN = "admin"
    CAMERA = "camera"
    CHAMBER = "chamber"
    DATALOGGING = "datalogging"
    MODULES = "modules"
    MONOCHROMATOR = "monochromator"
    NETWORK = "network"
    OVERLAYS = "overlays"
    XYZSTAGES = "xyzstages"
    PYROMETER = "pyrometer"


#: A tab right missing from ``alox.ini`` takes the value of this one (owner P1: machines keep their access).
TAB_FALLBACK: dict[PropertiesTab, PropertiesTab] = {PropertiesTab.PYROMETER: PropertiesTab.MONOCHROMATOR}


def _as_bool(value: Any) -> bool:
    """A right as read from the properties: a bool, or the INI text ``TRUE``/``FALSE`` when the accessor is untyped."""
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "T", "1", "YES", "ON")
    return bool(value)


def _password_bytes(password: str) -> bytes:
    """The bytes LabVIEW compares: the Windows ANSI text (cp1252); UTF-8 for characters outside it."""
    try:
        return password.encode("cp1252")
    except UnicodeEncodeError:
        return password.encode("utf-8")


class PasswordStore(Protocol):
    """Where the passwords live and how they are compared."""

    def check(self, user: UserType, password: str) -> bool:
        """True when ``password`` is the password of ``user``."""
        ...

    def change(self, user: UserType, password: str) -> None:
        """Make ``password`` the new password of ``user``."""
        ...


class Base64PasswordStore:
    """Today's storage: ``user.password.<operator|engineer|administrator>`` holds the Base64 of the password (an empty
    property is the empty password). Base64 is an encoding, not a protection (map Part 1 §8 note 9).

    Fixed: a *missing* property refuses every password (LabVIEW read it as the empty password, so a settings file
    without the password section let anyone log in as Engineer or Administrator with an empty password)."""

    def __init__(self, properties: Properties) -> None:
        self._properties = properties

    @staticmethod
    def key(user: UserType) -> str:
        return f"user.password.{UserType(user).password_key}"

    def _stored(self, user: UserType) -> bytes | None:
        if not self._properties.exists(self.key(user)):
            log.warning("property %s is missing: no password can match it", self.key(user))
            return None
        raw = self._properties.get(self.key(user), "")
        text = "" if raw is None else str(raw).strip()
        try:
            return base64.b64decode(text.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError):
            log.warning("property %s is not valid Base64: no password can match it", self.key(user))
            return None

    def check(self, user: UserType, password: str) -> bool:
        stored = self._stored(user)
        return stored is not None and hmac.compare_digest(stored, _password_bytes(password))

    def change(self, user: UserType, password: str) -> None:
        self._properties.set(self.key(user), base64.b64encode(_password_bytes(password)).decode("ascii"))


class Users:
    """The current user and the rights (``alox_manager_users``). Thread-safe.

    The current user starts as Operator (LabVIEW "Init"); which user should be logged in after boot is an open question
    (map §9 question 4). ``store`` defaults to :class:`Base64PasswordStore` on ``properties``.
    """

    def __init__(self, properties: Properties, store: PasswordStore | None = None,
                 initial: UserType = UserType.OPERATOR) -> None:
        self._properties = properties
        self._store: PasswordStore = store if store is not None else Base64PasswordStore(properties)
        self._lock = threading.Lock()
        self._current = UserType.parse(initial)

    @property
    def current(self) -> UserType:
        """The logged-in user (LabVIEW "Get User")."""
        with self._lock:
            return self._current

    @property
    def store(self) -> PasswordStore:
        return self._store

    def check_password(self, user: UserType | int | str, password: str) -> bool:
        """True when ``password`` is the password of ``user``; the current user does not change ("Check Password")."""
        return self._store.check(UserType.parse(user), password)

    def login(self, user: UserType | int | str, password: str) -> bool:
        """Become ``user`` when ``password`` is right and return True; otherwise keep the current user and return False
        (LabVIEW returns an error status instead)."""
        user = UserType.parse(user)
        if not self._store.check(user, password):
            log.info("login as %s refused (wrong password)", user.label)
            return False
        with self._lock:
            self._current = user
        return True

    def logout(self) -> UserType:
        """Back to Operator ("Logout"); returns the new current user."""
        with self._lock:
            self._current = UserType.OPERATOR
            return self._current

    def change_password(self, user: UserType | int | str, password: str) -> None:
        """Store a new password for ``user`` (``alox_change_password_ui``; checking the old one is the caller's job)."""
        self._store.change(UserType.parse(user), password)

    @staticmethod
    def right_key(action: UserAction | int | str, user: UserType | int | str) -> str:
        """``user.rights.<user>.<action>``."""
        return f"user.rights.{UserType.parse(user).rights_key}.{UserAction.parse(action).key}"

    def has_right(self, action: UserAction | int | str, user: UserType | int | str | None = None) -> bool:
        """The right of ``user`` (default: the current user) for ``action`` ("User Rights"); FALSE when the property is
        missing. An unknown action or user raises ValueError (LabVIEW 9002)."""
        who = self.current if user is None else UserType.parse(user)
        return _as_bool(self._properties.get(self.right_key(action, who), False))

    @staticmethod
    def tab_right_key(tab: PropertiesTab | str, user: UserType | int | str) -> str:
        """``user.rights.<user>.properties<tab>``."""
        return f"user.rights.{UserType.parse(user).rights_key}.properties{PropertiesTab(tab).value}"

    def has_tab_right(self, tab: PropertiesTab | str, user: UserType | int | str | None = None) -> bool:
        """The right ``user.rights.<user>.properties<tab>`` of the properties UI (default FALSE); a missing
        ``propertiespyrometer`` takes the user's monochromator right (:data:`TAB_FALLBACK`)."""
        who = self.current if user is None else UserType.parse(user)
        tab = PropertiesTab(tab)
        value = self._properties.get(self.tab_right_key(tab, who), None)
        if value is None and tab in TAB_FALLBACK:
            value = self._properties.get(self.tab_right_key(TAB_FALLBACK[tab], who), None)
        return _as_bool(False if value is None else value)
