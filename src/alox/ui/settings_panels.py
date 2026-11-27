"""The function settings panels (the ⚙ buttons), as data, and their editing logic, without Qt.

LabVIEW: each function has a settings panel ``alox_function_<ll|hl>_<x>_ui_config.vi`` that ``alox_manager_panels``
"Open Panel" puts into the Settings sub-panel of the core window (map Part 3 §2.1, Part 1 §2.4 ``_OPT_`` buttons,
§3 ``alox_manager_panels``). The panel reads the active profile ("Init settings from profile"), shows its keys in
tabs (SETTINGS, DISPLAY, ...), writes them back with APPLY / OK ("apply to profile when Ok/Apply pressed",
``alox_function_ll_get_image_ui_config d``) and is gated by the right ``<Function> Setup`` of ``alox_manager_users``
"User Rights" (§4.3). The functions bundle their parameters from the profile at each start
(``..._bundle_profile_parameters``), so a change made while a function runs is used by its next run.

Here:

- :class:`PanelSpec` describes one panel as the exported front panel shows it (``NNNN alox_function_*_ui_config
  .vip.png``; the controls of the hidden tabs from the terminals of the diagrams, ``...vid.png``): its LabVIEW tabs
  (:class:`Page`), their groups (:class:`Group`) and the controls (:class:`Field`: label, unit, profile key, kind,
  range, step, LabVIEW item texts). Only the controls whose profile key the port reads are described; the others are
  listed in :attr:`PanelSpec.omitted`. :attr:`PanelSpec.readers` are the port's readers of those keys (the params
  class ``from_profile`` or the equivalent bundle helper), used by the tests to check every key.
- :class:`SettingsModel` edits one panel on the active profile (``core.profile_manager``: ``get`` / ``set`` /
  ``save``): values are validated against the field (type, range, items), kept as pending edits, written with
  :meth:`SettingsModel.apply` (the profile in memory: the next run uses them), :meth:`SettingsModel.save` (apply,
  then the recipe archive, profile "Save"), discarded by :meth:`SettingsModel.cancel` (the values are read again).
  Writing needs the panel's right (``allowed``).

Item texts of the enum controls: as the front panels show them where visible ("SHAPE DETECTION", "OUTSIDE TO INSIDE",
"DUAL SPOTS", ...), else the labels of the typedefs as ported (upper case, as the panels show their rings).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from alox.core.users import UserAction

__all__ = ["PANELS", "ApplyResult", "Field", "Group", "Kind", "Page", "PanelSpec", "ProfileAccess", "Selector",
           "SettingsModel", "check", "colour", "colour_text", "num", "panel_by_name", "panel_for", "path", "ring",
           "text"]


# ================================================================================================== description
class Kind(Enum):
    """The LabVIEW control of a field: numeric (I32/U32/DBL), Boolean (switch, check box), ring/enum, path, string,
    colour (a LabVIEW colour box stored as the ``RRGGBB`` text of ``tools_color_to_string``)."""

    NUMBER = "number"
    CHECK = "check"
    ENUM = "enum"
    PATH = "path"
    TEXT = "text"
    COLOUR = "colour"


@dataclass(frozen=True)
class Field:
    """One control: ``key`` is the profile key (``{name}`` is replaced by the panel's :class:`Selector` value).
    Numbers: ``integer`` (I32/U32) or a DBL with ``decimals``; ``minimum``/``maximum`` (inclusive) and ``step``.
    Enums: ``items`` = (code, LabVIEW item text); the codes are integers, or numbers for a ring of values (WAFER
    SIZE: 76.2, 100, 150). Paths: ``directory`` for a folder."""

    label: str
    key: str
    kind: Kind
    default: Any
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1.0
    decimals: int = 0
    items: tuple[tuple[int, str], ...] = ()
    directory: bool = False

    @property
    def integer(self) -> bool:
        return self.kind is Kind.NUMBER and isinstance(self.default, int) and not isinstance(self.default, bool)

    @property
    def caption(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label

    def item_text(self, code: Any) -> str:
        return dict(self.items).get(code, str(code))

    def convert(self, value: Any) -> Any:
        """``value`` in the field's type (no range check); :class:`ValueError` when it has no such meaning."""
        if self.kind is Kind.CHECK:
            if isinstance(value, str):
                text = value.strip().upper()
                if text in ("TRUE", "T", "1", "ON", "YES"):
                    return True
                if text in ("FALSE", "F", "0", "OFF", "NO", ""):
                    return False
                raise ValueError(f"{self.label}: not a Boolean: {value!r}")
            if isinstance(value, (bool, int, float)):
                return bool(value)
            raise ValueError(f"{self.label}: not a Boolean: {value!r}")
        if self.kind in (Kind.PATH, Kind.TEXT):
            if value is None:
                return ""
            return str(value)
        if self.kind is Kind.COLOUR:
            return colour_text(value, self.label)
        if self.kind is Kind.ENUM:
            if isinstance(value, str):
                for code, text in self.items:
                    if value.strip().lower() == text.lower():
                        return code
            number = _number(value, self.label)
            for code, _text in self.items:
                if isinstance(code, float) and number == code:
                    return code
            if number != int(number):
                raise ValueError(f"{self.label}: not an item code: {value!r}")
            return int(number)
        number = _number(value, self.label)
        if self.integer:
            if number != int(number):
                raise ValueError(f"{self.label}: an integer is expected, got {value!r}")
            return int(number)
        return float(number)

    def problem(self, value: Any) -> str | None:
        """Why ``value`` (already converted) is not valid for the field, None when it is."""
        if self.kind is Kind.ENUM and value not in dict(self.items):
            return f"{self.label}: {value!r} is not one of {', '.join(text for _c, text in self.items)}"
        if self.kind is Kind.NUMBER:
            if self.minimum is not None and value < self.minimum:
                return f"{self.caption}: {value:g} is below the minimum {self.minimum:g}"
            if self.maximum is not None and value > self.maximum:
                return f"{self.caption}: {value:g} is above the maximum {self.maximum:g}"
        return None

    def validate(self, value: Any) -> Any:
        """``value`` converted and checked (:class:`ValueError` with the operator message otherwise)."""
        converted = self.convert(value)
        problem = self.problem(converted)
        if problem is not None:
            raise ValueError(problem)
        return converted


def colour_text(value: Any, label: str = "colour") -> str:
    """A colour as ``alox.ini`` stores it (``overlays.fontcolor_STR = "3399FF"``): six upper-case hex digits; also
    accepts ``#3399ff`` and a ``0xRRGGBB`` number (:class:`ValueError` otherwise)."""
    if isinstance(value, bool):
        raise ValueError(f"{label}: not a colour: {value!r}")              # noqa: TRY004 - caught as ValueError
    if isinstance(value, int):
        if not 0 <= value <= 0xFFFFFF:
            raise ValueError(f"{label}: not a colour: {value!r}")
        return f"{value:06X}"
    text = str(value).strip().lstrip("#")
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError(f"{label}: not a colour (RRGGBB): {value!r}")
    return text.upper()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label}: a number is expected, got {value!r}")   # noqa: TRY004 - caught as ValueError
    try:
        number = float(str(value).replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: a number is expected, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{label}: a finite number is expected, got {value!r}")
    return number


@dataclass(frozen=True)
class Group:
    """A decorated group of the front panel (its title as the panel shows it, e.g. ``CAMERA SETTINGS``)."""

    title: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Page:
    """A tab of the panel's Tab Control (``SETTINGS``, ``DISPLAY``, ...)."""

    title: str
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class Selector:
    """A control that chooses which set of keys the panel edits (Get Image: CAMERA → ``get.image.<camera>.*``):
    ``name`` is the placeholder of the keys, ``items`` = (value, LabVIEW item text)."""

    label: str
    name: str
    items: tuple[tuple[str, str], ...]


#: A reader of the profile keys: ``reader(profile, selection)`` builds the function's parameters as it does.
Reader = Callable[[Any, "str | None"], Any]


@dataclass(frozen=True)
class PanelSpec:
    """One settings panel: ``row`` (the window's function row), ``name`` (the panel name of the row, the caption of
    the SETTINGS tab), ``title`` (the panel's title), ``vi``, ``action`` (the right that enables it), its tabs,
    ``common`` (groups under the tabs, shown on every tab), ``selector``, ``readers``, ``overlays`` (image-store
    overlay groups whose keys the panel edits, :data:`alox.core.images.OVERLAY_PROPERTIES`) and ``omitted`` (LabVIEW
    controls left out, with the reason)."""

    row: str
    name: str
    title: str
    vi: str
    action: UserAction
    pages: tuple[Page, ...]
    common: tuple[Group, ...] = ()
    selector: Selector | None = None
    readers: tuple[Reader, ...] = ()
    overlays: tuple[str, ...] = ()
    omitted: tuple[str, ...] = ()

    def groups(self) -> Iterator[Group]:
        for page in self.pages:
            yield from page.groups
        yield from self.common

    def fields(self) -> Iterator[Field]:
        for group in self.groups():
            yield from group.fields

    def selections(self) -> tuple[str | None, ...]:
        return tuple(value for value, _t in self.selector.items) if self.selector else (None,)

    def key(self, f: Field, selection: str | None = None) -> str:
        if self.selector is None:
            return f.key
        return f.key.replace("{" + self.selector.name + "}", selection or self.selector.items[0][0])

    def keys(self, selection: str | None = None) -> list[str]:
        return [self.key(f, selection) for f in self.fields()]


# ================================================================================================== helpers
def num(label: str, key: str, default: float, minimum: float, maximum: float, *, unit: str = "", step: float = 1.0,
        decimals: int | None = None) -> Field:
    """A numeric control: I32 when ``default`` is an ``int``, else DBL (``decimals`` default 2)."""
    if decimals is None:
        decimals = 0 if isinstance(default, int) else 2
    return Field(label, key, Kind.NUMBER, default, unit, minimum, maximum, step, decimals)


def check(label: str, key: str, default: bool) -> Field:
    return Field(label, key, Kind.CHECK, bool(default))


def ring(label: str, key: str, default: int, *texts: str) -> Field:
    return Field(label, key, Kind.ENUM, default, items=tuple(enumerate(texts)))


def path(label: str, key: str, *, directory: bool = False) -> Field:
    return Field(label, key, Kind.PATH, "", directory=directory)


def text(label: str, key: str, default: str = "") -> Field:
    return Field(label, key, Kind.TEXT, default)


def colour(label: str, key: str, default: str) -> Field:
    return Field(label, key, Kind.COLOUR, colour_text(default, label))


def _from_profile(module: str, cls: str) -> Reader:
    def read(profile: Any, _selection: str | None) -> Any:
        import importlib
        return getattr(importlib.import_module(f"alox.functions.{module}"), cls).from_profile(profile)
    read.__qualname__ = f"{cls}.from_profile"
    return read


def _get_image(profile: Any, selection: str | None) -> Any:
    """``GetImage.params_for`` (``alox_function_ll_get_image_bundle_profile_parameters``), on a stand-in function
    whose services hold ``profile``."""
    from alox.functions.get_image import GetImage
    stand_in = SimpleNamespace(services=SimpleNamespace(profile=profile, settings={}))
    return GetImage.params_for(stand_in, 1 if selection == "lowmag" else 0)        # type: ignore[arg-type]


def _autofocus(profile: Any, _selection: str | None) -> Any:
    """``alox_function_ll_autofocus_bundle_profile_parameters`` (:func:`alox.functions.search_object.
    default_autofocus_params`; Z range from the application properties)."""
    from alox.functions.search_object import default_autofocus_params
    return default_autofocus_params(SimpleNamespace(profile=profile, settings={}))   # type: ignore[arg-type]


def _refocus(profile: Any, _selection: str | None) -> Any:
    """``alox_function_ll_refocus_bundle_profile_parameters`` (:func:`alox.functions.search_object.
    default_refocus_params`)."""
    from alox.functions.search_object import default_refocus_params
    return default_refocus_params(SimpleNamespace(profile=profile, settings={}))     # type: ignore[arg-type]


_HL_SETUP = "the SEMI SECS/GEM infos (PROCESS JOB, LOT, WAFER) are indicators of the running job"
_SHOW_UI = "SHOW UI: no profile key read by the port (the run panels are opened by the results panel manager)"


# ================================================================================================== panels
_G = "get.image.{camera}."
GET_IMAGE = PanelSpec(
    "GET_IMAGE", "Get Image", "GET IMAGE", "alox_function_ll_get_image_ui_config.vi", UserAction.GET_IMAGE_SETUP,
    selector=Selector("CAMERA", "camera", (("highmag", "HIGH MAG."), ("lowmag", "LOW MAG."))),
    pages=(
        Page("SETTINGS", (Group("CAMERA SETTINGS", (
            ring("EXPOSURE MODE", _G + "exposuremode", 0, "FIXED", "AUTO", "CURRENT"),
            num("EXPOSURE", _G + "exposure", 1.0, 1.0, 10_000_000.0, unit="µs", step=100.0, decimals=1),
            check("LED", _G + "led", False))),)),
        Page("PROCESSING", (
            Group("OPTIONAL RECTANGLE", (
                num("ROI LEFT", _G + "roileft", 0, 0, 100_000, unit="px"),
                num("ROI TOP", _G + "roitop", 0, 0, 100_000, unit="px"),
                num("ROI WIDTH", _G + "roiwidth", 0, 0, 100_000, unit="px"),
                num("ROI HEIGHT", _G + "roiheight", 0, 0, 100_000, unit="px"))),
            Group("ZOOM / BCG", (
                num("MAGNIFICATION", _G + "subsampling", 1.0, 0.05, 10.0, step=0.05),
                num("BRIGHTNESS", _G + "brightness", 128, 0, 255),
                num("CONTRAST", _G + "contrast", 45, 0, 89),
                num("GAMMA", _G + "gamma", 1.0, 0.01, 10.0, step=0.1),
                num("GAIN", _G + "gain", 1.0, 0.0, 100.0, step=0.1))))),
        Page("SIMULATION", (Group("SIMULATION", (
            check("SIMULATION", _G + "simulation", False),
            path("SIMULATION FOLDER", _G + "simulationfolder", directory=True),
            num("SIMUL. TIMER", _G + "simulationtimer", 0, 0, 3_600_000, unit="ms", step=100))),)),
        Page("DISPLAY", (Group("OVERLAYS", (
            check("OVERLAY NAME", _G + "overlayname", False),
            check("OVERLAY TYPE", _G + "overlaytype", False),
            check("OVERLAY TIMESTAMP", _G + "overlaytime", False),
            check("OVERLAY INDEX", _G + "overlayindex", False),
            check("OVERLAY HW INFOS", _G + "overlayhwinfos", False),
            check("OVERLAY SCALE", _G + "overlayscale", False))),)),
    ),
    readers=(_get_image,),
    omitted=("EXPOSURE TIMES TABLE: the wavelength/exposure table editor (alox_wl_auto_exposures_table_ui), not ported",
             "CURRENT IMAGE HISTOGRAM, State: indicators",
             "AUTO EXPO.: no profile key read by the port"))

AUTOFOCUS = PanelSpec(
    "AF", "Autofocus", "AUTOFOCUS", "alox_function_ll_autofocus_ui_config.vi", UserAction.AUTOFOCUS_SETUP,
    pages=(
        Page("SETTINGS", (Group("AUTOFOCUS", (
            num("DURATION", "autofocus.moveduration", 10, 0, 3600, unit="S"),
            ring("METHOD", "autofocus.method", 0, "FFT", "SOBEL", "PREWITT", "DIFF", "GRADIENT", "SIGMA", "FULL"),
            num("SUBSAMPLING", "autofocus.subsampling", 0.25, 0.01, 1.0, step=0.05))),)),
        Page("DISPLAY", (Group("OVERLAYS", (
            check("OVERLAY SCORE", "autofocus.overlayscore", True),
            check("OVERLAY POS", "autofocus.overlayposition", True))),)),
    ),
    readers=(_autofocus,), overlays=("autofocus",),
    omitted=(("FOCUS DETECT METHOD, FIT METHOD, FIT ALGORITHM, FIT. ORDER (TROUBLESHOOT: analysis of the last AF "
              "log in the panel), not read by the port"),
             ("POSITIONS: ZMIN / ZMAX are the application properties hardware.z.zminautofocus / zmaxautofocus "
              "(XYZ LIMITS, properties UI)"),
             "CAMERA: set by the start button (Low Mag.)", _SHOW_UI,
             "autofocus.speed and autofocus.step: read by the port, no control in the LabVIEW panel"))

REFOCUS = PanelSpec(
    "AF_FINE", "Refocus", "REFOCUS", "alox_function_ll_refocus_ui_config.vi", UserAction.REFOCUS_SETUP,
    pages=(
        Page("SETTINGS", (
            Group("REFOCUS", (
                num("STEPS", "refocus.nbsteps", 4, 1, 100),
                num("WAIT", "refocus.waitms", 0, 0, 60_000, unit="ms", step=10),
                num("Z STEP", "refocus.step", 0.05, 0.0001, 1.0, unit="mm", step=0.001, decimals=4),
                ring("METHOD HIGH MAG", "refocus.method", 0, "FFT", "SOBEL", "PREWITT", "DIFF", "GRADIENT",
                     "SIGMA", "FULL"))),
            Group("ROI", (
                num("HEIGHT", "refocus.roiheight", 128, 1, 10_000, unit="px"),
                num("WIDTH", "refocus.roiwidth", 128, 1, 10_000, unit="px"))))),
        Page("DISPLAY", (Group("OVERLAYS", (
            check("OVERLAY SCORE", "refocus.overlayscore", True),
            check("OVERLAY POSITION", "refocus.overlayposition", True),
            check("OVERLAY ROI", "refocus.overlayroi", True))),)),
    ),
    readers=(_refocus,), overlays=("refocus",),
    omitted=("OFFSET Z (mm): refocus.offset, no field in the camera-server refocus (not read by the port)",
             "CAMERA: set by the start button", _SHOW_UI))

_DC = "detect.vcsel."
DETECT_CIRCLE = PanelSpec(
    "DET_CIRCLE", "Detect Circle", "DETECT CIRCLE", "alox_function_ll_detect_circle_ui_config.vi",
    UserAction.DETECT_CIRCLE_SETUP,
    pages=(
        Page("SETTINGS", (
            Group("GENERAL", (ring("METHOD", _DC + "method", 0, "SHAPE DETECTION", "OBJ LEARNING"),)),
            Group("CIRCLE SETTINGS", (
                ring("BEST CIRCLE METHOD", _DC + "bestcirclemethod", 0, "CENTERED", "SCORE", "LARGEST"),
                num("RADIUS MIN", _DC + "minradius", 20.0, 0.0, 10_000.0, unit="µm"),
                num("RADIUS MAX", _DC + "maxradius", 50.0, 0.1, 10_000.0, unit="µm"))),
            Group("IMAGE PRE-PROCESSINGS", (
                ring("DOWNSAMPLING", _DC + "downsamplemethod", 0, "NONE", "25%", "50%", "75%", "90%"),
                num("EXTEND DARK", _DC + "morphopen", 0, 0, 99),
                num("EXTEND BRIGHT", _DC + "morphclose", 0, 0, 99),
                num("DILATE", _DC + "dilate", 0, 0, 99),
                num("ERODE", _DC + "erode", 0, 0, 99))))),
        Page("SHAPE", (
            Group("EDGES", (
                num("EDGE THRESHOLD", _DC + "edgethreshold", 15, 0, 255),
                num("MIN LENGTH", _DC + "edgeminlength", 15, 0, 10_000, unit="px"),
                num("MIN SCORE", _DC + "edgeminscore", 70.0, 0.0, 100.0, unit="%"),
                check("OCCLUSION", _DC + "edgeocclusion", True),
                check("EXTEND RESEARCH", _DC + "extendshape", True))),
            Group("SHAPES", (
                check("CIRCLES", _DC + "circles", True),
                check("ELLIPSES", _DC + "ellipses", True),
                check("RECTANGLES", _DC + "rectangles", True))))),
        Page("IMAGE LEARNING", (Group("OBJECT LEARNING", (
            path("MODEL PATH", _DC + "modelpath"),
            path("MASK PATH", _DC + "maskpath"),
            num("MIN SCORE", _DC + "scoreresult", 75.0, 0.0, 100.0, unit="%"),
            num("MAX OCCLUSION", _DC + "occlusionresult", 30.0, 0.0, 100.0, unit="%"))),)),
        Page("DISPLAY", (Group("OVERLAYS", (
            check("OVERLAY BEST CIRCLE", _DC + "overlaybestcircle", False),
            check("OVERLAY ALL CIRCLES", _DC + "overlayallcircles", False),
            check("OVERLAY OBJECTS", _DC + "overlayobjects", False))),)),
    ),
    readers=(_from_profile("detect_circle", "DetectCircleParams"),),
    omitted=("DISPLAY IMAGE (Source / Pre-Processed / Binarized / Filtered): panel preview only, no profile key",
             ("METHOD BLOBS and the BINARY OBJ. page (detect.vcsel.binarymethod, objecttype, detectkernel, "
              "binaryinvert, removesmalls, removesmallscount, removeborder, fillhole, mincircfactor, maxcircfactor): "
              "Blobs is not used anymore (owner R8), a recipe with method 2 runs Shape"),
             "TEMPLATE / LEARN MASK / TEMP IMAGE: the learning tools of the panel, not ported",
             ("detect.vcsel.dp, mindist, param1, param2 (Hough 'MRQT' values): read by the port, edited only by "
              "the old copy alox_function_ll_detect_circle_ui_config_py.vi (unused in V1.2.6.2)")))

_MV, _MO = "measure.vcsel.", "measure.oxyd."
_DIRECTIONS = ("OUTSIDE TO INSIDE", "INSIDE TO OUTSIDE")
_POLARITIES = ("ALL", "RISING EDGES", "FALLING EDGES")


def _edge_group(title: str, prefix: str, direction: int) -> Group:
    return Group(title, (
        ring("SEARCH DIRECTION", prefix + "direction", direction, *_DIRECTIONS),
        ring("EDGE POLARITY", prefix + "polarity", 0, *_POLARITIES),
        num("STEP", prefix + "stepsize", 5.0, 0.1, 100.0, unit="px", step=0.5),
        num("WIDTH", prefix + "width", 3, 1, 99, unit="px"),
        num("STRENGTH", prefix + "minstrength", 10.0, 0.0, 255.0),
        num("KERNEL", prefix + "kernelsize", 3, 1, 99)))


MEASURE_OXYD = PanelSpec(
    "MES_VCSEL", "Measure Oxyd", "MEASURE MESA/OXYD", "alox_function_ll_measure_mesa_ui_config.vi",
    UserAction.MEASURE_OXYD_SETUP,
    pages=(
        Page("SETTINGS MESA", (
            _edge_group("MEASURE MESA", _MV, 0),
            Group("MESA DIAMETER", (
                num("MIN MESA Diam.", _MV + "minMesaDiam", 5.0, 0.0, 10_000.0, unit="µm"),
                num("MAX MESA Diam.", _MV + "maxMesaDiam", 40.0, 0.0, 10_000.0, unit="µm"))))),
        Page("SETTINGS OXID", (
            _edge_group("MEASURE OXYD", _MO, 1),
            Group("OXID DIAMETER", (
                num("MAX OXID Diam.", _MO + "oxydmax", 50.0, 0.0, 10_000.0, unit="µm"),
                num("Result OFFSET", _MO + "resultoffset", 0.0, -1_000.0, 1_000.0, unit="µm", step=0.1))))),
        Page("FILTERING OXID", (Group("FILTERING", (
            # LabVIEW's six types, then the Python-only DEPTH (Line mesas), HEIGHT Y and WIDTH X (Rectangle mesas;
            # owner S2 / S3, alox.vision.stats.OxideMeasureType); the Mesa job measures with this type too
            ring("DIAM. TYPE", _MO + "measuretype", 0, "MEAN", "MEDIAN", "FIT", "SURFACE", "CIRCUMSCRIBED",
                 "INSCRIBED", "DEPTH", "HEIGHT Y", "WIDTH X"),
            num("OXYD TARGET", _MO + "oxydtarget", 25.0, 0.0, 10_000.0, unit="µm"),
            num("FIT MAX DIST", _MO + "fitmaxdistance", 3, 0, 1_000, unit="px"),
            num("FIT MIN SCORE", _MO + "fitminscore", 500.0, 0.0, 100_000.0, step=10.0),
            num("FIT MAX ITER", _MO + "fitmaxiterations", 500, 1, 100_000, step=10),
            check("REJECT OUTLIERS", _MO + "rejectoutliers", True))),)),
        Page("DISPLAY", (
            Group("MESA OVERLAYS", (
                check("MESA MEASURED", _MO + "overlaymesa", True),
                check("OVERLAY MESA SEARCH LINES", _MV + "mesaSearchLines", True),
                check("OVERLAY MESA EDGES", _MV + "overlaymesaedges", True))),
            Group("OXID OVERLAYS", (
                check("SEARCH LINES", _MO + "overlaydiameters", True),
                check("OVERLAY OXYD", _MO + "overlayoxyd", True),
                check("OVERLAY TARGET", _MO + "overlaytarget", True),
                check("OVERLAY INLIERS", _MO + "overlaypoints", True),
                check("OVERLAY RESULTS MEAS", _MO + "overlaymeasure", True))))),
    ),
    common=(Group("MEASURES", (
        check("Meas Mesa", _MV + "MeasMesaMandatory", True),
        check("Meas Oxid", _MO + "MeasOxydMandatory", True),
        ring("Mesa type", _MV + "mesashapetype", 0, "CIRCULAR", "LINE", "RECTANGLE"))),),
    readers=(_from_profile("measure_oxyd", "MeasureOxydParams"),),
    omitted=("SEARCH ANGLE, OXYD MIN DIAM (um): no profile key read by the port",
             "Mesa type LINE: the SEARCH DIRECTION rings do not apply (the rake direction comes from the edge found)",
             "EDGE STRENGTH graph, Edge Information, FOUND OXID: indicators of the panel's test measure"))

TRACKING = PanelSpec(
    "TRACK", "Tracking", "TRACKING", "alox_function_ll_tracking_ui_config.vi", UserAction.TRACKING_SETUP,
    pages=(
        Page("SETTINGS", (Group("TRACKING SETTINGS", (
            num("TRACKING ZONE DIAMETER", "tracking.diameter", 0.2, 0.01, 100_000.0, unit="µm", step=0.1),)),)),
        Page("DISPLAY", (Group("OVERLAYS", (check("OVERLAY TRACKING ZONE", "tracking.overlayzone", False),)),)),
    ),
    readers=(_from_profile("tracking", "TrackingParams"),),
    omitted=("RUN: a test run of the function from the panel",))

AUTO_EXPOSURE = PanelSpec(
    "AUTO_EXPO", "Auto Exposure", "AUTO EXPOSURE", "alox_function_ll_auto_exposure_ui_config.vi",
    UserAction.AUTO_EXPOSURE_SETUP,
    pages=(
        Page("SETTINGS", (
            Group("AUTO EXPOSURE SETTINGS", (num("TARGET BRIGHTNESS", "autoexpo.targetbrightness", 128, 0, 255),)),
            Group("ROI MODE", (
                num("MESA DIAMETER ROI", "autoexpo.roidiameter", 100.0, 0.1, 10_000.0, unit="µm"),
                check("DETECT CIRCLE (OFF: ROI at the image center)", "autoexpo.bypassCircleIn", False))),
            Group("EXPOSURE RANGE", (
                num("EXPO MIN", "autoexpo.exposurelow", 5000.0, 1.0, 10_000_000.0, unit="µs", step=100.0,
                    decimals=1),
                num("EXPO MAX", "autoexpo.exposurehigh", 15000.0, 1.0, 10_000_000.0, unit="µs", step=100.0,
                    decimals=1))))),
        Page("DISPLAY", (Group("DISPLAY", (
            check("SHOW WINDOW", "autoexpo.showwindow", False),
            check("OVERLAY ROI", "autoexpo.overlayroi", False),
            check("OVERLAY RESULT", "autoexpo.overlayresults", False))),)),
    ),
    readers=(_from_profile("auto_exposure", "AutoExposureParams"),),
    omitted=("MODE (autoexpo.mode): set by the start buttons (Curr. = Current Wavelength, Full = All Wavelengths)",
             "EXPOSURE TIMES TABLE: the wavelength/exposure table editor, not ported",
             "FULL ANALYSIS RANGE START WL / END WL: indicators (set in the WAVELENGTH panel)"))

_WL = "wavelength."
SEARCH_WAVELENGTH = PanelSpec(
    "WL", "Search Wavelength", "WAVELENGTH", "alox_function_ll_search_wavelength_ui_config.vi",
    UserAction.SEARCH_WAVELENGTH_SETUP,
    pages=(
        Page("SETTINGS", (
            Group("SEARCH SETTINGS", (
                num("START WL", _WL + "start", 700.0, 300.0, 2_000.0, unit="nm"),
                num("END WL", _WL + "end", 950.0, 300.0, 2_000.0, unit="nm"),
                num("STEP WL", _WL + "step", 2.0, 0.1, 1_000.0, unit="nm"),
                num("MIN CONTRAST", _WL + "contrast", 20.0, -255.0, 255.0),
                num("STEP REFOCUS", _WL + "steprefocus", 0, 0, 1_000, unit="nm"),
                num("WL SHIFT", _WL + "wlshift", 0, -1_000, 1_000, unit="nm"))),
            Group("MANUAL WL", (num("MANUAL WL", _WL + "wlmanual", 0.0, 0.0, 2_000.0, unit="nm"),)),
            Group("SEARCH REGIONS OF INTERESTS", (
                ring("REGIONS TYPE", _WL + "spotautoroitype", 0, "DUAL SPOTS", "SPOT-ANNULAR"),
                ring("CENTERING METHOD", _WL + "detectcirclemethod", 0, "SINGLE (AT START WL)",
                     "FULL (AT EVERY WL)"),
                num("SPOTS SIZE", _WL + "spotsize", 2.0, 0.1, 10_000.0, unit="µm"),
                num("DISTANCE", _WL + "distance", 5.0, 0.0, 10_000.0, unit="µm"),
                num("ANGLE", _WL + "angle", 45.0, -360.0, 360.0, unit="°"),
                num("SPOT SIZE", _WL + "annulspotsize", 2.0, 0.1, 10_000.0, unit="µm"),
                num("ANNUL. DIAM.1", _WL + "annuldiameter1", 20.0, 0.0, 10_000.0, unit="µm"),
                num("ANNUL. DIAM.2", _WL + "annuldiameter2", 30.0, 0.0, 10_000.0, unit="µm"))))),
        Page("DISPLAY", (Group("DISPLAY", (
            check("SHOW UI", _WL + "showwindow", False),
            check("OVERLAY ZONES", _WL + "overlayzones", False),
            check("OVERLAY RESULTS", _WL + "overlayresults", False),
            check("OVERLAY MESA MEASUREMENT", _WL + "overlayoxidmeas", False))),)),
    ),
    readers=(_from_profile("search_wavelength", "SearchWavelengthParams"),),
    omitted=("WAVELENGTH MODE (wavelength.wlmode): set by the start buttons (Manu / Search)",
             ("CURRENT WL, SET WL 0nm, SET MANU WL: monochromator commands of the panel (SET WAVELENGTH of the "
              "main window)"), "MANUAL SCAN: the panel's manual scan tool, not ported",
             "EXPOSURE TIMES TABLE: the wavelength/exposure table editor, not ported"))

_SO = "search.object."
SEARCH_OBJECT = PanelSpec(
    "SEARCH_OBJ", "Search Object", "SEARCH OBJECT", "alox_function_ll_detect_object_ui_config.vi",
    UserAction.SEARCH_OBJECT_SETUP,
    pages=(
        Page("SETTINGS", (
            Group("SEARCH OBJECT SETTINGS", (
                check("AUTOFOCUS", _SO + "autofocus", True),
                check("REFOCUS", _SO + "refocus", True),
                num("MAX STEPS", _SO + "maxmoves", 15, 0, 1_000),
                num("STEP", _SO + "step", 0.1, 0.0, 100.0, unit="mm", step=0.01, decimals=3))),
            Group("MATCH OBJECT SETTINGS", (
                path("MODEL PATH", _SO + "searchmodel"),
                path("MASK PATH", _SO + "learnmask"),
                num("EDGE THRESHOLD", _SO + "edgethreshold", 25, 0, 255),
                num("MIN LENGTH", _SO + "minlength", 5, 0, 10_000),
                num("MIN SCORE", _SO + "minscore", 70.0, 0.0, 100.0, unit="%"),
                num("MAX OCCLUSION", _SO + "maxocclusionpc", 50.0, 0.0, 100.0, unit="%"),
                num("MORPH. OPEN", _SO + "morphopen", 0, 0, 99),
                num("MORPH. CLOSE", _SO + "morphclose", 0, 0, 99))),
            Group("POSITIONS", (
                num("START X", _SO + "startx", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.1, decimals=3),
                num("START Y", _SO + "starty", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.1, decimals=3),
                num("OFFSET X", _SO + "offsetX", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.01, decimals=3),
                num("OFFSET Y", _SO + "offsetY", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.01, decimals=3))))),
        Page("DISPLAY", (Group("DISPLAY", (
            check("SHOW WINDOW", _SO + "showwindow", True),
            check("OVERLAY MATCH", _SO + "overlaymatch", True),
            check("OVERLAY CONTOURS", _SO + "overlaycontours", True))),)),
    ),
    readers=(_from_profile("search_object", "SearchObjectParams"),),
    omitted=(("NEW / LOAD / EDIT / SEARCH OBJECT: the model learning tools of the panel, not ported (MODEL PATH "
              "and MASK PATH are edited as paths)"), "OBJECT LEARNED: indicator"))

_MM = "measure.mesa."
MESA = PanelSpec(
    "MES_MESA", "Mesa Measurement", "MESA MEASUREMENT", "alox_function_hl_mesa_measurement_ui_config.vi",
    UserAction.MESA_SETUP,
    pages=(
        Page("SETTINGS", (
            Group("MESA MEASUREMENT", (
                num("REFOCUS TIME", _MM + "refocustime", 15.0, 0.0, 3_600.0, unit="s"),
                num("REFOCUS MAX RETRIES", _MM + "refocusretries", 3, 0, 100),
                num("# AVERAGES", _MM + "averages", 1, 1, 1_000),
                check("TRACKING", _MM + "tracking", False),
                num("OXYD TARGET", _MM + "oxydtarget", 0.0, 0.0, 10_000.0, unit="µm"),
                num("LINE REGRESSION", _MM + "regressiontime", 0.0, 0.0, 36_000.0, unit="s"))),
            Group("RETRIES", (
                ring("WL MODE", _MM + "wlmodetab", 1, "MANUAL", "SCAN"),
                ring("EXPOSURE MODE", _MM + "exposuremode", 1, "CALIB EXPOSURE", "AUTO EXPOSURE"))),
            Group("START POSITION", (
                num("START X", _MM + "startx", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.1, decimals=3),
                num("START Y", _MM + "starty", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.1, decimals=3))))),
        Page("DISPLAY", (Group("DISPLAY", (
            check("SHOW WINDOW", _MM + "showwindow", False),
            check("OVERLAY OXYD DIAM", _MM + "overlaydiameter", False),
            check("OVERLAY OXYD TIME", _MM + "overlaytime", False),
            check("OVERLAY OXYD STATUS", _MM + "overlaystatus", False),
            check("OVERLAY OXYD RATE", _MM + "overlayrate", False))),)),
    ),
    readers=(_from_profile("mesa", "MesaParams"),),
    omitted=(_HL_SETUP, "WL DONE, AUTOEXPO DONE: indicators; RETRY WL, RETRY AUTOEXPO: no profile key read",
             ("TILT CORRECT, POSITIONS CONTROLLED, ANALYSIS, SINGLESHOT (measure.mesa.tiltcorrection, "
              "positionsctrl, analysis, singleshot): set by the start buttons and the REST request, not by the panel"),
             "measure.oxyd.oxydtarget: edited in the Measure Oxyd panel (FILTERING OXID)"))

_MB = "measure.bowing."
BOWING = PanelSpec(
    "MES_BOW", "Bowing Measurement", "BOWING MEASUREMENT", "alox_function_hl_bowing_measurement_ui_config.vi",
    UserAction.BOWING_SETUP,
    pages=(
        Page("SETTINGS", (Group("BOWING MEASUREMENT", (
            num("BOWING RADIUS", _MB + "distance", 0.0, 0.0, 1_000.0, unit="mm"),
            num("MAX. BOWING", _MB + "maxbowing", 0.0, -10_000.0, 10_000.0, unit="µm"),
            ring("CAMERA", _MB + "camera", 1, "HIGH MAG.", "LOW MAG."),
            check("FAST METHOD", _MB + "methodFast", False))),)),
        Page("DISPLAY", (Group("DISPLAY", (
            check("SHOW WINDOW", _MB + "showwindow", False),
            check("OVERLAY BOWING", _MB + "overlaybowing", False),
            check("OVERLAY PARALL", _MB + "overlayparallelism", False))),)),
    ),
    readers=(_from_profile("bowing", "BowingParams"),),
    omitted=(_HL_SETUP, "Zwafer1..5, Zchuck1..5, BOWING A/B, PARALL A/B: results (RESULTS tab)"))

_MT = "measure.tilt."
TILT = PanelSpec(
    "MES_TILT", "Tilt Measurement", "TILT MEASUREMENT", "alox_function_hl_tilt_measurement_ui_config.vi",
    UserAction.TILT_SETUP,
    pages=(
        Page("GENERAL", (
            Group("TILT MEASUREMENT", (
                ring("TILT MEASURE METHOD", _MT + "method", 0, "STRAIGHT EDGE", "DUAL OBJECTS", "LONG BASELINE EDGE",
                     "LATTICE"),
                num("MAX STEPS", _MT + "maxmoves", 0, 0, 1_000),
                num("STEP", _MT + "step", 0.1, 0.0, 100.0, unit="mm", step=0.01, decimals=3),
                check("AUTOFOCUS", _MT + "autofocus", True),
                check("REFOCUS", _MT + "refocus", True),
                num("START X WAFER", _MT + "startx", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.1, decimals=3),
                num("START Y WAFER", _MT + "starty", 0.0, -1_000.0, 1_000.0, unit="mm", step=0.1, decimals=3),
                ring("CAMERA", _MT + "camera", 1, "HIGH MAG.", "LOW MAG."))),
            Group("TILT ANGLE TOLERANCES", (
                num("EXPECTED ANGLE", _MT + "expectedangledeg", 0.0, -180.0, 180.0, unit="°"),
                num("ANGLE TOLERANCE", _MT + "anglerangedeg", 10.0, 0.0, 180.0, unit="±°"))))),
        Page("STRAIGHT EDGE", (Group("STRAIGHT EDGE DETECTION", (
            ring("SEARCH DIR", _MT + "edgesearchdir", 0, "LEFT TO RIGHT", "RIGHT TO LEFT", "TOP TO BOTTOM",
                 "BOTTOM TO TOP"),
            ring("EDGE POL", _MT + "edgepolarity", 0, *_POLARITIES),
            ring("FIT LINE", _MT + "edgemethod", 0, "FIRST EDGE RAKE", "BEST EDGE RAKE", "HOUGH EDGE RAKE",
                 "FIRST EDGE PROJECTION", "BEST EDGE PROJECTION"),
            num("EDGE STRENGTH", _MT + "edgestrength", 20, 0, 255),
            num("KERNEL SIZE", _MT + "edgekernel", 3, 1, 99),
            num("WIDTH", _MT + "edgewidth", 10, 1, 1_000, unit="PIXELS"),
            num("STEP", _MT + "edgestep", 30, 1, 1_000, unit="PIXELS"),
            num("MIN POINTS", _MT + "minpointspc", 25, 0, 100, unit="%"))),)),
        Page("DUAL OBJECTS", (
            Group("TEMPLATE1", (
                path("OBJ1 MODEL PATH", _MT + "obj1model"),
                path("OBJ1 MASK PATH", _MT + "obj1mask"),
                num("OBJ1 MIN SCORE", _MT + "obj1minscore", 70.0, 0.0, 100.0, unit="%"),
                num("OBJ1 MAX OCC", _MT + "obj1maxocclusionpc", 40.0, 0.0, 100.0, unit="%"))),
            Group("TEMPLATE2", (
                path("OBJ2 MODEL PATH", _MT + "obj2model"),
                path("OBJ2 MASK PATH", _MT + "obj2mask"),
                num("OBJ2 MIN SCORE", _MT + "obj2minscore", 70.0, 0.0, 100.0, unit="%"),
                num("OBJ2 MAX OCC", _MT + "obj2maxocclusionpc", 40.0, 0.0, 100.0, unit="%"))),
            Group("MATCHING", (
                num("OBJ THRESHOLD", _MT + "edgethreshold", 25, 0, 255),
                num("OBJ MIN LENGTH", _MT + "minlength", 5, 0, 10_000),
                num("OBJ OPEN", _MT + "morphopen", 0, 0, 99),
                num("OBJ CLOSE", _MT + "morphclose", 0, 0, 99))))),
        Page("LONG BASELINE", (Group("LONG BASELINE EDGE / LATTICE", (
            num("BASELINE LENGTH", _MT + "lblength", 20.0, 0.1, 300.0, unit="mm", step=1.0, decimals=2),
            num("FIRST STEP (0 = AUTO)", _MT + "lbfirststep", 0.0, 0.0, 150.0, unit="mm", step=0.1, decimals=3),
            num("STEP GROWTH", _MT + "lbgrowth", 8.0, 1.1, 100.0, step=0.5, decimals=1),
            num("CONSISTENCY", _MT + "lbtolerancedeg", 0.5, 0.01, 45.0, unit="±°", step=0.1, decimals=2),
            num("MESA PITCH (0 = MEASURED)", _MT + "latticepitchum", 0.0, 0.0, 10_000.0, unit="µm", step=1.0,
                decimals=2),
            # the angle of the grid's densest row against the wafer X axis by design (owner S6: any orientation)
            num("GRID ROW ANGLE", _MT + "latticeangledeg", 0.0, -180.0, 180.0, unit="°", step=0.5, decimals=3),
            check("REFOCUS AT EACH POSITION", _MT + "lbrefocus", False))),)),
        Page("DISPLAY", (Group("DISPLAY", (
            check("SHOW WINDOW", _MT + "showwindow", True),
            check("OVERLAY MATCHES", _MT + "overlaymatches", True),
            check("OVERLAY TILT", _MT + "overlaytilt", True),
            check("OVERLAY EDGES", _MT + "overlayedges", True),
            check("OVERLAY LINE", _MT + "overlaystraightedge", True))),)),
    ),
    readers=(_from_profile("tilt", "TiltParams"),),
    omitted=(_HL_SETUP, ("the learning tools of DUAL OBJECTS (TEMPLATE1/2 learn, OBJECT #1/#2 LEARNED/FOUND), "
                         "not ported (the model and mask files are edited as paths)"), "RESULTS: indicators"))

#: The panels, by function row of the window (``alox.ui.controller.ROWS``), in the order of the panel's rows.
PANELS: dict[str, PanelSpec] = {spec.row: spec for spec in (
    GET_IMAGE, SEARCH_OBJECT, AUTOFOCUS, REFOCUS, DETECT_CIRCLE, TRACKING, AUTO_EXPOSURE, SEARCH_WAVELENGTH,
    MEASURE_OXYD, BOWING, TILT, MESA)}


def panel_for(row: str) -> PanelSpec | None:
    """The panel of a function row (``GET_IMAGE``, ``AF``, ...), None when it has none."""
    return PANELS.get(row)


def panel_by_name(name: str) -> PanelSpec | None:
    """The panel whose :attr:`PanelSpec.name` is ``name`` (the panel name of a row: ``"Get Image"``)."""
    return next((spec for spec in PANELS.values() if spec.name == name), None)


# ================================================================================================== editing
class ProfileAccess(Protocol):
    """The active profile as the panels use it: ``core.profile_manager`` satisfies it."""

    def get(self, dotted: str, default: Any = None) -> Any: ...

    def set(self, dotted: str, value: Any) -> None: ...

    def save(self) -> Path: ...


@dataclass(frozen=True)
class ApplyResult:
    """What :meth:`SettingsModel.apply` / :meth:`~SettingsModel.save` did: the keys written, the errors by key (a
    refused right or a failed save under ``""``) and the archive written by a save."""

    written: tuple[str, ...] = ()
    errors: Mapping[str, str] = field(default_factory=dict)
    archive: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


class SettingsModel:
    """The values of one panel on the active profile (module docstring). ``allowed()``: the right of the current
    user (re-read at each write). Not thread-safe: used by the UI thread."""

    def __init__(self, spec: PanelSpec, profile: ProfileAccess, *, allowed: Callable[[], bool] = lambda: True,
                 selection: str | None = None) -> None:
        self.spec = spec
        self.profile = profile
        self._allowed = allowed
        selections = spec.selections()
        self.selection = selection if selection in selections else selections[0]
        self._fields = {f.key: f for f in spec.fields()}
        self._loaded: dict[str, Any] = {}         # resolved key → value read from the profile (converted)
        self._missing: set[str] = set()           # keys absent from the profile (shown with the field default)
        self._pending: dict[str, Any] = {}        # resolved key → edited value (validated)
        self.load_error: str | None = None
        self.reload()

    # -- reading
    @property
    def allowed(self) -> bool:
        try:
            return bool(self._allowed())
        except Exception:                                         # noqa: BLE001 - a failing check refuses
            return False

    @property
    def editable(self) -> bool:
        return self.load_error is None and self.allowed

    def entries(self) -> list[tuple[Field, str]]:
        """The fields with their resolved key, for the current selection."""
        return [(f, self.spec.key(f, self.selection)) for f in self.spec.fields()]

    def field(self, key: str) -> Field:
        """The field of a key template or of a resolved key."""
        found = self._fields.get(key)
        if found is not None:
            return found
        for f, resolved in self._all_entries():
            if resolved == key:
                return f
        raise KeyError(key)

    def _all_entries(self) -> Iterator[tuple[Field, str]]:
        for selection in self.spec.selections():
            for f in self.spec.fields():
                yield f, self.spec.key(f, selection)

    def _resolve(self, key: str) -> tuple[Field, str]:
        if key in self._fields:
            f = self._fields[key]
            return f, self.spec.key(f, self.selection)
        return self.field(key), key

    def value(self, key: str) -> Any:
        """The value shown for a field (template or resolved key): the pending edit, else the profile's."""
        f, resolved = self._resolve(key)
        if resolved in self._pending:
            return self._pending[resolved]
        if resolved not in self._loaded:
            self._read(f, resolved)
        return self._loaded[resolved]

    def missing(self, key: str) -> bool:
        """True when the profile has no such key (the field shows its default)."""
        _f, resolved = self._resolve(key)
        self.value(key)
        return resolved in self._missing

    def _read(self, f: Field, resolved: str) -> None:
        try:
            raw = self.profile.get(resolved, None)
        except (RuntimeError, LookupError, TypeError, ValueError) as exc:       # no profile loaded
            self.load_error = f"profile not readable: {exc}"
            raw = None
        if raw is None:
            self._missing.add(resolved)
            self._loaded[resolved] = f.default
            return
        self._missing.discard(resolved)
        try:
            self._loaded[resolved] = f.convert(raw)
        except ValueError:
            self._loaded[resolved] = f.default
            self._missing.add(resolved)

    def reload(self) -> None:
        """Read every value again from the profile (the selections read so far); pending edits are dropped."""
        self.load_error = None
        self._pending.clear()
        known = set(self._loaded)
        self._loaded.clear()
        self._missing.clear()
        for f, resolved in self.entries():
            self._read(f, resolved)
        for f, resolved in self._all_entries():
            if resolved in known and resolved not in self._loaded:
                self._read(f, resolved)

    def select(self, selection: str) -> None:
        """Choose the set of keys shown (Get Image: the camera); pending edits of the others are kept."""
        if selection not in self.spec.selections():
            raise ValueError(f"{self.spec.name}: unknown selection {selection!r}")
        self.selection = selection
        for f, resolved in self.entries():
            if resolved not in self._loaded:
                self._read(f, resolved)

    def problems(self) -> list[str]:
        """The profile values of the current selection that the fields refuse (out of range, unknown item)."""
        found = []
        for f, resolved in self.entries():
            if resolved in self._pending:
                continue
            problem = f.problem(self.value(resolved))
            if problem is not None:
                found.append(f"{problem} (profile {resolved})")
        return found

    # -- editing
    def set(self, key: str, value: Any) -> str | None:
        """Edit a field (template or resolved key); returns the error message (the edit is then not kept) or None.
        A value equal to the profile's cancels the edit."""
        f, resolved = self._resolve(key)
        try:
            checked = f.validate(value)
        except ValueError as exc:
            return str(exc)
        current = self._loaded.get(resolved)
        if resolved not in self._loaded:
            self._read(f, resolved)
            current = self._loaded[resolved]
        if checked == current and resolved not in self._missing and type(checked) is type(current):
            self._pending.pop(resolved, None)
        else:
            self._pending[resolved] = checked
        return None

    @property
    def dirty(self) -> bool:
        return bool(self._pending)

    def changes(self) -> dict[str, Any]:
        """The pending edits, by resolved key."""
        return dict(self._pending)

    def cancel(self) -> None:
        """CANCEL: drop the pending edits and read the values again from the profile."""
        self.reload()

    def apply(self) -> ApplyResult:
        """APPLY: write the pending edits into the active profile (used by the next run of the function). Refused
        without the right; a value the profile refuses (type of an existing key) stays pending with its error."""
        if not self.allowed:
            return ApplyResult(errors={"": f"{self.spec.name}: setup not allowed for this user"})
        written: list[str] = []
        errors: dict[str, str] = {}
        for resolved, value in list(self._pending.items()):
            try:
                self.profile.set(resolved, value)
            except (RuntimeError, LookupError, TypeError, ValueError) as exc:
                errors[resolved] = f"{self.field(resolved).caption}: {exc}"
                continue
            self._loaded[resolved] = value
            self._missing.discard(resolved)
            del self._pending[resolved]
            written.append(resolved)
        return ApplyResult(tuple(written), errors)

    def save(self) -> ApplyResult:
        """SAVE: :meth:`apply`, then the recipe (profile "Save": ``<profiles>/<name>.zip``)."""
        applied = self.apply()
        if not applied.ok:
            return applied
        try:
            archive = self.profile.save()
        except (RuntimeError, OSError, ValueError) as exc:
            return ApplyResult(applied.written, {"": f"profile not saved: {exc}"})
        return ApplyResult(applied.written, {}, archive)
