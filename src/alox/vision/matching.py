"""Pattern matching behind one interface (:class:`Matcher`), replacing NI Vision's geometric pattern matching.

Spec: ``docs/vision-core/migration-plan.md`` §4 ("geometric pattern matching": behind a ``Matcher`` interface, NI C API
first, an OpenCV version validated against it on recorded data) and §8; ``docs/vision-core/map.md`` Part 3 §3.6, Part 4
§3.2 (``alox_vision_detect_object_preprocessing``), §3.4. Diagrams: ``alox_function_ll_detect_object_state_machine
d66`` ("Detect Object": ``IMAQ Setup Match Geometric Pattern 2`` + ``IMAQ Match Geometric Pattern 2``),
``alox_vision_bundle_setup_geometric_matching_options d`` (tilt), ``alox_vision_overlay_geometric_pattern_matching_
results d``.

What LabVIEW does (``… detect_object_state_machine d7, d66``): the model is a PNG **with NI vision info** (``IMAQ Read
Image And Vision Info``): the pixels of the template plus the geometric template NI learned in the settings panel
(``…_ui_model``, ``IMAQ Learn Geometric Pattern 2`` with the learn mask). At run time it is only matched:

- curve extraction ("Get image settings" cluster, really the ``Curve Options``): Extraction Mode ``Normal``, Edge
  Threshold, Edge Filter Size ``Normal``, Min Length, Row/Col Search Step Size 5, Max Endpoint Gap 5, Closed F,
  Subpixel F;
- match options: a 6-boolean mode constant ``T F T F F F`` (read as rotation-invariant T, scale-invariant F,
  occlusion-invariant T, the others F: **inferred**, the cluster labels are not visible), subpixel F, range settings
  Rotation Angle [0, 360]° and Occlusion [0, Max Occlusion (%)];
- ``IMAQ Match Geometric Pattern 2``: Number of Matches Requested 50, Minimum Match Score = ``Min Score (%) × 10``
  (NI scores are 0–1000).

NI returns, per match (``GM Match Report``, typedef ``alox_typedef_detect_object_function_results``): Position (x, y,
pixels, the template centre), Angle (degrees, counter-clockwise as displayed), Scale (%), Score (0–1000), Bounding Box
(4 corners), Contrast Reversed, Occlusion %, Template Target Curve Score, Target Template Curve Score, Correlation
Score; the matches are sorted by score. :class:`Match` keeps these fields.

Implementations (:func:`create_matcher` picks one):

1. :class:`NiVisionMatcher` — NI Vision C API (``nivision.dll``) through ``ctypes``: the identical algorithm while the
   NI Vision run-time licence is present. **Skeleton only** (see its docstring): it detects the DLL and its exports
   but raises :class:`NotImplementedError` until the structure layouts are checked against ``nivision.h`` on a machine;
   ``create_matcher("auto")`` does not select it while :attr:`NiVisionMatcher.IMPLEMENTED` is False.
2. :class:`OpenCVMatcher` — OpenCV (optional dependency, extra ``vision``; imported lazily): masked normalised
   cross-correlation ``cv2.matchTemplate(TM_CCOEFF_NORMED)`` with a rotation search and a coarse-to-fine pyramid.
3. :class:`NumpyMatcher` — the same algorithm with NumPy/SciPy (FFT correlation): no extra dependency, slower; for
   tests, simulation and machines without OpenCV.

How the correlation matchers differ from NI geometric matching (to validate on recorded images before NI is dropped):

- NI matches **edge curves** (geometric features): robust to contrast changes, partial occlusion and non-uniform
  lighting; it reports an occlusion % and the two curve scores. Here the score is the Pearson correlation of the
  **grey levels** under the template (``score = 1000 × max(0, r)``): a uniform contrast/brightness change does not
  change it, but occlusion, clutter and lighting gradients lower it, so a ``Min Score`` tuned on NI is not directly
  transferable. ``occlusion`` is reported as 0; the occlusion range and the curve options are accepted and ignored;
  both curve scores and the correlation score are copies of the score.
- Rotation: sampled (coarse step from the template size, refined to about ``atan(0.5 / R)``, R = half diagonal;
  parabolic interpolation of the angle with ``subpixel``); NI's geometric search is continuous. Scale-invariant
  matching (``scale_range`` other than 100 %) is not supported (Search Object does not use it): ``scale`` is 100.
- The NI learned template inside the model PNG cannot be read: the template is re-learned from the PNG's pixels (and
  the learn mask when given) at load time (:func:`load_template`).
- Position: the template centre ``(x0 + (w − 1)/2, y0 + (h − 1)/2)`` of a match whose top-left pixel is ``(x0, y0)``
  (NI's exact convention for the centre of an even-sized template is not documented: ±0.5 px).
"""

