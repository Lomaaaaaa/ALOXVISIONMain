"""The look of the operator window: a light theme (default) and a dark theme (a darkened room near the microscope).

A :class:`Theme` is a set of palette tokens (background, surface, text, muted text, border, accent, the image
background, and a :class:`Badge` for each function status); :func:`style_sheet` turns it into the one Qt style sheet
of the application and :func:`palette` into the matching :class:`QPalette` (dialogs, menus and the Fusion style's
own drawings follow it). :func:`apply` installs a theme at run time; :func:`saved` / :func:`save` remember the
operator's choice in the user settings (``QSettings``, key ``ui/theme``).

The status badges keep LabVIEW's meanings and texts (``alox.ui.controller.STATUS_TEXTS``): OFF orange, OFF OK neutral,
RUNNING amber, FINISHED green, ERROR red; the shades are chosen for a text contrast of at least 4.5:1 (WCAG AA,
:func:`contrast`) in both themes.

Widgets pick their style through dynamic properties matched by the style sheet:

- ``QFrame[card="true"]``: a panel (rounded, on the surface colour);
- ``QLabel[role=...]``: ``section`` (group titles), ``name`` (function names), ``brand``, ``title`` (settings panel
  title), ``field`` (input captions), ``muted`` (notes); ``QLabel[tone="error"]`` an error message;
- ``QLabel[status=...]``: a status badge (``off``, ``off-ok``, ``running``, ``finished``, ``error``);
- ``QPushButton[kind="primary"]``: the main button of a form; ``QToolButton[kind="stop"]``: a function's stop.
"""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from alox.core.functions import FunctionStatus
from alox.ui import icons

__all__ = [
    "DARK",
    "LIGHT",
    "STATUS_KEYS",
    "THEMES",
    "Badge",
    "Theme",
    "apply",
    "contrast",
    "current",
    "palette",
    "repolish",
    "resolve",
    "save",
    "saved",
    "style_sheet",
]

ORGANIZATION = "ALOX"
APPLICATION = "ALOX Vision Core"
SETTINGS_KEY = "ui/theme"
#: The first one installed is used (Windows, then the bundled Linux fonts).
FONT_FAMILIES = ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans")
FONT_PX = 12

#: The badge (style sheet ``status`` property) of each function status.
STATUS_KEYS: dict[FunctionStatus, str] = {
    FunctionStatus.OFF: "off", FunctionStatus.OFF_COND_OK: "off-ok", FunctionStatus.RUNNING: "running",
    FunctionStatus.FINISHED: "finished", FunctionStatus.FAILED: "error"}


@dataclass(frozen=True)
class Badge:
    """A status badge: text on a background, with a slightly darker edge."""

    background: str
    text: str
    border: str


@dataclass(frozen=True)
class Theme:
    """The palette tokens of a theme (``#rrggbb``)."""

    name: str
    background: str          # the window behind the panels
    surface: str             # panels, inputs, tab pages
    surface_alt: str         # buttons, read-only fields
    hover: str               # a button under the mouse
    pressed: str             # a button pressed
    text: str                # values, button texts
    muted: str               # captions, group titles, secondary texts
    disabled: str            # text of a disabled control
    border: str
    accent: str              # focus, selection, checked buttons, start buttons, the primary button
    accent_hover: str
    accent_text: str         # text on the accent colour
    accent_soft: str         # a checked tool button
    danger: str              # the stop buttons, error messages
    image: str               # around the image (neutral dark in both themes)
    image_text: str          # "NO IMAGE"
    off: Badge
    off_ok: Badge
    running: Badge
    finished: Badge
    error: Badge

    def badge(self, status: FunctionStatus | str) -> Badge:
        key = STATUS_KEYS[status] if isinstance(status, FunctionStatus) else status
        return getattr(self, key.replace("-", "_"))

    def colours(self) -> dict[str, str]:
        """Every colour token, badges flattened (``running.background`` ...)."""
        out: dict[str, str] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Badge):
                out.update({f"{f.name}.{b.name}": getattr(value, b.name) for b in fields(Badge)})
            elif f.name != "name":
                out[f.name] = value
        return out


