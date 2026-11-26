"""The themes of the operator window (``alox.ui.theme``) and its icons (``alox.ui.icons``)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from alox.core.functions import FunctionStatus
from alox.ui import icons, theme
from alox.ui.app import _parser

HEX = re.compile(r"^#[0-9a-f]{6}$")


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])          # type: ignore[return-value]


@pytest.fixture
def restore(qapp: QApplication) -> Iterator[None]:
    before = theme.current()
    yield
    theme.apply(qapp, before)


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=lambda t: t.name)
def test_every_token_is_defined(t: theme.Theme):
    colours = t.colours()
    assert set(colours) == set(theme.LIGHT.colours())
    assert all(HEX.match(value) for value in colours.values()), colours
    for key in ("background", "surface", "text", "muted", "border", "accent", "image"):
        assert key in colours
    for status in FunctionStatus:
        assert isinstance(t.badge(status), theme.Badge)
    assert theme.THEMES[t.name] is t


def test_the_themes_differ_but_keep_the_image_area_dark():
    assert theme.LIGHT.background != theme.DARK.background and theme.LIGHT.text != theme.DARK.text
    for t in theme.THEMES.values():
        assert theme._luminance(t.image) < 0.03                    # neutral dark around the image in both
        r, g, b = (int(t.image[i:i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, b) - min(r, g, b) <= 12


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=lambda t: t.name)
@pytest.mark.parametrize("status", list(FunctionStatus), ids=lambda s: s.name)
def test_the_status_text_is_legible_on_its_badge(t: theme.Theme, status: FunctionStatus):
    badge = t.badge(status)
    assert theme.contrast(badge.text, badge.background) >= 4.5


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=lambda t: t.name)
def test_the_badges_keep_labview_meanings(t: theme.Theme):
    """OFF orange, OFF OK neutral, RUNNING yellow/amber, FINISHED green, ERROR red."""
    def hue(colour: str) -> int:
        return QColor(colour).hsvHue()

    assert 15 <= hue(t.off.background) <= 35
    grey = QColor(t.off_ok.background)
    assert max(grey.red(), grey.green(), grey.blue()) - min(grey.red(), grey.green(), grey.blue()) <= 16
    assert 38 <= hue(t.running.background) <= 55
    assert 90 <= hue(t.finished.background) <= 160
    assert hue(t.error.background) <= 8 or hue(t.error.background) >= 350
    assert hue(t.running.background) - hue(t.off.background) >= 15          # amber and orange told apart


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=lambda t: t.name)
def test_texts_are_legible(t: theme.Theme):
    for foreground, background in (("text", "background"), ("text", "surface"), ("text", "surface_alt"),
                                   ("muted", "background"), ("muted", "surface"), ("muted", "surface_alt"),
                                   ("accent_text", "accent"), ("accent", "surface"), ("danger", "surface")):
        ratio = theme.contrast(getattr(t, foreground), getattr(t, background))
        assert ratio >= 4.5, (foreground, background, ratio)
    assert theme.contrast(t.image_text, t.image) >= 3.0                      # 30 px "NO IMAGE"


def test_contrast_follows_wcag():
    assert theme.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert theme.contrast("#123456", "#123456") == pytest.approx(1.0)


@pytest.mark.parametrize("t", [theme.LIGHT, theme.DARK], ids=lambda t: t.name)
def test_the_style_sheet_builds_and_parses(qapp: QApplication, restore: None, t: theme.Theme):
    sheet = theme.style_sheet(t)
    assert "@" not in sheet and sheet.count("{") == sheet.count("}")
    for key in theme.STATUS_KEYS.values():
        badge = t.badge(key)
        assert f'QLabel[status="{key}"]' in sheet and badge.background in sheet and badge.text in sheet
    for image in re.findall(r"url\(([^)]+)\)", sheet):
        assert Path(image).is_file()
    warnings: list[str] = []

    def handler(kind: QtMsgType, _context: object, message: str) -> None:
        if kind != QtMsgType.QtDebugMsg:
            warnings.append(message)
    previous = qInstallMessageHandler(handler)
    try:
        theme.apply(qapp, t)
        host = QWidget()
        layout = QVBoxLayout(host)
        for widget in (QLabel("x"), QPushButton("x"), QComboBox(), QSpinBox(), QGroupBox("x")):
            layout.addWidget(widget)
        badge_label = QLabel("RUNNING")
        theme.repolish(badge_label, status="running")
        layout.addWidget(badge_label)
        host.grab()
    finally:
        qInstallMessageHandler(previous)
    assert not [w for w in warnings if "style" in w.lower() or "parse" in w.lower()], warnings
    image = badge_label.grab().toImage()
    assert image.pixelColor(4, image.height() // 2).name() == t.running.background


def test_apply_switches_the_application(qapp: QApplication, restore: None):
    assert theme.apply(qapp, "dark") is theme.DARK
    assert theme.current() is theme.DARK and qapp.styleSheet() == theme.style_sheet(theme.DARK)
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == theme.DARK.background
    assert qapp.palette().color(QPalette.ColorRole.Highlight).name() == theme.DARK.accent
    assert qapp.property("alox_theme") == "dark"
    assert theme.apply(qapp, theme.LIGHT) is theme.LIGHT
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == theme.LIGHT.background
    with pytest.raises(ValueError):
        theme.resolve("sepia")


def test_the_choice_is_remembered(tmp_path: Path):
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    assert theme.saved(settings) is None
    theme.save("dark", settings)
    assert theme.saved(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)) == "dark"
    settings.setValue(theme.SETTINGS_KEY, "sepia")
    assert theme.saved(settings) is None                                 # an unknown name is ignored
    theme.save(theme.DARK)                                               # the user settings (a test folder)
    try:
        assert theme.saved() == "dark" and theme.resolve(None) is theme.DARK
        assert theme.resolve("LIGHT") is theme.LIGHT                     # an explicit choice wins
    finally:
        theme.save("light")


def test_the_command_line_chooses_the_theme():
    assert _parser().parse_args(["--theme", "dark"]).theme == "dark"
    assert _parser().parse_args([]).theme is None
    with pytest.raises(SystemExit):
        _parser().parse_args(["--theme", "sepia"])


@pytest.mark.parametrize("name", icons.NAMES)
def test_every_icon_draws(qapp: QApplication, name: str):
    image = icons.image(name, "#dc2626", 24)
    painted = [image.pixelColor(x, y) for x in range(24) for y in range(24) if image.pixelColor(x, y).alpha() > 128]
    assert len(painted) >= 12
    assert all(abs(c.red() - 0xdc) <= 2 and abs(c.green() - 0x26) <= 2 for c in painted)


def test_a_disabled_icon_keeps_its_colour_faded(qapp: QApplication):
    icon = icons.icon("stop", theme.LIGHT.danger)
    normal = icon.pixmap(24, 24).toImage().pixelColor(12, 12)
    disabled = icon.pixmap(24, 24, icon.Mode.Disabled).toImage().pixelColor(12, 12)
    assert normal.name() == theme.LIGHT.danger and normal.alpha() == 255
    assert disabled.red() > disabled.green() and 60 <= disabled.alpha() <= 140     # still red, faded