from __future__ import annotations

import ctypes
import importlib
import logging
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from scipy import ndimage

from aloxcam.imaging.png import PngError, read_png

__all__ = [
    "CurveOptions",
    "Match",
    "MatchSettings",
    "Matcher",
    "MatcherUnavailable",
    "NiVisionMatcher",
    "NumpyMatcher",
    "OpenCVMatcher",
    "Template",
    "TemplateError",
    "create_matcher",
    "detect_object_preprocessing",
    "extract_curves",
    "load_template",
    "read_template_image",
]

log = logging.getLogger(__name__)


# ================================================================================================== settings, results
@dataclass(frozen=True)
class CurveOptions:
    """NI ``Curve Options`` (``IMAQ Setup Match Geometric Pattern 2``, ``IMAQ Extract Curves``). Defaults: the values
    wired by Search Object (``… detect_object_state_machine d66``) with NI's defaults for the threshold and length.
    Used by the NI matcher and by :func:`extract_curves` (contours overlay); the correlation matchers ignore them."""

    extraction_mode: str = "Normal"          # "Normal" | "Uniform Regions"
    edge_threshold: int = 75
    edge_filter_size: str = "Normal"         # "Fine" | "Normal" | "Contour Tracing"
    min_length: int = 25
    row_step: int = 5
    column_step: int = 5
    max_endpoint_gap: int = 5
    closed: bool = False
    subpixel: bool = False


@dataclass(frozen=True)
class MatchSettings:
    """What ``IMAQ Setup/Match Geometric Pattern 2`` receive. ``min_score`` is NI's 0–1000 scale; ``angle_ranges``
    are ``(min, max)`` degrees (``(0, 360)``: any rotation; ``(−10, 10)`` wraps through 0; ``((0, 0),)``: no
    rotation); ``scale_range`` and ``occlusion_range`` in %."""

    number_of_matches: int = 1
    min_score: float = 800.0
    angle_ranges: tuple[tuple[float, float], ...] = ((0.0, 0.0),)
    scale_range: tuple[float, float] = (100.0, 100.0)
    occlusion_range: tuple[float, float] = (0.0, 0.0)
    subpixel: bool = False
    contrast_reversal: bool = False          # also report matches of inverted contrast (Contrast Reversed = T)
    curve: CurveOptions = field(default_factory=CurveOptions)

    def __post_init__(self) -> None:
        if int(self.number_of_matches) < 0:
            raise ValueError(f"number_of_matches must be >= 0, got {self.number_of_matches!r}")
        object.__setattr__(self, "number_of_matches", int(self.number_of_matches))
        object.__setattr__(self, "min_score", float(self.min_score))
        ranges = tuple((float(lo), float(hi)) for lo, hi in self.angle_ranges) or ((0.0, 0.0),)
        object.__setattr__(self, "angle_ranges", ranges)

    @property
    def rotation(self) -> bool:
        """True when the ranges allow more than one angle."""
        return any(hi != lo for lo, hi in self.angle_ranges)

    def full_circle(self) -> bool:
        return any(abs(hi - lo) >= 360.0 for lo, hi in self.angle_ranges)

    def allowed(self, angle: float) -> bool:
        """``angle`` (any value) lies in one of the ranges (limits included)."""
        a = angle % 360.0
        for lo, hi in self.angle_ranges:
            span = hi - lo
            if abs(span) >= 360.0:
                return True
            if span < 0:
                span += 360.0
            if (a - lo) % 360.0 <= span + 1e-9:
                return True
        return False

    def sample_angles(self, step: float) -> list[float]:
        """Angles (degrees, in [0, 360)) covering the ranges with at most ``step`` between neighbours."""
        if not self.rotation:
            return [self.angle_ranges[0][0] % 360.0]
        out: list[float] = []
        for lo, hi in self.angle_ranges:
            span = hi - lo
            if abs(span) >= 360.0:
                n = max(1, math.ceil(360.0 / step))
                out.extend(i * 360.0 / n for i in range(n))
                continue
            if span < 0:
                span += 360.0
            n = max(1, math.ceil(span / step))
            out.extend(lo + span * i / n for i in range(n + 1))
        seen: list[float] = []
        for a in sorted(x % 360.0 for x in out):
            if not seen or abs(a - seen[-1]) > 1e-6:
                seen.append(a)
        if len(seen) > 1 and abs(seen[0] + 360.0 - seen[-1]) <= 1e-6:
            seen.pop()
        return seen