LIGHT = Theme(
    name="light",
    background="#eef1f5", surface="#ffffff", surface_alt="#f5f7fa", hover="#e7ebf1", pressed="#d8dee7",
    text="#1b2330", muted="#5b6676", disabled="#a3acb9", border="#d3d9e2",
    accent="#2563eb", accent_hover="#1d4ed8", accent_text="#ffffff", accent_soft="#dbe7fd",
    danger="#dc2626", image="#1c1f24", image_text="#8b929c",
    off=Badge("#fb923c", "#3b1203", "#ea7a1f"),
    off_ok=Badge("#eef1f5", "#465162", "#d3d9e2"),
    running=Badge("#fcd34d", "#3d2a00", "#eab308"),
    finished=Badge("#4ade80", "#06301a", "#22b55a"),
    error=Badge("#dc2626", "#ffffff", "#b91c1c"),
)

DARK = Theme(
    name="dark",
    background="#14171c", surface="#1d2128", surface_alt="#262b33", hover="#2f353f", pressed="#3a414c",
    text="#e4e8ee", muted="#9aa3b0", disabled="#5d6572", border="#343a44",
    accent="#4f8cff", accent_hover="#6a9fff", accent_text="#0b1220", accent_soft="#22324d",
    danger="#f05252", image="#0e1013", image_text="#7d8592",
    off=Badge("#e8762c", "#1f0d02", "#c95f1a"),
    off_ok=Badge("#262b33", "#b4bcc8", "#3d444f"),
    running=Badge("#d9a91a", "#1f1600", "#b88d0c"),
    finished=Badge("#2fb560", "#03170a", "#24914c"),
    error=Badge("#cf2e2e", "#ffffff", "#a82020"),
)

THEMES: dict[str, Theme] = {t.name: t for t in (LIGHT, DARK)}

_current: Theme | None = None
_asset_dir: Path | None = None


