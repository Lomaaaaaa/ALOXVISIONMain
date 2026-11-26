"""Users and rights (``alox.core.users``) on a dict-backed properties fake; passwords are synthetic."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest

from alox.core.users import (
    Base64PasswordStore,
    PasswordStore,
    PropertiesTab,
    UserAction,
    Users,
    UserType,
)


class FakeProperties:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = dict(values or {})

    def get(self, dotted: str, default: Any = None) -> Any:
        return self.values.get(dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        self.values[dotted] = value

    def exists(self, dotted: str) -> bool:
        return dotted in self.values


def b64(text: str) -> str:
    return base64.b64encode(text.encode("cp1252")).decode("ascii")


@pytest.fixture
def props() -> FakeProperties:
    return FakeProperties({
        "user.password.engineer": b64("synthetic-1"),
        "user.password.administrator": b64("Synthétique"),
        "user.rights.operator.mesa": True,
        "user.rights.operator.autoexposuresetup": False,
        "user.rights.engineer.autoexposuresetup": True,
        "user.rights.admin.exitapp": "TRUE",
        "user.rights.engineer.propertiescamera": True,
    })


# ============================================================================================== enums


def test_user_type():
    assert [(u.value, u.label) for u in UserType] == [(0, "Operator"), (1, "Engineer"), (2, "Admin")]
    assert [u.rights_key for u in UserType] == ["operator", "engineer", "admin"]
    assert [u.password_key for u in UserType] == ["operator", "engineer", "administrator"]
    assert UserType.parse("Admin") is UserType.ADMIN
    assert UserType.parse("administrator") is UserType.ADMIN
    assert UserType.parse(1) is UserType.ENGINEER
    for bad in ("maintenance", 3):
        with pytest.raises(ValueError):
            UserType.parse(bad)


def test_user_actions():
    assert len(UserAction) == 32
    assert UserAction.EXIT_APP == 0 and UserAction.EDIT_PROPERTIES == 5 and UserAction.MESA_SETUP == 31
    assert UserAction.AUTO_EXPOSURE_SETUP.label == "Auto Exposure Setup"
    assert UserAction.AUTO_EXPOSURE_SETUP.key == "autoexposuresetup"
    assert UserAction.SEARCH_WAVELENGTH_SETUP.key == "searchwavelengthsetup"
    assert UserAction.EXIT_APP.key == "exitapp"
    assert len({a.key for a in UserAction}) == 32
    assert UserAction.parse("Refocus Oxyd Setup") is UserAction.REFOCUS_OXYD_SETUP
    assert UserAction.parse("measureoxyd") is UserAction.MEASURE_OXYD
    assert UserAction.parse(30) is UserAction.MESA
    for bad in ("Start Vacuum", 32):
        with pytest.raises(ValueError):
            UserAction.parse(bad)


def test_right_keys():
    assert Users.right_key(UserAction.DETECT_CIRCLE_SETUP, UserType.ADMIN) == "user.rights.admin.detectcirclesetup"
    assert Users.right_key("Get Image", "Operator") == "user.rights.operator.getimage"
    assert Base64PasswordStore.key(UserType.ADMIN) == "user.password.administrator"


# ============================================================================================== login


def test_starts_as_operator_and_login(props: FakeProperties):
    users = Users(props)
    assert users.current is UserType.OPERATOR
    assert users.login(UserType.ENGINEER, "wrong") is False
    assert users.current is UserType.OPERATOR
    assert users.login(UserType.ENGINEER, "synthetic-1") is True
    assert users.current is UserType.ENGINEER
    assert users.login("Admin", "Synthétique") is True               # cp1252 text, key "administrator"
    assert users.current is UserType.ADMIN
    assert users.logout() is UserType.OPERATOR and users.current is UserType.OPERATOR


def test_check_password_does_not_change_the_user(props: FakeProperties):
    users = Users(props)
    assert users.check_password(UserType.ENGINEER, "synthetic-1") is True
    assert users.check_password(UserType.ENGINEER, "Synthetic-1") is False
    assert users.current is UserType.OPERATOR


def test_empty_password_property_is_the_empty_password_but_a_missing_one_refuses_all(props: FakeProperties):
    props.set("user.password.operator", "")
    users = Users(props)
    assert users.check_password(UserType.OPERATOR, "") is True
    assert users.check_password(UserType.OPERATOR, "x") is False
    del props.values["user.password.administrator"]
    assert users.check_password(UserType.ADMIN, "") is False          # fixed: LabVIEW let anyone in
    assert users.login(UserType.ADMIN, "") is False


def test_invalid_base64_never_matches(props: FakeProperties):
    props.set("user.password.engineer", "abc")                       # bad padding
    assert Users(props).check_password(UserType.ENGINEER, "") is False


def test_change_password_round_trip(props: FakeProperties):
    users = Users(props)
    users.change_password(UserType.OPERATOR, "new-synthetic")
    assert props.values["user.password.operator"] == b64("new-synthetic")
    assert users.login(UserType.OPERATOR, "new-synthetic")


def test_another_password_store_can_replace_base64(props: FakeProperties):
    class HashedStore:
        def __init__(self) -> None:
            self.hashes: dict[UserType, bytes] = {}

        def check(self, user: UserType, password: str) -> bool:
            return self.hashes.get(user) == hashlib.sha256(password.encode()).digest()

        def change(self, user: UserType, password: str) -> None:
            self.hashes[user] = hashlib.sha256(password.encode()).digest()

    store: PasswordStore = HashedStore()
    users = Users(props, store=store)
    assert not users.login(UserType.ENGINEER, "synthetic-1")         # the Base64 property is ignored
    users.change_password(UserType.ENGINEER, "other")
    assert users.login(UserType.ENGINEER, "other") and users.store is store


# ============================================================================================== rights


def test_rights_of_the_current_user(props: FakeProperties):
    users = Users(props)
    assert users.has_right(UserAction.MESA) is True
    assert users.has_right(UserAction.AUTO_EXPOSURE_SETUP) is False
    assert users.has_right(UserAction.TILT) is False                 # missing property: FALSE
    users.login(UserType.ENGINEER, "synthetic-1")
    assert users.has_right("Auto Exposure Setup") is True
    assert users.has_right(UserAction.MESA) is False


def test_rights_of_another_user_and_text_values(props: FakeProperties):
    users = Users(props)
    assert users.has_right(UserAction.EXIT_APP, UserType.ADMIN) is True   # untyped INI text "TRUE"
    props.set("user.rights.admin.exitapp", "FALSE")
    assert users.has_right(UserAction.EXIT_APP, "Admin") is False
    with pytest.raises(ValueError):
        users.has_right("Start Vacuum")


def test_properties_tab_rights(props: FakeProperties):
    users = Users(props)
    assert len(PropertiesTab) == 9
    assert users.has_tab_right(PropertiesTab.CAMERA, UserType.ENGINEER) is True
    assert users.has_tab_right("camera") is False
    with pytest.raises(ValueError):
        users.has_tab_right("unknown")