@dataclass(frozen=True)
class Match:
    """One match, as NI's ``GM Match Report``: ``position`` = template centre (pixels, x right, y down); ``angle`` in
    degrees in [0, 360), counter-clockwise as displayed; ``scale`` %; ``score`` 0–1000; ``bounding_box`` the 4
    corners of the rotated template (top-left, top-right, bottom-right, bottom-left of the template)."""

    position: tuple[float, float]
    angle: float
    scale: float
    score: float
    bounding_box: tuple[tuple[float, float], ...]
    contrast_reversed: bool = False
    occlusion: float = 0.0
    template_target_curve_score: float = 0.0
    target_template_curve_score: float = 0.0
    correlation_score: float = 0.0


class TemplateError(ValueError):
    """The template cannot be learned (empty, constant, too small) or its file cannot be read."""


class MatcherUnavailable(RuntimeError):
    """The requested implementation cannot be used here (DLL or package missing)."""


@dataclass(frozen=True, eq=False)
class Template:
    """A learned template: grey ``pixels`` (float32), the ``mask`` of the pixels used (bool; None = all),
    the file it came from, and the implementation's learned ``data`` (NI: the template image handle)."""

    pixels: np.ndarray
    mask: np.ndarray | None = None
    path: Path | None = None
    data: Any = None

    @property
    def size(self) -> tuple[int, int]:
        """``(width, height)``."""
        return int(self.pixels.shape[1]), int(self.pixels.shape[0])


@runtime_checkable
class Matcher(Protocol):
    """Learn a template, then find it in images (``IMAQ Learn / Setup / Match Geometric Pattern``)."""

    name: str

    def learn(self, pixels: np.ndarray, *, mask: np.ndarray | None = None,
              curve: CurveOptions | None = None) -> Template:
        """``IMAQ Learn Geometric Pattern 2``: :class:`TemplateError` when the template cannot be used."""

    def match(self, image: np.ndarray, template: Template, settings: MatchSettings) -> list[Match]:
        """``IMAQ Match Geometric Pattern 2``: the matches with ``score ≥ settings.min_score``, best first, at most
        ``settings.number_of_matches``."""


# ================================================================================================== image helpers
def _grey(image: np.ndarray) -> np.ndarray:
    """2-D float32 grey image; RGB(A) → luminance (0.299, 0.587, 0.114), as the image readers of the camera server."""
    arr = np.asarray(image)
    if arr.ndim == 3 and arr.shape[2] in (3, 4):
        arr = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    if arr.ndim != 2 or arr.size == 0:
        raise TemplateError(f"a non-empty 2-D grey image is required, got shape {arr.shape}")
    return np.ascontiguousarray(arr, dtype=np.float32)


def read_template_image(path: str | os.PathLike[str]) -> np.ndarray:
    """The pixels of a model or mask PNG (NI's private "vision info" chunks are skipped). :class:`TemplateError` when
    the file is missing or cannot be decoded."""
    try:
        return read_png(path)
    except FileNotFoundError:
        raise TemplateError(f"template file not found: {path}") from None
    except (OSError, PngError) as exc:
        raise TemplateError(f"template file {path}: {exc}") from exc


def load_template(matcher: Matcher, model_path: str | os.PathLike[str],
                  mask_path: str | os.PathLike[str] | None = None, *, curve: CurveOptions | None = None) -> Template:
    """Read a model PNG (and its learn mask: pixels ≠ 0 are learned, **inferred** NI convention) and learn it with
    ``matcher``. NI's learned data stored in the file is not decoded (module docstring): the template is re-learned
    from the pixels. A mask that does not have the model's size is an error."""
    pixels = read_template_image(model_path)
    mask = None
    if mask_path is not None:
        raw = np.asarray(read_template_image(mask_path))
        if raw.ndim == 3:
            raw = raw[..., :3].max(axis=2)
        mask = raw != 0
        if mask.shape != np.asarray(pixels).shape[:2]:
            raise TemplateError(f"learn mask {mask_path}: size {mask.shape[::-1]} differs from the model "
                                f"{np.asarray(pixels).shape[1::-1]}")
    template = matcher.learn(pixels, mask=mask, curve=curve)
    return Template(template.pixels, template.mask, Path(model_path), template.data)


def _odd_up(n: int) -> int:
    """``tools_round_u32_to_upper_odd_value``: even → n + 1."""
    n = int(n)
    return n + 1 if n % 2 == 0 else n