# ================================================================================================== colours
def _luminance(colour: str) -> float:
    """WCAG relative luminance of ``#rrggbb``."""
    channels = [int(colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(foreground: str, background: str) -> float:
    """WCAG contrast ratio of two colours (1 … 21; 4.5 is the AA level for normal text)."""
    a, b = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _alpha(colour: str, alpha: float) -> str:
    c = QColor(colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


# ================================================================================================== style sheet
_QSS = """
QMainWindow, QDialog { background: @background; }
QWidget { color: @text; }
QToolTip { background: @surface; color: @text; border: 1px solid @border; padding: 4px 6px; }

/* ---- tool bar, status bar ---- */
QToolBar { background: @surface; border: none; border-bottom: 1px solid @border; padding: 6px 10px; spacing: 4px; }
QToolBar::separator { background: @border; width: 1px; margin: 4px 8px; }
QToolBar QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 4px 8px; }
QToolBar QToolButton:hover { background: @hover; }
QToolBar QToolButton:pressed { background: @pressed; }
QToolBar QToolButton:checked { background: @accent_soft; border-color: @accent; }
QToolBar QToolButton:disabled { color: @disabled; background: transparent; border-color: transparent; }
QStatusBar { background: @surface; border-top: 1px solid @border; color: @muted; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: @muted; padding: 0px 10px; border-left: 1px solid @border; }

/* ---- panels and texts ---- */
QFrame[card="true"] { background: @surface; border: 1px solid @border; border-radius: 10px; }
QLabel { background: transparent; }
QLabel[role="section"] { color: @muted; font-size: 11px; font-weight: 700; padding: 2px 0px 3px 0px;
    border-bottom: 1px solid @border; }
QLabel[role="name"] { font-weight: 500; }
QLabel[role="brand"] { font-size: 18px; font-weight: 800; color: @text; }
QLabel[role="title"] { font-size: 15px; font-weight: 700; }
QLabel[role="field"] { color: @muted; }
QLabel[role="muted"] { color: @muted; }
QLabel[tone="error"] { color: @danger; font-weight: 600; }

/* ---- status badges ---- */
QLabel[status] { border-radius: 11px; font-size: 11px; font-weight: 700; padding: 0px 8px; }
@badges

/* ---- buttons ---- */
QPushButton { background: @surface_alt; color: @text; border: 1px solid @border; border-radius: 6px;
    padding: 5px 10px; min-height: 18px; font-weight: 600; }
QPushButton:hover { background: @hover; }
QPushButton:pressed { background: @pressed; }
QPushButton:focus { border-color: @accent; }
QPushButton:checked { background: @accent; color: @accent_text; border-color: @accent; }
QPushButton:checked:hover { background: @accent_hover; }
QPushButton:disabled { background: @surface; color: @disabled; border-color: @border; }
QPushButton[kind="primary"] { background: @accent; color: @accent_text; border-color: @accent; }
QPushButton[kind="primary"]:hover { background: @accent_hover; border-color: @accent_hover; }
QPushButton[kind="primary"]:disabled { background: @surface_alt; color: @disabled; border-color: @border; }
QToolButton { background: @surface_alt; color: @text; border: 1px solid @border; border-radius: 6px;
    padding: 3px 5px; font-weight: 600; }
QToolButton:hover { background: @hover; }
QToolButton:pressed { background: @pressed; }
QToolButton:checked { background: @accent_soft; border-color: @accent; }
QToolButton:disabled { background: @surface; color: @disabled; }
QToolButton[kind="stop"]:hover { background: @danger_soft; border-color: @danger; }

/* ---- inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: @surface; color: @text; border: 1px solid @border;
    border-radius: 6px; padding: 4px 8px; min-height: 18px;
    selection-background-color: @accent; selection-color: @accent_text; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: @accent; }
QLineEdit:read-only { background: @surface_alt; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: @background; color: @disabled; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: @muted; }
QComboBox { padding-right: 22px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 22px; }
QComboBox::down-arrow { image: url(@arrow_down); width: 10px; height: 10px; }
QComboBox::down-arrow:disabled { image: url(@arrow_down_off); }
QComboBox QAbstractItemView { background: @surface; color: @text; border: 1px solid @border;
    selection-background-color: @accent; selection-color: @accent_text; outline: none; padding: 2px; }
QSpinBox, QDoubleSpinBox { padding-right: 22px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; border: none; width: 20px; background: transparent; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-position: bottom right;
    border-bottom-right-radius: 6px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@arrow_up); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@arrow_down); width: 9px; height: 9px; }
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled { image: url(@arrow_up_off); }
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled { image: url(@arrow_down_off); }
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover { background: @hover; }
QPlainTextEdit, QListWidget { background: @surface; color: @text; border: 1px solid @border; border-radius: 6px;
    padding: 4px; selection-background-color: @accent; selection-color: @accent_text; }
QListWidget::item { padding: 2px 4px; }
QListWidget[flat="true"] { border: none; background: transparent; padding: 8px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid @muted; border-radius: 4px;
    background: @surface; }
QCheckBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent; image: url(@check); }
QCheckBox::indicator:disabled { background: @surface_alt; border-color: @border; }
QCheckBox::indicator:checked:disabled { background: @disabled; border-color: @disabled; }

/* ---- tabs, group boxes, scroll areas ---- */
QTabWidget::pane { background: @surface; border: 1px solid @border; border-radius: 10px; top: -1px; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab { background: transparent; color: @muted; border: none; border-bottom: 2px solid transparent;
    padding: 7px 14px; margin-right: 2px; font-weight: 600; }
QTabBar::tab:hover:!selected { color: @text; background: @hover; border-top-left-radius: 6px;
    border-top-right-radius: 6px; }
QTabBar::tab:selected { color: @accent; border-bottom: 2px solid @accent; }
QGroupBox { background: @surface; border: 1px solid @border; border-radius: 8px; margin-top: 4px;
    padding: 26px 10px 8px 10px; font-size: 11px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: border; subcontrol-position: top left; left: 11px; top: 7px;
    padding: 0px; color: @muted; }
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QLineEdit, QGroupBox QComboBox, QGroupBox QSpinBox,
QGroupBox QDoubleSpinBox, QGroupBox QToolButton { font-size: 12px; font-weight: 400; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: @scroll; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 24px; }
QScrollBar::handle:horizontal { min-width: 24px; }
QScrollBar::handle:hover { background: @muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- the image display ---- */
QGraphicsView { background: @image; border: none; border-radius: 6px; }
"""

_BADGE = ('QLabel[status="{key}"] {{ background: {b.background}; color: {b.text}; '
          'border: 1px solid {b.border}; }}')


def _assets(theme: Theme) -> dict[str, str]:
    """The images the style sheet refers to (the arrows of combo and spin boxes), drawn by :mod:`alox.ui.icons` in
    the theme's colours into a temporary folder of the process (removed at exit), ``@2x`` variants included; the
    check mark of check boxes."""
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = Path(tempfile.mkdtemp(prefix="alox-ui-"))
        atexit.register(shutil.rmtree, _asset_dir, True)
    out = {}
    for key, name, colour in (("arrow_down", "chevron-down", theme.muted), ("arrow_up", "chevron-up", theme.muted),
                              ("arrow_down_off", "chevron-down", theme.disabled),
                              ("arrow_up_off", "chevron-up", theme.disabled),
                              ("check", "check", theme.accent_text)):
        stem = f"{theme.name}-{key}"
        for suffix, ratio in (("", 1.0), ("@2x", 2.0)):
            target = _asset_dir / f"{stem}{suffix}.png"
            if not target.exists():
                icons.image(name, colour, 12 if key == "check" else 10, ratio).save(str(target))
        out[key] = (_asset_dir / f"{stem}.png").as_posix()
    return out


def style_sheet(theme: Theme) -> str:
    """The application style sheet of ``theme``."""
    values = {f.name: getattr(theme, f.name) for f in fields(theme) if isinstance(getattr(theme, f.name), str)}
    values["danger_soft"] = _alpha(theme.danger, 0.14)
    values["scroll"] = _alpha(theme.muted, 0.45)
    values.update(_assets(theme))
    values["badges"] = "\n".join(_BADGE.format(key=key, b=theme.badge(key)) for key in STATUS_KEYS.values())
    text = re.sub(r"@(\w+)", lambda m: values[m.group(1)], _QSS)
    return text.strip() + "\n"


def palette(theme: Theme) -> QPalette:
    """The QPalette matching ``theme`` (what the style sheet does not cover: menus, dialogs, Fusion drawings)."""
    p = QPalette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    for r, colour in ((role.Window, theme.background), (role.WindowText, theme.text), (role.Base, theme.surface),
                      (role.AlternateBase, theme.surface_alt), (role.Text, theme.text),
                      (role.Button, theme.surface_alt), (role.ButtonText, theme.text),
                      (role.BrightText, theme.danger), (role.Highlight, theme.accent),
                      (role.HighlightedText, theme.accent_text), (role.ToolTipBase, theme.surface),
                      (role.ToolTipText, theme.text), (role.PlaceholderText, theme.muted), (role.Link, theme.accent),
                      (role.Light, theme.surface), (role.Midlight, theme.hover), (role.Mid, theme.border),
                      (role.Dark, theme.pressed), (role.Shadow, theme.image)):
        p.setColor(r, QColor(colour))
    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(group.Disabled, r, QColor(theme.disabled))
    p.setColor(group.Disabled, role.Base, QColor(theme.background))
    p.setColor(group.Disabled, role.Button, QColor(theme.background))
    return p


# ================================================================================================== run time
def resolve(name: str | Theme | None = None) -> Theme:
    """The theme named ``name`` (a Theme is returned as is; None: the saved choice, else light)."""
    if isinstance(name, Theme):
        return name
    key = (name or saved() or LIGHT.name).strip().lower()
    if key not in THEMES:
        raise ValueError(f"unknown theme {name!r} (one of {', '.join(THEMES)})")
    return THEMES[key]


def current() -> Theme:
    """The theme applied last (light before any)."""
    return _current or LIGHT


def applied() -> bool:
    return _current is not None


def apply(app: QApplication | None = None, name: str | Theme | None = None) -> Theme:
    """Install the theme ``name`` (see :func:`resolve`) on the application: Fusion style, palette, font, style
    sheet. Every widget is re-polished by Qt; icons drawn from the tokens are re-made by their owners (the main
    window's ``apply_theme``)."""
    global _current
    theme = resolve(name)
    app = app or QApplication.instance()                                # type: ignore[assignment]
    if app is None:
        raise RuntimeError("no QApplication")
    if app.property("alox_theme") is None:
        app.setStyle("Fusion")                    # follows the palette on every platform (Windows: dark theme)
    app.setPalette(palette(theme))
    font = QFont(app.font())
    font.setFamilies(list(FONT_FAMILIES))
    font.setPixelSize(FONT_PX)
    app.setFont(font)
    app.setStyleSheet(style_sheet(theme))
    app.setProperty("alox_theme", theme.name)
    _current = theme
    return theme


def user_settings() -> QSettings:
    """The operator's user settings (an INI file under the user's configuration folder)."""
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORGANIZATION, APPLICATION)


def saved(settings: QSettings | None = None) -> str | None:
    """The theme remembered in the user settings (None: none, or an unknown name)."""
    value = (settings or user_settings()).value(SETTINGS_KEY)
    name = str(value).strip().lower() if value else ""
    return name if name in THEMES else None


def save(name: str | Theme, settings: QSettings | None = None) -> None:
    """Remember the theme in the user settings."""
    store = settings or user_settings()
    store.setValue(SETTINGS_KEY, resolve(name).name)
    store.sync()


def repolish(widget: QWidget, **properties: Any) -> None:
    """Set dynamic style properties of ``widget`` and re-apply the style sheet to it (when one changed)."""
    changed = False
    for key, value in properties.items():
        if widget.property(key) != value:
            widget.setProperty(key, value)
            changed = True
    if changed:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
