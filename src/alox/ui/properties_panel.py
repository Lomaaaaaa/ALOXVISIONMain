"""The properties panel ("Vision Core", ``alox_properties_ui.vi``), as data, and its logic, without Qt.

LabVIEW (``alox_properties_ui`` front panel ``0232 ...vip.png`` and diagram ``...vid*.png``): the SETTINGS button of
the core window (right "Edit Properties") puts this VI into the Settings sub-panel (``alox_manager_panels`` "Open
Panel" "Vision Core"). Title "ALOX VISION CORE PROPERTIES"; a 12-frame sequence:

- frame 0 "Translate UI here" (empty); frame 1 "Enabling properties user rights": each page of the Tab Control is
  "Enabled" or "Disabled & Grayed" by ``user.rights.<operator|engineer|admin>.properties<tab>`` of the current user
  (default F): STATUS always enabled, SYSTEM ``chamber``, HARDWARE DEVICES always enabled with its pages CAMERA
  ``camera``, XYZ STAGE ``xyzstages``, MONOCHROMATOR ``monochromator`` and PYROMETER ``monochromator`` (sic), DISPLAY
  ``overlays``, NETWORK ``network``, MODULES ``modules``, LOGGING ``datalogging``, ADMIN ``admin``; then the USER
  ACTIONS list (41 rights: the 32 ``alox_typedef_user_action`` and the 9 tab rights, item texts below) and "Is
  User='Operator' AND has right to control XYZ stages => show tab XYZ by default" (the right read is
  ``user.rights.admin.propertiesxyzstages``, kept as drawn: :meth:`PropertiesModel.initial_page`);
- frames 2..8 read the properties into the controls of each page (the keys below; defaults of the "Get Property"
  calls), frame 9 the event loop: ``Timeout`` 300 ms refreshes SYS. / TILT / BOWING / MESA STATUS ID and DESC and
  CURRENT TILT (°); each control's "Value Change" is a "Set Property" of its key (in memory); APPLY RIGHTS writes the
  41 rights of the selected USER; SET PASSWORD opens ``alox_change_password_ui``; RESET JOB INFO is SEMI manager
  "Init"; Chuck Calibration starts a bowing measurement with the global ``Chuck Calibration``; SAVE alox.ini copies
  ``alox.ini`` over ``<exe>\\alox_default.ini``; EXIT ends the loop; frame 10 then runs properties "Save" (alox.ini)
  and restores the cameras' software exposure mode, frame 11 "Close Panel".

Here each page with fields is a :class:`~alox.ui.settings_panels.SettingsModel` on ``core.properties`` (the
``alox.ini`` store: :class:`alox.core.properties.Properties`), gated by the page's right; :class:`PropertiesModel`
groups them: edits are pending until APPLY (into the store in memory, as LabVIEW's "Set Property"), SAVE (apply,
then ``alox.ini``: only the changed keys are written, in place) or EXIT (LabVIEW's properties "Save" at the end,
after asking about pending edits). Keys read by the devices at start-up only (ports, COM ports, camera names, flips,
flat field, timeouts) are marked: they apply at the next start; the panel never reconfigures a device.

Deviations: the device controls of the LabVIEW pages (camera grab, exposure, gain, LED; XYZ moves, joysticks, homing,
zero; monochromator moves; module start / stop / show / kill) are not here: the main window has the moves and
joysticks, and this application has no external hardware modules (MODULES shows the state of its devices). The
module ports and heartbeats of the LabVIEW TCP modules are kept editable (they stay in ``alox.ini`` for the LabVIEW
tools) and marked as next-start values. COM ports are indicators in LabVIEW; here they can be edited (next start), as
the application opens the serial ports itself. Periods are shown in ms as stored (LabVIEW shows heartbeats and the
mesa log periods in s). The web service and websocket ports are added to NETWORK (read at start-up).
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alox.api.backend import Measure
from alox.core.users import PropertiesTab, UserAction, UserType
from alox.ui.settings_panels import (
    ApplyResult,
    Field,
    Group,
    Kind,
    Page,
    PanelSpec,
    Selector,
    SettingsModel,
    check,
    colour,
    num,
    path,
    ring,
    text,
)

__all__ = ["HARDWARE", "PAGES", "PANEL_NAME", "RIGHT_TEXTS", "TOP_PAGES", "PropertiesModel", "PropertiesPage",
           "StatusLine", "page_by_key"]

#: The panel name of ``alox_manager_panels`` (caption of the SETTINGS tab).
PANEL_NAME = "Vision Core"
TITLE = "ALOX VISION CORE PROPERTIES"
#: The STATUS refresh of the event loop's ``Timeout`` case (ms).
STATUS_PERIOD_MS = 300


@dataclass(frozen=True)
class PropertiesPage:
    """A page of the Tab Control: ``key``, LabVIEW caption, the tab right (None: always enabled), its groups, the keys
    applied at the next start (``restart``), the selector (ADMIN: USER), ``parent`` (the HARDWARE DEVICES pages) and
    ``omitted`` (LabVIEW controls of the page left out, with the reason)."""

    key: str
    title: str
    right: PropertiesTab | None
    groups: tuple[Group, ...] = ()
    restart: frozenset[str] = frozenset()
    selector: Selector | None = None
    parent: str | None = None
    omitted: tuple[str, ...] = ()

    @property
    def has_fields(self) -> bool:
        return bool(self.groups)

    def spec(self) -> PanelSpec:
        """The page as a settings-panel description (for its :class:`SettingsModel`)."""
        return PanelSpec("PROPERTIES", f"{PANEL_NAME} {self.title}", self.title, "alox_properties_ui.vi",
                         UserAction.EDIT_PROPERTIES, pages=(Page(self.title, self.groups),), selector=self.selector,
                         omitted=self.omitted)

    def field_keys(self) -> list[str]:
        return [f.key for group in self.groups for f in group.fields]


def _ms(label: str, key: str, default: int, maximum: int = 3_600_000, minimum: int = 0) -> Field:
    return num(label, key, default, minimum, maximum, unit="ms", step=100)


def _port(label: str, key: str, default: int) -> Field:
    return num(label, key, default, 1, 65_535)


def _com(label: str, key: str, default: int) -> Field:
    return num(label, key, default, 1, 256)


def _mm(label: str, key: str) -> Field:
    return num(label, key, 0.0, -1000.0, 1000.0, unit="mm", step=0.001, decimals=4)


def _keys(*groups: Group) -> frozenset[str]:
    return frozenset(f.key for g in groups for f in g.fields)


# ================================================================================================== pages
STATUS = PropertiesPage("status", "STATUS", None, omitted=(
    "timer 1..7: the durations of the init frames (debug indicators)",))

_CHAMBER = Group("CHAMBER", (
    ring("CHAMBER TYPE", "general.coretype", 0, "PROCESS", "MEASURE"),
    num("CHAMBER NUMBER", "general.core", 1, 1, 99)))
_WAFER = Group("WAFER", (
    Field("WAFER SIZE", "general.wafersize", Kind.ENUM, 100.0,
          items=((76.2, '76.2 mm (3")'), (100.0, '100 mm (4")'), (150.0, '150 mm (6")'))),))
_CHUCK = Group("CHUCK (BOWING)", (
    _mm("deltaZ1", "hardware.chuck.dz1"), _mm("Zcenter", "hardware.chuck.dz2"),
    _mm("deltaZ3", "hardware.chuck.dz3"), _mm("deltaZ4", "hardware.chuck.dz4"),
    _mm("deltaZ5", "hardware.chuck.dz5"),
    num("MAX PARALLELISM", "hardware.chuck.maxparallelism", 100.0, 0.0, 100_000.0, unit="µm", step=1.0),
    path("PATH CHUCK REF FILE", "hardware.chuck.pathChuckRefFile")))
SYSTEM = PropertiesPage("system", "SYSTEM", PropertiesTab.CHAMBER, (_CHAMBER, _WAFER, _CHUCK),
                        restart=frozenset({"general.wafersize"}), omitted=(
    "Estimated chuck BOW (µm): indicator computed from the chuck reference file, not ported",
    "LOAD CHUCK REF (bowchuckA/B from the chuck reference file): the file layout is not confirmed, not ported"))

HARDWARE = PropertiesPage("hardware", "HARDWARE DEVICES", None)


def _camera_group(title: str, cam: str, name: str) -> Group:
    base = f"hardware.camera.{cam}."
    return Group(title, (
        text("NAME", base + "name", name),
        check("FLAT FIELD CORRECTION", base + "flatfieldcorrection", False),
        check("H FLIP", base + "hflip", False),
        check("V FLIP", base + "vflip", False),
        path("FLAT FIELD IMAGE", base + "flatfieldimage")))


_CAM_MODULE = Group("CAMERA MODULE", (
    _port("CAM PORT", "hardware.camera.port", 503),
    _ms("CAM TIMEOUT", "hardware.camera.timeout", 5000, 600_000),
    _ms("CAM HEARTBEAT", "hardware.camera.heartbeat", 30_000)))
_HIGH, _LOW = _camera_group("HIGH MAG.", "highmag", "HighMag"), _camera_group("LOW MAG.", "lowmag", "LowMag")
CAMERA = PropertiesPage("camera", "CAMERA", PropertiesTab.CAMERA, (_HIGH, _LOW, _CAM_MODULE),
                        restart=_keys(_HIGH, _LOW, _CAM_MODULE), parent="hardware", omitted=(
    "GRAB HI / LO MAG, EXPO MODE, SHUTTER, SET GAIN, SET LED: live camera controls (the main window grabs images)",
    "CALIB. (alox_calibration_ui) and FFC (alox_vignetting_ui) buttons: dialogs not ported",
    "CALIB OK / FFC OK: indicators of the calibration manager"))

_XYZ_MODULE = Group("XYZ MODULE", (
    _port("XYZ PORT", "hardware.xy.port", 504),
    _ms("XYZ TIMEOUT", "hardware.xy.timeout", 5000, 600_000),
    _ms("XYZ HEARTBEAT", "hardware.xy.heartbeat", 30_000),
    _com("XY COM PORT", "hardware.xy.comport", 2),
    _com("Z COM PORT", "hardware.z.comport", 3)))
_COORDS = Group("COORDINATES", (
    ring("COORD. SYSTEM", "hardware.xy.stagescoordsys", 3, "Coord. Sys. 1", "Coord. Sys. 2", "Coord. Sys. 3",
         "Coord. Sys. 4"),
    _mm("WAFER ORIGIN X", "hardware.xy.waferxorg"), _mm("WAFER ORIGIN Y", "hardware.xy.waferyorg"),
    _mm("CHUCK ORIGIN X", "hardware.xy.chuckxorg"), _mm("CHUCK ORIGIN Y", "hardware.xy.chuckyorg")))
XYZ = PropertiesPage("xyz", "XYZ STAGES", PropertiesTab.XYZSTAGES, (_COORDS, _XYZ_MODULE), restart=_keys(_XYZ_MODULE),
                     parent="hardware", omitted=(
    "X / Y / Z position controls, MOVE, STOP, JOYSTICK, HOMING: the main window has the moves and joysticks",
    "XY ZERO / XY ZERO chuck (origin = the current position): the origins are typed here",
    "XYZ SETUP (alox_xyz_stages_ui: speeds and limits): dialog not ported",
    "WAFER POSITION: the wafer map picture (alox_manager_xy_map)"))

_MONO = Group("MONOCHROMATOR MODULE", (
    _port("MONO PORT", "hardware.monochromator.port", 505),
    _ms("MONO TIMEOUT", "hardware.monochromator.timeout", 5000, 600_000),
    _ms("MONO HEARTBEAT", "hardware.monochromator.heartbeat", 30_000),
    _com("MONO COM PORT", "hardware.monochromator.comport", 2),
    _ms("WAIT POSITION", "hardware.monochromator.waitposition", 0, 60_000)))
MONOCHROMATOR = PropertiesPage("monochromator", "MONOCHROMATOR", PropertiesTab.MONOCHROMATOR, (_MONO,),
                               restart=_keys(_MONO), parent="hardware", omitted=(
    "WL control, MOVE, current position: the main window has SET WAVELENGTH",))

_PYRO = Group("PYROMETER", (_com("PYRO COM PORT", "hardware.pyro.comport", 1),))
PYROMETER = PropertiesPage("pyrometer", "PYROMETER", PropertiesTab.MONOCHROMATOR, (_PYRO,), restart=_keys(_PYRO),
                           parent="hardware")

_TEXT = Group("OVERLAYS TEXT", (
    text("FONT NAME", "overlays.fontname", "Arial"),
    num("FONT SIZE", "overlays.fontsize", 24, 1, 500),
    colour("COLOR", "overlays.fontcolor", "3399FF"),
    num("LINE SPACE", "overlays.fontlinespace", 30, 0, 1000)))
_ALARM = Group("ALARM", (
    num("ALARM LEFT", "overlays.alarmleft", 2000, 0, 20_000, unit="px"),
    num("ALARM TOP", "overlays.alarmtop", 60, 0, 20_000, unit="px"),
    num("ALARM WIDTH", "overlays.alarmwidth", 300, 0, 20_000, unit="px"),
    num("ALARM HEIGHT", "overlays.alarmheight", 150, 0, 20_000, unit="px")))
_DISPLAY = Group("DISPLAY", (
    _ms("DISP TIMER", "general.displaytimer", 500, 60_000, 50),
    check("SAVE OVERLAY INFOS", "general.saveOverlay", True),
    check("SAVE OVERLAY RESULTS", "general.saveOverlayRes", True)))
DISPLAY = PropertiesPage("display", "DISPLAY", PropertiesTab.OVERLAYS, (_TEXT, _ALARM, _DISPLAY),
                         restart=frozenset({"general.displaytimer"}))

_WEB = Group("WEB SERVICE", (
    _port("WEB SERVICE PORT", "webservice.port", 8001),
    _port("WEBSOCKET PORT", "websocket.port", 6123),
    check("LOG WEB SERVICE", "log.webapi", True)))
_WATCHDOG = Group("WATCHDOG", (
    check("WATCHDOG", "general.watchdog", True),
    _ms("HEARTBEAT FREQ", "heartbeat.frequency", 1000, 3_600_000, 100),
    num("WATCHDOG RETRIES", "general.watchdogmaxfails", 3, 0, 1000)))
NETWORK = PropertiesPage("network", "NETWORK", PropertiesTab.NETWORK, (_WEB, _WATCHDOG),
                         restart=frozenset({"webservice.port", "websocket.port"}))

MODULES = PropertiesPage("modules", "MODULES", PropertiesTab.MODULES, omitted=(
    ("CAM / XYZ / MONO START, STOP, SHOW, KILL: the external LabVIEW hardware modules; this application drives its "
     "devices itself (their states are shown)"),))

_MESA_LOG = Group("MESA (MEASURE CHAMBER)", (
    _ms("LOG MESA IMAGE EACH", "log.imagesfrequency", 0, 86_400_000),
    _ms("LOG MESA DATA EACH", "log.datafrequency", 0, 86_400_000)))
_DATA_LOG = Group("SAVE DATA", tuple(check(label, f"log.save{name}data", True) for label, name in (
    ("AUTOFOCUS", "autofocus"), ("WAVELENGTH", "wavelength"), ("AUTO EXPOSURE", "autoexpo"), ("MESA", "mesa"),
    ("TILT", "tilt"), ("BOWING", "bowing"))))
_IMAGE_LOG = Group("SAVE IMAGES", tuple(check(label, f"log.save{name}", True) for label, name in (
    ("WAVELENGTH", "wavelengthimage"), ("REFOCUS", "refocusimages"), ("AUTO EXPOSURE", "autoexpoimages"),
    ("MESA", "mesaimages"), ("TILT", "tiltimages"), ("BOWING", "bowingimages"))))
LOGGING = PropertiesPage("logging", "LOGGING", PropertiesTab.DATALOGGING, (_MESA_LOG, _DATA_LOG, _IMAGE_LOG))

#: The 41 items of USER ACTIONS (``alox_properties_ui`` frame 1.2): the LabVIEW item text and the right's key.
RIGHT_TEXTS: tuple[tuple[str, str], ...] = (
    ("Exit Application", UserAction.EXIT_APP.key),
    ("Create vision profile", UserAction.CREATE_PROFILE.key),
    ("Delete vision profile", UserAction.DELETE_PROFILE.key),
    ("Edit vision profile", UserAction.EDIT_PROFILE.key),
    ("Change vision profile", UserAction.CHANGE_PROFILE.key),
    ("Access/edit vision core properties", UserAction.EDIT_PROPERTIES.key),
    *((f"{verb} '{name}' function{suffix}", action.key)
      for name, run, setup in (
          ("Get Image", UserAction.GET_IMAGE, UserAction.GET_IMAGE_SETUP),
          ("Autofocus", UserAction.AUTOFOCUS, UserAction.AUTOFOCUS_SETUP),
          ("Refocus", UserAction.REFOCUS, UserAction.REFOCUS_SETUP),
          ("Refocus Oxyd", UserAction.REFOCUS_OXYD, UserAction.REFOCUS_OXYD_SETUP),
          ("Detect Circle", UserAction.DETECT_CIRCLE, UserAction.DETECT_CIRCLE_SETUP),
          ("Tracking", UserAction.TRACKING, UserAction.TRACKING_SETUP),
          ("Measure Oxyd", UserAction.MEASURE_OXYD, UserAction.MEASURE_OXYD_SETUP),
          ("Search Wavelength", UserAction.SEARCH_WAVELENGTH, UserAction.SEARCH_WAVELENGTH_SETUP),
          ("Search Object", UserAction.SEARCH_OBJECT, UserAction.SEARCH_OBJECT_SETUP),
          ("Auto Exposure", UserAction.AUTO_EXPOSURE, UserAction.AUTO_EXPOSURE_SETUP),
          ("Bowing Measurement", UserAction.BOWING, UserAction.BOWING_SETUP),
          ("Tilt Measurement", UserAction.TILT, UserAction.TILT_SETUP),
          ("Mesa Measurement", UserAction.MESA, UserAction.MESA_SETUP))
      for verb, action, suffix in (("Run", run, ""), ("Edit", setup, " settings"))),
    *((f"Edit '{name}' properties tab", f"properties{tab.value}") for name, tab in (
        ("Chamber", PropertiesTab.CHAMBER), ("Camera", PropertiesTab.CAMERA),
        ("Monochromator", PropertiesTab.MONOCHROMATOR), ("XYZ stages", PropertiesTab.XYZSTAGES),
        ("Display", PropertiesTab.OVERLAYS), ("Network", PropertiesTab.NETWORK), ("Modules", PropertiesTab.MODULES),
        ("Data Logging", PropertiesTab.DATALOGGING), ("Admin", PropertiesTab.ADMIN))),
)


def _rights(title: str, items: tuple[tuple[str, str], ...]) -> Group:
    return Group(title, tuple(check(label, "user.rights.{user}." + key, False) for label, key in items))


USERS = Selector("USER", "user", tuple((u.rights_key, ("ADMINISTRATOR" if u is UserType.ADMIN else u.name))
                                       for u in UserType))
ADMIN = PropertiesPage("admin", "ADMIN", PropertiesTab.ADMIN, (
    _rights("APPLICATION", RIGHT_TEXTS[:6]), _rights("FUNCTIONS", RIGHT_TEXTS[6:32]),
    _rights("PROPERTIES TABS", RIGHT_TEXTS[32:])), selector=USERS, omitted=(
    "SAVE alox.ini (copy alox.ini over <exe>/alox_default.ini): kept as SAVE AS DEFAULTS",))

#: The pages of the Tab Control, in order; the HARDWARE DEVICES pages in :data:`HARDWARE_PAGES`.
TOP_PAGES: tuple[PropertiesPage, ...] = (STATUS, SYSTEM, HARDWARE, DISPLAY, NETWORK, MODULES, LOGGING, ADMIN)
HARDWARE_PAGES: tuple[PropertiesPage, ...] = (CAMERA, XYZ, MONOCHROMATOR, PYROMETER)
PAGES: dict[str, PropertiesPage] = {p.key: p for p in (*TOP_PAGES, *HARDWARE_PAGES)}


def page_by_key(key: str) -> PropertiesPage:
    return PAGES[key]


# ================================================================================================== model
@dataclass(frozen=True)
class StatusLine:
    """One block of the STATUS page: its title, the status ID and its translated description."""

    title: str
    code: int
    text: str


@dataclass(frozen=True)
class SaveResult:
    """What APPLY / SAVE did: the keys written, the errors, the file saved, and the keys that apply at the next start."""

    written: tuple[str, ...] = ()
    errors: Mapping[str, str] = field(default_factory=dict)
    saved: Path | None = None
    restart: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


class _Store:
    """``core.properties`` read at each call (the core replaces it at boot), as a :class:`SettingsModel` profile."""

    def __init__(self, core: Any) -> None:
        self._core = core

    def get(self, dotted: str, default: Any = None) -> Any:
        return self._core.properties.get(dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        self._core.properties.set(dotted, value)

    def save(self) -> Path:
        return self._core.properties.save()


class PropertiesModel:
    """The pages of the panel on ``core.properties`` and the panel's actions (module docstring). Not thread-safe:
    used by the UI thread (the status reads only in-memory state)."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self.store = _Store(core)
        self.models: dict[str, SettingsModel] = {
            page.key: SettingsModel(page.spec(), self.store, allowed=lambda p=page: self.allowed(p))
            for page in PAGES.values() if page.has_fields}

    # -- rights
    def allowed(self, page: PropertiesPage | str) -> bool:
        """The page's right for the current user (``Users.has_tab_right``, default F); always for STATUS and the
        HARDWARE DEVICES container."""
        page = PAGES[page] if isinstance(page, str) else page
        if page.right is None:
            return True
        try:
            return self.core.users.has_tab_right(page.right)
        except ValueError:
            return False

    def initial_page(self) -> tuple[str, str | None]:
        """The page shown at opening (frame 1.3): HARDWARE DEVICES / XYZ STAGES when the user is Operator and
        ``user.rights.admin.propertiesxyzstages`` (as drawn) is TRUE, else STATUS."""
        users = self.core.users
        xyz = bool(users.has_tab_right(PropertiesTab.XYZSTAGES, UserType.ADMIN))
        if users.current is UserType.OPERATOR and xyz:
            return HARDWARE.key, XYZ.key
        return STATUS.key, None

    # -- editing
    @property
    def dirty(self) -> bool:
        return any(m.dirty for m in self.models.values())

    def changes(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for model in self.models.values():
            found.update(model.changes())
        return found

    def apply(self) -> SaveResult:
        """APPLY: every pending edit into the store (in memory, used by the next reader); a page without its right
        keeps its edits and reports the refusal."""
        written: list[str] = []
        errors: dict[str, str] = {}
        for key, model in self.models.items():
            if not model.dirty:
                continue
            result: ApplyResult = model.apply()
            written += result.written
            errors.update({k or key: v for k, v in result.errors.items()})
        return SaveResult(tuple(written), errors, None, self._restart(written))

    def save(self) -> SaveResult:
        """SAVE: :meth:`apply`, then ``alox.ini`` (only the changed keys, in place)."""
        applied = self.apply()
        if not applied.ok:
            return applied
        try:
            saved = self.save_file()
        except (OSError, ValueError) as exc:
            return SaveResult(applied.written, {"": f"alox.ini not saved: {exc}"}, None, applied.restart)
        return SaveResult(applied.written, {}, saved, applied.restart)

    def save_file(self) -> Path | None:
        """Properties "Save" (EXIT): the keys changed in memory since the last save, None without a file."""
        props = self.core.properties
        if props.path is None:
            return None
        return props.save()

    def cancel(self) -> None:
        for model in self.models.values():
            model.cancel()

    @staticmethod
    def _restart(written: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        keys = {key for page in PAGES.values() for key in page.restart}
        return tuple(key for key in written if key in keys)

    @staticmethod
    def restart_key(key: str) -> bool:
        return any(key in page.restart for page in PAGES.values())

    # -- STATUS
    def status(self) -> list[StatusLine]:
        """SYS. / TILT / BOWING / MESA STATUS ID and DESC (``alox_manager_*_status_ID`` "Get")."""
        core = self.core
        lines = [StatusLine("SYSTEM", *core.system_status())]
        for title, measure in (("TILT FUNCTION", Measure.TILT), ("BOWING FUNCTION", Measure.BOWING),
                               ("MESA FUNCTION", Measure.MESA)):
            lines.append(StatusLine(title, *core.measure_status(measure)))
        return lines

    def tilt(self) -> float:
        """CURRENT TILT (°) (``alox_manager_tilt`` "Get Tilt")."""
        return float(self.core.tilt())

    # -- NETWORK, MODULES
    def urls(self) -> tuple[str, str]:
        """WEB SERVER URL ``http://<host>:<port>/alox/v1/`` and WEB SOCKET URL ``ws://<host>:<port>`` (frame 8)."""
        host = self.core.hostname()
        rest = self.core.properties.get("webservice.port", 8001)
        ws = self.core.properties.get("websocket.port", 6123)
        return f"http://{host}:{rest}/alox/v1/", f"ws://{host}:{ws}"

    def modules(self) -> list[tuple[str, str]]:
        """The devices of the application and their state (MODULES)."""
        found = []
        for name, device in sorted(self.core.devices.items()):
            state = getattr(device, "state", None)
            found.append((name, str(getattr(state, "name", state) or "-")))
        return found

    # -- ADMIN
    def change_password(self, user: UserType | int | str, password: str, confirm: str) -> str | None:
        """``alox_change_password_ui`` OK: NEW PASSWORD = CONFIRM PASSWORD → ``user.password.<user>`` = Base64 of
        the password (``Users.change_password``), then ``alox.ini`` saved; returns the refusal, None when done. The
        right is ADMIN's (the button is on that page)."""
        if not self.allowed(ADMIN):
            return "change password: not allowed for this user"
        if password != confirm:
            return "the passwords do not match"
        self.core.users.change_password(user, password)
        try:
            self.save_file()
        except (OSError, ValueError) as exc:
            return f"password changed in memory, alox.ini not saved: {exc}"
        return None

    def save_as_defaults(self) -> Path:
        """SAVE alox.ini: save ``alox.ini``, then copy it over ``<exe>/alox_default.ini`` (the file a new machine
        starts from). :class:`OSError` / :class:`ValueError` on failure."""
        if not self.allowed(ADMIN):
            raise ValueError("save as defaults: not allowed for this user")
        self.save_file()
        source = self.core.properties.path
        if source is None or not Path(source).is_file():
            raise ValueError("alox.ini has no file")
        target = Path(self.core.folders.defaults_ini)
        shutil.copyfile(source, target)
        return target