def detect_object_preprocessing(image: np.ndarray, morph_open: int, morph_close: int) -> np.ndarray:
    """``alox_vision_detect_object_preprocessing`` (map Part 4 §3.2): ``#Open`` and ``#Close`` rounded up to odd ``k``;
    each with ``k ≥ 3`` applies ``IMAQ GrayMorphology`` Open then Close with a k×k square of ones. The border
    handling of ``IMAQ ImageBorderSize`` is not visible: the edge pixels are repeated (**inferred**). Returns a new
    array of the input's dtype (the input when nothing applies)."""
    arr = np.asarray(image)
    out = arr
    k_open, k_close = _odd_up(morph_open), _odd_up(morph_close)
    if k_open >= 3:
        out = ndimage.grey_opening(out, size=(k_open, k_open), mode="nearest")
    if k_close >= 3:
        out = ndimage.grey_closing(out, size=(k_close, k_close), mode="nearest")
    return out


def extract_curves(image: np.ndarray, curve: CurveOptions, *, max_curves: int = 200,
                   max_points: int = 400) -> list[np.ndarray]:
    """An approximation of ``IMAQ Extract Curves`` for the contours overlay (``… detect_object_state_machine d66``
    "Contours?"): pixels whose Sobel gradient magnitude (/8, i.e. grey levels per pixel) reaches
    ``curve.edge_threshold``, grouped by 8-connectivity, groups of at least ``curve.min_length`` pixels; each curve
    as an ``(n, 2)`` array of ``(x, y)``, sub-sampled to ``max_points``. Display only."""
    g = _grey(image).astype(np.float64)
    gx = ndimage.sobel(g, axis=1, mode="nearest") / 8.0
    gy = ndimage.sobel(g, axis=0, mode="nearest") / 8.0
    edges = np.hypot(gx, gy) >= float(curve.edge_threshold)
    labels, count = ndimage.label(edges, structure=np.ones((3, 3), dtype=bool))
    if count == 0:
        return []
    sizes = np.bincount(labels.ravel())
    keep = [i for i in np.argsort(sizes[1:])[::-1] + 1 if sizes[i] >= max(1, int(curve.min_length))][:max_curves]
    curves: list[np.ndarray] = []
    for index in keep:
        ys, xs = np.nonzero(labels == index)
        pts = np.column_stack([xs, ys]).astype(np.float64)
        if len(pts) > max_points:
            pts = pts[np.linspace(0, len(pts) - 1, max_points).astype(int)]
        curves.append(pts)
    return curves


def _block_mean(image: np.ndarray, factor: int) -> np.ndarray:
    """Down-sample by an integer factor (mean of ``factor × factor`` blocks; the remainder rows/columns dropped)."""
    if factor <= 1:
        return image
    h, w = image.shape
    h2, w2 = h // factor, w // factor
    cropped = image[:h2 * factor, :w2 * factor]
    return cropped.reshape(h2, factor, w2, factor).mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def _rotation(angle_deg: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return math.cos(a), math.sin(a)


def _canvas(width: int, height: int, angle_deg: float) -> tuple[int, int]:
    c, s = _rotation(angle_deg)
    cw = max(1, math.ceil(abs(width * c) + abs(height * s) - 1e-6))
    ch = max(1, math.ceil(abs(width * s) + abs(height * c) - 1e-6))
    return cw, ch


def bounding_box(center: Sequence[float], size: Sequence[int], angle_deg: float) -> tuple[tuple[float, float], ...]:
    """The 4 corners of a ``size = (w, h)`` template centred at ``center`` and rotated by ``angle_deg``
    (counter-clockwise as displayed): top-left, top-right, bottom-right, bottom-left of the template."""
    c, s = _rotation(angle_deg)
    w, h = float(size[0]), float(size[1])
    cx, cy = float(center[0]), float(center[1])
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + c * dx + s * dy, cy - s * dx + c * dy))
    return tuple(out)


def _peaks(score: np.ndarray, threshold: float, size: int, limit: int) -> list[tuple[int, int, float]]:
    """Local maxima ``(row, col, value)`` of ``score`` at least ``threshold``, best first, at most ``limit``."""
    if score.size == 0:
        return []
    size = max(3, size | 1)
    local = ndimage.maximum_filter(score, size=size, mode="constant", cval=-np.inf)
    rows, cols = np.nonzero((score >= local) & (score >= threshold))
    if rows.size == 0:
        return []
    values = score[rows, cols]
    order = np.argsort(values)[::-1][:limit]
    return [(int(rows[i]), int(cols[i]), float(values[i])) for i in order]


def _parabola(left: float, mid: float, right: float) -> float:
    """Offset in [−0.5, 0.5] of the vertex of the parabola through three equally spaced samples."""
    den = left - 2.0 * mid + right
    if not math.isfinite(den) or den >= 0:
        return 0.0
    return float(min(0.5, max(-0.5, 0.5 * (left - right) / den)))


@dataclass
class _Candidate:
    value: float          # signed correlation
    x: float
    y: float
    angle: float


# ================================================================================================== correlation engine
class _CorrelationMatcher:
    """Masked normalised cross-correlation with a rotation search, coarse to fine (module docstring). Subclasses give
    the two primitives :meth:`_ncc` (valid-mode correlation map) and :meth:`_warp` (rotated template on a canvas)."""

    name = "correlation"
    #: smallest template side kept at the coarse level, and the deepest pyramid level
    coarse_min_side = 16
    max_level = 3

    # ---------------------------------------------------------------------------------------------- primitives
    def _ncc(self, image: np.ndarray, templ: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        raise NotImplementedError

    def _warp(self, templ: np.ndarray, matrix: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        """``templ`` resampled on a ``size = (w, h)`` canvas: canvas pixel ``(u, v)`` ← template at
        ``matrix @ (u, v, 1)`` (bilinear, 0 outside)."""
        raise NotImplementedError

    # ---------------------------------------------------------------------------------------------- learn
    def learn(self, pixels: np.ndarray, *, mask: np.ndarray | None = None,
              curve: CurveOptions | None = None) -> Template:
        grey = _grey(pixels)
        h, w = grey.shape
        if h < 4 or w < 4:
            raise TemplateError(f"template too small ({w} x {h} pixels)")
        m = None
        if mask is not None:
            m = np.asarray(mask).astype(bool)
            if m.shape != grey.shape:
                raise TemplateError(f"mask size {m.shape[::-1]} differs from the template {grey.shape[::-1]}")
            if m.all():
                m = None
        used = grey if m is None else grey[m]
        if used.size < 16 or float(np.ptp(used)) == 0.0:
            raise TemplateError("template has no contrast (constant pixels) or too few pixels under the mask")
        return Template(grey, m)

    # ---------------------------------------------------------------------------------------------- match
    def match(self, image: np.ndarray, template: Template, settings: MatchSettings) -> list[Match]:
        if settings.number_of_matches <= 0:
            return []
        img = _grey(image)
        img = img - np.float32(img.mean())                      # NCC is offset-invariant; better FFT accuracy
        tpl = template.pixels.astype(np.float32)
        th, tw = tpl.shape
        ih, iw = img.shape
        if th > ih or tw > iw:
            return []
        mask = template.mask
        level = 0
        while (level < self.max_level and min(th, tw) >> (level + 1) >= self.coarse_min_side
               and min(ih, iw) >> (level + 1) >= 2 * self.coarse_min_side):
            level += 1
        factor = 1 << level
        cands, step = self._coarse(img, tpl, mask, factor, settings)
        results: list[tuple[float, Match]] = []
        top = max((abs(c.value) if settings.contrast_reversal else c.value) for c in cands) if cands else 0.0
        for cand in cands:
            if (abs(cand.value) if settings.contrast_reversal else cand.value) < 0.5 * top:
                continue                                         # far below the best candidate: not refined
            found = self._refine(img, tpl, mask, cand, factor, step, settings)
            if found is not None:
                results.append(found)
        results.sort(key=lambda item: item[0], reverse=True)
        kept: list[Match] = []
        min_dist = 0.5 * min(tw, th)
        for score, m in results:
            if score < settings.min_score:
                continue
            if any(math.hypot(m.position[0] - k.position[0], m.position[1] - k.position[1]) < min_dist for k in kept):
                continue
            kept.append(m)
            if len(kept) >= settings.number_of_matches:
                break
        return kept

    def _rotated(self, templ: np.ndarray, mask: np.ndarray | None, angle: float
                 ) -> tuple[np.ndarray, np.ndarray | None]:
        """The template rotated by ``angle`` about its centre on its bounding canvas, and the mask of the canvas
        pixels fully inside the (masked) template."""
        if angle % 360.0 == 0.0:
            return templ, mask
        h, w = templ.shape
        cw, ch = _canvas(w, h, angle)
        c, s = _rotation(angle)
        tcx, tcy = (w - 1) / 2.0, (h - 1) / 2.0
        ccx, ccy = (cw - 1) / 2.0, (ch - 1) / 2.0
        # canvas (u, v) -> template (x, y): x = c·(u−ccx) − s·(v−ccy) + tcx ; y = s·(u−ccx) + c·(v−ccy) + tcy
        matrix = np.array([[c, -s, tcx - c * ccx + s * ccy], [s, c, tcy - s * ccx - c * ccy]])
        rotated = self._warp(templ, matrix, (cw, ch))
        support = np.ones_like(templ) if mask is None else mask.astype(np.float32)
        valid = self._warp(support, matrix, (cw, ch)) >= 0.999
        return rotated, valid

    def _score_map(self, image: np.ndarray, templ: np.ndarray, mask: np.ndarray | None,
                   settings: MatchSettings) -> tuple[np.ndarray, np.ndarray]:
        raw = self._ncc(image, templ, mask)
        raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0).clip(-1.0, 1.0)
        return (np.abs(raw) if settings.contrast_reversal else raw), raw

    def _coarse(self, img: np.ndarray, tpl: np.ndarray, mask: np.ndarray | None, factor: int,
                settings: MatchSettings) -> tuple[list[_Candidate], float]:
        """Candidates on the ``factor``-reduced image over the sampled angles, and the angle step used (0: no
        rotation)."""
        ci = _block_mean(img, factor)
        ct = _block_mean(tpl, factor)
        cm = None if mask is None else _block_mean(mask.astype(np.float32), factor) >= 0.999
        th, tw = ct.shape
        radius = 0.5 * math.hypot(tw, th)
        step = min(12.0, max(1.0, math.degrees(math.atan2(1.5, radius))))
        threshold = max(0.1, 0.6 * settings.min_score / 1000.0)
        limit = int(min(100, max(10, 2 * settings.number_of_matches)))
        cands: list[_Candidate] = []
        for angle in settings.sample_angles(step):
            rt, rm = self._rotated(ct, cm, angle)
            if rt.shape[0] > ci.shape[0] or rt.shape[1] > ci.shape[1]:
                continue
            score, raw = self._score_map(ci, rt, rm, settings)
            ch, cw = rt.shape
            for row, col, _ in _peaks(score, threshold, min(ch, cw) // 2, limit):
                cx = (col + (cw - 1) / 2.0) * factor + (factor - 1) / 2.0
                cy = (row + (ch - 1) / 2.0) * factor + (factor - 1) / 2.0
                cands.append(_Candidate(float(raw[row, col]), cx, cy, angle))
        cands.sort(key=lambda c: abs(c.value) if settings.contrast_reversal else c.value, reverse=True)
        kept: list[_Candidate] = []
        min_dist = 0.25 * min(tpl.shape)
        for cand in cands:
            if any(math.hypot(cand.x - k.x, cand.y - k.y) < min_dist for k in kept):
                continue
            kept.append(cand)
            if len(kept) >= limit:
                break
        return kept, (step if settings.rotation else 0.0)

    def _evaluate(self, img: np.ndarray, tpl: np.ndarray, mask: np.ndarray | None, x: float, y: float,
                  angle: float, margin: int, settings: MatchSettings) -> tuple[float, float, float, float] | None:
        """Best correlation of the template rotated by ``angle`` with its centre within ``margin`` pixels of
        ``(x, y)``: ``(score used for ranking, signed correlation, x, y)``, None when the window is too small."""
        rt, rm = self._rotated(tpl, mask, angle)
        ch, cw = rt.shape
        ih, iw = img.shape
        x0 = max(0, math.floor(x - (cw - 1) / 2.0) - margin)
        y0 = max(0, math.floor(y - (ch - 1) / 2.0) - margin)
        x1 = min(iw, math.ceil(x + (cw - 1) / 2.0) + margin + 1)
        y1 = min(ih, math.ceil(y + (ch - 1) / 2.0) + margin + 1)
        window = img[y0:y1, x0:x1]
        if window.shape[0] < ch or window.shape[1] < cw:
            return None
        score, raw = self._score_map(window, rt, rm, settings)
        row, col = np.unravel_index(int(np.argmax(score)), score.shape)
        bx = x0 + col + (cw - 1) / 2.0
        by = y0 + row + (ch - 1) / 2.0
        if settings.subpixel:
            if 0 < col < score.shape[1] - 1:
                bx += _parabola(score[row, col - 1], score[row, col], score[row, col + 1])
            if 0 < row < score.shape[0] - 1:
                by += _parabola(score[row - 1, col], score[row, col], score[row + 1, col])
        return float(score[row, col]), float(raw[row, col]), bx, by

    def _refine(self, img: np.ndarray, tpl: np.ndarray, mask: np.ndarray | None, cand: _Candidate, factor: int,
                coarse_step: float, settings: MatchSettings) -> tuple[float, Match] | None:
        """Full-resolution search around a candidate: the position within the coarse uncertainty and, with rotation,
        the angle by halving steps from half the coarse step down to ``atan(0.5 / R)`` (R = half diagonal)."""
        th, tw = tpl.shape
        margin = 2 * factor + 2
        first = self._evaluate(img, tpl, mask, cand.x, cand.y, cand.angle, margin, settings)
        if first is None:
            return None
        value, signed, x, y = first
        angle = cand.angle
        if coarse_step > 0:
            fine = min(coarse_step, max(0.05, math.degrees(math.atan2(0.5, 0.5 * math.hypot(tw, th)))))
            step = coarse_step / 2.0
            local = max(2, margin // 2)
            while True:
                sides: list[float] = []
                best = (value, signed, x, y, angle)
                for a in (angle - step, angle + step):
                    found = self._evaluate(img, tpl, mask, x, y, a, local, settings) if settings.allowed(a) else None
                    sides.append(found[0] if found is not None else -1.0)
                    if found is not None and found[0] > best[0]:
                        best = (found[0], found[1], found[2], found[3], a)
                moved = best[4] != angle
                value, signed, x, y, angle = best
                if step <= fine and not moved:
                    if settings.subpixel and min(sides) >= 0.0:
                        angle += step * _parabola(sides[0], value, sides[1])
                    break
                if step > fine:
                    step = max(fine, step / 2.0)
        angle %= 360.0
        score = 1000.0 * max(0.0, value)
        match = Match(position=(x, y), angle=angle, scale=100.0, score=score,
                      bounding_box=bounding_box((x, y), (tw, th), angle), contrast_reversed=signed < 0,
                      occlusion=0.0, template_target_curve_score=score, target_template_curve_score=score,
                      correlation_score=score)
        return score, match


class NumpyMatcher(_CorrelationMatcher):
    """Correlation matcher on NumPy/SciPy (FFT correlation, ``scipy.ndimage`` rotation): no extra dependency."""

    name = "numpy"

    def _ncc(self, image: np.ndarray, templ: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        from scipy.signal import fftconvolve

        image = image.astype(np.float64)
        t = templ.astype(np.float64)
        m = np.ones_like(t) if mask is None else mask.astype(np.float64)
        n = float(m.sum())
        if n == 0:
            return np.zeros((image.shape[0] - t.shape[0] + 1, image.shape[1] - t.shape[1] + 1))
        tz = (t - float((t * m).sum()) / n) * m
        t_norm = math.sqrt(float((tz * tz).sum()))

        def corr(a: np.ndarray, k: np.ndarray) -> np.ndarray:
            return fftconvolve(a, k[::-1, ::-1], mode="valid")

        num = corr(image, tz)
        s1 = corr(image, m)
        s2 = corr(image * image, m)
        var = s2 - s1 * s1 / n
        scale = float(np.mean(image * image)) * n + 1e-12
        den = t_norm * np.sqrt(np.clip(var, 0.0, None))
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where((var > 1e-9 * scale) & (den > 0), num / den, 0.0)
        return out

    def _warp(self, templ: np.ndarray, matrix: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        # ndimage works in (row, col) = (y, x): input (y, x) = M' @ (v, u) + offset
        (a, b, tx), (c, d, ty) = matrix
        m2 = np.array([[d, c], [b, a]])
        offset = np.array([ty, tx])
        return ndimage.affine_transform(templ.astype(np.float32), m2, offset=offset, output_shape=(size[1], size[0]),
                                        order=1, mode="constant", cval=0.0)


class OpenCVMatcher(_CorrelationMatcher):
    """Correlation matcher on OpenCV (``cv2.matchTemplate`` ``TM_CCOEFF_NORMED`` with a mask, ``cv2.warpAffine``);
    :class:`MatcherUnavailable` when ``cv2`` cannot be imported (extra ``vision``: ``opencv-python-headless``)."""

    name = "opencv"

    def __init__(self) -> None:
        try:
            self._cv2 = importlib.import_module("cv2")
        except ImportError as exc:
            raise MatcherUnavailable(f"OpenCV is not installed ({exc}); pip install opencv-python-headless") from None

    def _ncc(self, image: np.ndarray, templ: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        cv2 = self._cv2
        img = np.ascontiguousarray(image, dtype=np.float32)
        t = np.ascontiguousarray(templ, dtype=np.float32)
        if mask is None or bool(np.all(mask)):
            return cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        m = np.ascontiguousarray(mask, dtype=np.float32)
        return cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED, mask=m)

    def _warp(self, templ: np.ndarray, matrix: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        cv2 = self._cv2
        return cv2.warpAffine(np.ascontiguousarray(templ, dtype=np.float32), matrix.astype(np.float64), size,
                              flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT,
                              borderValue=0.0)


# ================================================================================================== NI Vision (skeleton)
class NiVisionMatcher:
    """NI Vision C API (``nivision.dll``) through ``ctypes`` — **skeleton, not usable yet**.

    Intended mapping (NI Vision C reference; the LabVIEW VIs are wrappers of these):

    - model file: ``imaqCreateImage(IMAQ_IMAGE_U8, 0)`` + ``imaqReadVisionFile(image, path, NULL, NULL)`` reads the PNG
      **and** the geometric template NI learned in the settings panel, so no re-learning is needed and results are
      NI's own (the purpose of this class, migration plan §4);
    - ``learn``: ``imaqArrayToImage`` + ``imaqLearnGeometricPattern(template, originOffset, curveOptions,
      advancedLearnOptions, mask)``;
    - ``match``: ``imaqMatchGeometricPattern2(image, template, curveOptions, matchOptions, advancedMatchOptions, roi,
      &numMatches)`` → ``GeometricPatternMatch2[]`` (position, rotation, scale, score, corner[4], inverse, occlusion,
      templateMatchCurveScore, matchTemplateCurveScore, correlationScore), freed with ``imaqDispose``; errors with
      ``imaqGetLastError``.

    Why a skeleton: ``ctypes`` needs the exact layouts of ``CurveOptions``, ``MatchGeometricPatternOptions``,
    ``MatchGeometricPatternAdvancedOptions2``, ``GeometricPatternMatch2`` and the enum values (match modes, image
    types) of the installed NI Vision version; a wrong layout corrupts memory instead of failing. They must be copied
    from ``nivision.h`` of the machine (``<NI>\\Shared\\ExternalCompilerSupport\\C\\include``) and tested there. Until
    then :meth:`learn` and :meth:`match` raise :class:`NotImplementedError` and :func:`create_matcher` ("auto") does
    not select this class (:attr:`IMPLEMENTED`); the constructor only loads the DLL and checks its exports.
    """

    name = "nivision"
    IMPLEMENTED = False
    DLL_NAMES = ("nivision.dll",)
    EXPORTS = ("imaqCreateImage", "imaqDispose", "imaqArrayToImage", "imaqReadVisionFile",
               "imaqLearnGeometricPattern", "imaqMatchGeometricPattern2", "imaqGetLastError")

    def __init__(self, dll_path: str | os.PathLike[str] | None = None) -> None:
        self._dll = self._load(dll_path)
        if self._dll is None:
            raise MatcherUnavailable("NI Vision (nivision.dll) is not available on this machine")
        missing = [name for name in self.EXPORTS if not hasattr(self._dll, name)]
        if missing:
            raise MatcherUnavailable(f"nivision.dll lacks {', '.join(missing)}")

    @classmethod
    def _load(cls, dll_path: str | os.PathLike[str] | None = None) -> Any:
        if sys.platform != "win32":
            return None
        loader = getattr(ctypes, "WinDLL", ctypes.CDLL)
        for name in ([str(dll_path)] if dll_path else list(cls.DLL_NAMES)):
            try:
                return loader(name)
            except OSError:
                continue
        return None

    @classmethod
    def available(cls) -> bool:
        """The DLL loads here (Windows with the NI Vision run-time)."""
        return cls._load() is not None

    def learn(self, pixels: np.ndarray, *, mask: np.ndarray | None = None,
              curve: CurveOptions | None = None) -> Template:
        raise NotImplementedError("NiVisionMatcher.learn: the nivision.dll structure layouts are not written yet "
                                  "(see the class docstring)")

    def match(self, image: np.ndarray, template: Template, settings: MatchSettings) -> list[Match]:
        raise NotImplementedError("NiVisionMatcher.match: the nivision.dll structure layouts are not written yet "
                                  "(see the class docstring)")


# ================================================================================================== factory
def create_matcher(prefer: str = "auto") -> Matcher:
    """The matcher to use. ``prefer``: ``"auto"`` (NI when implemented and present, else OpenCV when installed, else
    NumPy), ``"ni"``, ``"opencv"``, ``"numpy"``. :class:`MatcherUnavailable` when an explicit choice cannot be
    used; :class:`ValueError` for an unknown name."""
    choice = str(prefer).strip().lower()
    if choice in ("ni", "nivision"):
        return NiVisionMatcher()
    if choice in ("opencv", "cv2"):
        return OpenCVMatcher()
    if choice == "numpy":
        return NumpyMatcher()
    if choice != "auto":
        raise ValueError(f"unknown matcher {prefer!r} (auto, ni, opencv, numpy)")
    if NiVisionMatcher.available():
        if NiVisionMatcher.IMPLEMENTED:
            try:
                return NiVisionMatcher()
            except MatcherUnavailable as exc:
                log.warning("NI Vision matcher not usable: %s", exc)
        else:
            log.info("nivision.dll found, but the NI matcher is a skeleton: the correlation matcher is used")
    try:
        return OpenCVMatcher()
    except MatcherUnavailable:
        return NumpyMatcher()
