"""Focus (sharpness) metrics of ALOX: ``alox_vision_measure_focus_fft.vi`` and ``alox_vision_measure_focus_sobel.vi``.

Spec: ``docs/07-focus-metrics.md`` (section numbers below refer to it unless stated); the callers are specified in
``docs/06-autofocus-refocus.md`` (§4.5 autofocus ``Measure``, §5.6 Fin ROI, §5.7 refocus ``Mesure Position``).

Processing chain (§1.2)::

    image ─► ROI (IMAQ Extract 2, only for a non-empty rectangle, §4.1)
          ─► nearest-neighbour resample to rint(W·s) × rint(H·s) (IMAQ Resample, Zero Order, §4.2–§4.3)
          ├─► FFT metric : Σ |DFT| over the whole plane, DC included (§5)
          └─► edge metric: mean of the saturated edge image (IMAQ EdgeDetection, threshold 0, §6); FULL: product of the
                           six means (§6.2)

Design rules (§9.1):

- pure functions on NumPy arrays: no shared buffers (defect F3 fixed), the input is never modified; the "Sigma"
  refocus path returns a *new* filtered array that the caller displays and saves (LabVIEW filters in place, F8);
- the input dtype is kept (U8 expected, owner: Mono8 sensors); edge images are saturated to it (§6.4) and the scores are
  rounded to float32 (SGL) before being returned as Python ``float`` (§8, F17);
- every NI behaviour the printouts cannot settle is a keyword parameter with the provisional default of §9.1 rule 3:
  ``phase="corner"`` (§4.3, test A1), ``border="edge"`` (§6.5, A4), ``clip_rect=False`` (§4.1, A2),
  ``sequential_f32=False`` (§8, A8), ``sigma_lowpass=True`` and ``include_center=True`` (§7.2, A9). Freeze the measured
  values in :class:`NiCompatMetrics` once the validation plan of §10 has run; they are never client-visible;
- errors raise :class:`FocusMetricError` (a :class:`aloxcam.errors.FocusError`, code 9503 ``FOCUS_METRIC``, 09 §10.5),
  because NI's own codes are unknown; :func:`measure_lv_style` gives LabVIEW's ``(image, 0.0, error)`` contract (F11);
- deliberate deviations from LabVIEW (§11): FULL is computed sequentially with separate buffers (F4, owner Q27 "fix the
  FULL race"), and an FFT error in the refocus FULL composition is propagated instead of a silent 0 (F6);
  ``Timer (ms)`` is not implemented (rule 7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

from aloxcam.errors import Code, FocusError

__all__ = [
    "BORDERS",
    "EDGE_METHODS",
    "METHODS",
    "PHASES",
    "REFOCUS_EDGE",
    "SIGMA_LOWPASS",
    "FocusMetricError",
    "NiCompatMetrics",
    "autofocus_metric_method",
    "extract_roi",
    "fin_roi",
    "focus_edge",
    "focus_fft",
    "histogram_mean",
    "lv_round",
    "lv_to_i32",
    "measure",
    "measure_lv_style",
    "ni_edge",
    "ni_lowpass",
    "resample_nearest",
    "subsample",
]

#: IMAQ EdgeDetection methods in the order of the FULL frame [Doc6 p.957]; NI enum values 0..5 (inferred, §2).
EDGE_METHODS = ("Differentiation", "Gradient", "Prewitt", "Roberts", "Sigma", "Sobel")
#: Border fills of the edge filters and of the LowPass (numpy ``np.pad`` modes; NI's fill is unknown, §6.5).
BORDERS = ("edge", "reflect", "constant")
#: Zero-order source-pixel mappings of IMAQ Resample (unknown in NI, §4.3).
PHASES = ("corner", "corner_round", "center")
#: The ``Autofocus Method`` enum (06 §2.4; codes 0..6 in this order, owner answers §1).
METHODS = ("FFT", "Sobel", "Prewitt", "Diff", "Gradient", "Sigma", "FULL")
#: Refocus edge methods → IMAQ EdgeDetection method (06 §5.7).
REFOCUS_EDGE = {"Sobel": "Sobel", "Prewitt": "Prewitt", "Diff": "Differentiation", "Gradient": "Gradient"}
#: ``IMAQ LowPass {X Size, Y Size, % Tolerance}`` run by the refocus "Sigma" method (§7.1).
SIGMA_LOWPASS = (20, 20, 40.0)

_I32_MIN, _I32_MAX = -(2 ** 31), 2 ** 31 - 1


class FocusMetricError(FocusError):
    """An NI Vision call inside a metric VI failed; LabVIEW then returns score 0 (§2, F11). Code 9503.

    ``image`` is the image the caller keeps: for "Sigma" the already low-pass-filtered one (LabVIEW filters in place
    before the metric runs, §7.1), else None (= the input)."""

    def __init__(self, source: str, *, image: np.ndarray | None = None) -> None:
        super().__init__(Code.FOCUS_METRIC, source)
        self.image = image


# ================================================================================================== LabVIEW numerics
def lv_to_i32(x: float) -> int:
    """LabVIEW ``To Long Integer`` (§2, §4.2): round to nearest, **ties to even**, saturate to I32.
    A non-finite value (NaN scale factor) raises :class:`FocusMetricError`."""
    if not np.isfinite(x):
        raise FocusMetricError(f"IMAQ Resample: non-finite size {x!r}")
    return int(min(max(np.rint(x), _I32_MIN), _I32_MAX))


def lv_round(x: float) -> int:
    """LabVIEW float → integer conversion: round half to even (06 §1.3, §5.6)."""
    return int(np.rint(x))


# ================================================================================================== front end (§4)
def extract_roi(img: np.ndarray, rect: Sequence[int] | None, clip_rect: bool = False) -> np.ndarray:
    """``IMAQ Extract 2`` with steps 1×1 (§4, §4.1). ``rect = [Left, Top, Right, Bottom]``, right and bottom edges
    excluded (inferred, A2). ``None`` or an empty array → the whole image, no extraction (Compare Aggregates with an
    empty array: ``[0, 0, 0, 0]`` is *not* empty). Any other rectangle is applied; one that is not 4 elements, empty
    (``R ≤ L`` or ``B ≤ T``) or not inside the image raises :class:`FocusMetricError` (NI behaviour unknown, F12), unless
    ``clip_rect`` first clips it to the image (the other candidate of test A2). Returns a view (never modified)."""
    if rect is None or len(rect) == 0:
        return img
    if len(rect) != 4:
        raise FocusMetricError(f"IMAQ Extract 2: Optional Rectangle has {len(rect)} elements (4 expected)")
    left, top, right, bottom = (int(v) for v in rect)
    h, w = img.shape[:2]
    if clip_rect:
        left, right = min(max(left, 0), w), min(max(right, 0), w)
        top, bottom = min(max(top, 0), h), min(max(bottom, 0), h)
    if not (0 <= left < right <= w and 0 <= top < bottom <= h):
        raise FocusMetricError(f"IMAQ Extract 2: rectangle {[int(v) for v in rect]} empty or outside the "
                               f"{w}x{h} image")
    return img[top:bottom, left:right]


def resample_nearest(img: np.ndarray, x_res: int, y_res: int, phase: str = "corner") -> np.ndarray:
    """``IMAQ Resample``, Zero Order (§4.3): ``S(y, x) = R(sy(y), sx(x))``, no averaging, no anti-aliasing (F2).

    ``phase`` selects the source pixel (integer arithmetic is the reference; indices clamped to ``W − 1``):
    ``"corner"`` ``floor(x·W/X)`` (provisional default), ``"corner_round"`` ``floor(x·W/X + ½)``, ``"center"``
    ``floor((x + ½)·W/X)``. A size ≤ 0 raises :class:`FocusMetricError` (resample error, score 0: §4.2, F13).
    ``X > W`` up-samples by pixel replication."""
    if x_res <= 0 or y_res <= 0:
        raise FocusMetricError(f"IMAQ Resample: invalid size {x_res}x{y_res}")
    h, w = img.shape[:2]
    xd = np.arange(x_res, dtype=np.int64)
    yd = np.arange(y_res, dtype=np.int64)
    if phase == "corner":
        xs, ys = xd * w // x_res, yd * h // y_res
    elif phase == "corner_round":
        xs, ys = (2 * xd * w + x_res) // (2 * x_res), (2 * yd * h + y_res) // (2 * y_res)
    elif phase == "center":
        xs, ys = (2 * xd + 1) * w // (2 * x_res), (2 * yd + 1) * h // (2 * y_res)
    else:
        raise ValueError(f"unknown resample phase {phase!r} (one of {', '.join(PHASES)})")
    return img[np.ix_(np.minimum(ys, h - 1), np.minimum(xs, w - 1))]


def subsample(img: np.ndarray, rect: Sequence[int] | None, scale: float, phase: str = "corner",
              clip_rect: bool = False) -> np.ndarray:
    """The common front end of both metric VIs (§4): ROI (§4.1), then nearest-neighbour resampling to
    ``rint(W_R·s) × rint(H_R·s)`` with ties to even (§4.2: 1002 × 0.25 = 250.5 → 250, 1006 × 0.25 → 252)."""
    if img.ndim != 2:
        raise FocusMetricError(f"focus metric: grayscale (2-D) image expected, got shape {img.shape}")
    roi = extract_roi(img, rect, clip_rect)
    h, w = roi.shape
    return resample_nearest(roi, lv_to_i32(w * scale), lv_to_i32(h * scale), phase)


# ================================================================================================== FFT metric (§5)
def focus_fft(img: np.ndarray, rect: Sequence[int] | None = (), scale: float = 0.25, *, phase: str = "corner",
              clip_rect: bool = False, sequential_f32: bool = False) -> float:
    """``alox_vision_measure_focus_fft.vi`` (§5.1–§5.2): the sum of the magnitudes of the unnormalised forward 2-D DFT
    of the resampled ROI over the **whole plane, DC included** (F1: a constant image ``c`` gives ``c·X·Y``). No window,
    no padding, no scaling (inferred, A7); the dead ``First Quadrant?`` option is not implemented (§5.3).

    The magnitude plane is SGL (``float32``) and its sum is SGL (§8): by default an accurate sum rounded to float32;
    ``sequential_f32`` emulates a running float32 accumulator (NI's accumulator is unknown, A8). Default ``scale``
    0.25 is the VI default (§3.1)."""
    s = subsample(img, rect, scale, phase, clip_rect)
    mag = np.abs(np.fft.fft2(s.astype(np.float64))).astype(np.float32)      # Magnitude plane, SGL
    if sequential_f32:
        total = np.cumsum(mag.ravel(), dtype=np.float32)[-1]
    else:
        total = np.float32(mag.sum(dtype=np.float64))
    return float(total)                                                    # SGL → DBL (exact)


# ================================================================================================== edge metric (§6)
def _saturate(out: np.ndarray, dtype: np.dtype) -> np.ndarray:
    """NI saturation to the image type (§6.4): non-integer results rounded half to even (Sigma, LowPass; inferred),
    then clipped to the integer range (U8: 0–255)."""
    dt = np.dtype(dtype)
    if dt.kind in "ui":
        info = np.iinfo(dt)
        if out.dtype.kind == "f":
            out = np.rint(out)
        return np.clip(out, info.min, info.max).astype(dt)
    return out.astype(dt)


def ni_edge(img: np.ndarray, method: str, border: str = "edge") -> np.ndarray:
    """``IMAQ EdgeDetection`` with Threshold 0 and no mask (§6.1, §6.3; formulas from NI's documentation, inferred).

    With ``p`` the 3×3 neighbourhood (``up`` = row above, ``ul`` = up-left, …):

    - Differentiation (2×2): ``max(|left − p|, |ul − p|, |up − p|)``;
    - Gradient (2×2): ``max(|up − p|, |ul − left|)`` — vertical variations only (horizontal edges, A3);
    - Roberts (2×2): ``max(|ul − p|, |up − left|)``;
    - Prewitt / Sobel (3×3): ``max(|K1⋆p|, |K2⋆p|)`` with ``K1 = [[−1,0,1],[−k,0,k],[−1,0,1]]``, ``K2 = K1ᵀ``,
      ``k = 1`` (Prewitt) or 2 (Sobel): the **maximum** of the two absolute responses, not a magnitude;
    - Sigma (3×3, least certain, A5): ``M``, ``S`` = mean and population standard deviation of the block (centre
      included); ``M`` if ``|p − M| ≤ S`` else ``p`` — a smoothing, hence a brightness-like score (§7.3, F10).

    The result has the input dtype, saturated (Sobel/Prewitt exceed 255 on strong edges, §6.4). ``border`` is the fill
    of the one-pixel frame (§6.5): ``"edge"`` (replicate, provisional default), ``"reflect"`` or ``"constant"`` (0)."""
    if method not in EDGE_METHODS:
        raise ValueError(f"unknown edge detection method {method!r} (one of {', '.join(EDGE_METHODS)})")
    if border not in BORDERS:
        raise ValueError(f"unknown border {border!r} (one of {', '.join(BORDERS)})")
    integer = img.dtype.kind in "ui"
    a = img.astype(np.int64 if integer and method != "Sigma" else np.float64)
    p = np.pad(a, 1, mode=border)
    c = p[1:-1, 1:-1]
    up, down, left, right = p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]
    ul, ur, dl, dr = p[:-2, :-2], p[:-2, 2:], p[2:, :-2], p[2:, 2:]
    if method == "Differentiation":
        out = np.maximum.reduce([np.abs(left - c), np.abs(ul - c), np.abs(up - c)])
    elif method == "Gradient":
        out = np.maximum(np.abs(up - c), np.abs(ul - left))
    elif method == "Roberts":
        out = np.maximum(np.abs(ul - c), np.abs(up - left))
    elif method in ("Prewitt", "Sobel"):
        k = 1 if method == "Prewitt" else 2
        gx = (ur + k * right + dr) - (ul + k * left + dl)
        gy = (dl + k * down + dr) - (ul + k * up + ur)
        out = np.maximum(np.abs(gx), np.abs(gy))
    else:                                                                  # Sigma
        stack = np.stack([ul, up, ur, left, c, right, dl, down, dr])
        m, sd = stack.mean(axis=0), stack.std(axis=0)                     # population std (inferred)
        out = np.where(np.abs(c - m) <= sd, m, c)
    return _saturate(out, img.dtype)


def histogram_mean(img: np.ndarray) -> np.float32:
    """``IMAQ Histogram`` → ``Mean Value`` (6th element of the report) as SGL: the arithmetic mean of every pixel, no
    mask (§6.6). For U8 the 256 classes of width 1 cannot bias it; the 16-bit definition is unknown (A6)."""
    return np.float32(img.sum(dtype=np.float64) / img.size)


def focus_edge(img: np.ndarray, rect: Sequence[int] | None = (), scale: float = 0.25, method: str = "Sobel",
               full: bool = False, *, phase: str = "corner", border: str = "edge", clip_rect: bool = False) -> float:
    """``alox_vision_measure_focus_sobel.vi`` (§6.1–§6.2): the float32 mean of the edge image of ``method``; with
    ``full``, the product of the six means in the order Differentiation, Gradient, Prewitt, Roberts, Sigma, Sobel, each
    partial product rounded to float32 (``method`` is then ignored). LabVIEW runs the six FULL branches in parallel on
    one shared image (race, §6.7); here they run sequentially on separate arrays: the intended product (F4).
    Defaults ``scale = 0.25``, ``method = "Sobel"``, ``full = False`` are the VI defaults (§3.2; FULL inferred, Q30)."""
    s = subsample(img, rect, scale, phase, clip_rect)
    if not full:
        return float(histogram_mean(ni_edge(s, method, border)))
    prod = np.float32(1.0)
    for m in EDGE_METHODS:
        prod = np.float32(prod * histogram_mean(ni_edge(s, m, border)))
    return float(prod)


# ================================================================================================== LowPass (§7.2)
def ni_lowpass(img: np.ndarray, x_size: int = 3, y_size: int = 3, tolerance_pct: float = 40.0,
               border: str = "edge", include_center: bool = True) -> np.ndarray:
    """``IMAQ LowPass`` — **working hypothesis only** (§7.2, formula unknown, A9): ``M`` = mean of the
    ``x_size × y_size`` neighbourhood (window anchor ``k // 2``, i.e. offsets −10…+9 for 20; centre included unless
    ``include_center`` is False); the pixel becomes ``M`` when ``|p − M| > tolerance%·M``, else it is kept; rounded and
    saturated to the image type. Returns a new array (never in place)."""
    if x_size < 1 or y_size < 1:
        raise FocusMetricError(f"IMAQ LowPass: invalid kernel size {x_size}x{y_size}")
    if border not in BORDERS:
        raise ValueError(f"unknown border {border!r} (one of {', '.join(BORDERS)})")
    n = x_size * y_size
    if n == 1 and not include_center:
        raise FocusMetricError("IMAQ LowPass: a 1x1 kernel without its centre is empty")
    a = img.astype(np.float64)
    ax, ay = x_size // 2, y_size // 2
    p = np.pad(a, ((ay, y_size - 1 - ay), (ax, x_size - 1 - ax)), mode=border)
    c = np.pad(p.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))            # integral image
    h, w = a.shape
    s = (c[y_size:y_size + h, x_size:x_size + w] - c[:h, x_size:x_size + w]
         - c[y_size:y_size + h, :w] + c[:h, :w])
    m = s / n if include_center else (s - a) / (n - 1)
    out = np.where(np.abs(a - m) > tolerance_pct / 100.0 * m, m, a)
    return _saturate(out, img.dtype)


# ================================================================================================== callers (06)
def autofocus_metric_method(method: str) -> str:
    """The autofocus ``Measure`` case structure (06 §4.5, §1.1 here): ``"FFT"`` and ``"Prewitt"`` keep their metric,
    **every other method** (Sobel, Diff, Gradient, Sigma, FULL) runs Sobel (``"Sobel", Default`` case)."""
    return method if method in ("FFT", "Prewitt") else "Sobel"


def fin_roi(width: int, height: int, fin_w: int, fin_h: int, *, low_mag: bool = False) -> list[int]:
    """The refocus "Fin" ROI ``[Left, Top, Right, Bottom]`` (06 §5.6), not clipped to the image.

    High Mag (``"High Mag.", Default``): a rectangle centred in the image, ``hw = rint(fin_w·0.5)``,
    ``hh = rint(fin_h·0.5)``, ``cx = rint(W·0.5)``, ``cy = rint(H·0.5)`` (half to even) → ``[cx−hw, cy−hh, cx+hw,
    cy+hh]``; e.g. 2448×2048 with 101×103 → ``[1174, 972, 1274, 1076]``. Low Mag: ``[fin_w, fin_h, W, H]``, **not
    centred** (reproduced as LabVIEW, D12, owner Q21)."""
    if low_mag:
        return [int(fin_w), int(fin_h), int(width), int(height)]
    hw, hh = lv_round(fin_w * 0.5), lv_round(fin_h * 0.5)
    cx, cy = lv_round(width * 0.5), lv_round(height * 0.5)
    return [cx - hw, cy - hh, cx + hw, cy + hh]


def measure(img: np.ndarray, method: str, roi: Sequence[int] | None, scale: float, *, phase: str = "corner",
            border: str = "edge", clip_rect: bool = False, sequential_f32: bool = False,
            sigma_lowpass: bool = True, include_center: bool = True) -> tuple[np.ndarray, float]:
    """The single entry point of both callers (§9.1 rule 4; the ``Metrics`` protocol of 06 §9.2): returns
    ``(image, score)``, where ``image`` is the input except for "Sigma" (a new low-pass-filtered array, §7.1).

    ``method`` is an ``Autofocus Method`` label (06 §5.7): ``"FFT"``; ``"Sobel"``, ``"Prewitt"``, ``"Diff"``,
    ``"Gradient"`` (edge metric, FULL False); ``"Sigma"`` (refocus only: ``IMAQ LowPass {20, 20, 40 %}`` on the
    **whole** image, then the Sigma edge metric; ``sigma_lowpass=False`` models NI rejecting the even 20×20 kernel,
    F9); ``"FULL"`` (refocus only: six-filter edge product on the ROI × FFT score of the **whole** image, F5, F7; an
    FFT error is propagated, F6). The autofocus maps its method with :func:`autofocus_metric_method` first. An unknown
    method raises :class:`ValueError` (the refocus case structure has no ``Default`` case)."""
    edge_kw = {"phase": phase, "border": border, "clip_rect": clip_rect}
    fft_kw = {"phase": phase, "clip_rect": clip_rect, "sequential_f32": sequential_f32}
    if method == "FFT":
        return img, focus_fft(img, roi, scale, **fft_kw)
    if method in REFOCUS_EDGE:
        return img, focus_edge(img, roi, scale, REFOCUS_EDGE[method], **edge_kw)
    if method == "Sigma":
        if sigma_lowpass:
            x_size, y_size, tolerance = SIGMA_LOWPASS
            img = ni_lowpass(img, x_size, y_size, tolerance, border, include_center)
        try:
            return img, focus_edge(img, roi, scale, "Sigma", **edge_kw)
        except FocusMetricError as exc:
            exc.image = img                       # LabVIEW keeps the filtered image even when the metric fails
            raise
    if method == "FULL":
        edge = focus_edge(img, roi, scale, full=True, **edge_kw)
        return img, edge * focus_fft(img, (), scale, **fft_kw)                 # DBL × DBL
    raise ValueError(f"unknown focus method {method!r} (one of {', '.join(METHODS)})")


@dataclass(frozen=True)
class NiCompatMetrics:
    """The ``Metrics`` object of 06 §9.2 (``measure(img, method, roi, scale) -> (image, score)``) with the NI behaviour
    parameters frozen (§9.1 rule 3, §10.5). The defaults are the provisional ones until the validation campaign."""

    phase: str = "corner"
    border: str = "edge"
    clip_rect: bool = False
    sequential_f32: bool = False
    sigma_lowpass: bool = True
    include_center: bool = True

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"unknown resample phase {self.phase!r} (one of {', '.join(PHASES)})")
        if self.border not in BORDERS:
            raise ValueError(f"unknown border {self.border!r} (one of {', '.join(BORDERS)})")

    def measure(self, img: np.ndarray, method: str, roi: Sequence[int] | None,
                scale: float) -> tuple[np.ndarray, float]:
        return measure(img, method, roi, scale, **asdict(self))


def measure_lv_style(img: np.ndarray, method: str, roi: Sequence[int] | None, scale: float,
                     **ni_params: object) -> tuple[np.ndarray, float, FocusMetricError | None]:
    """LabVIEW's contract of the metric VIs (§2, F11): on error the score is 0.0 and the error is returned with the
    image the caller keeps (the filtered one for "Sigma")."""
    try:
        out, score = measure(img, method, roi, scale, **ni_params)          # type: ignore[arg-type]
    except FocusMetricError as exc:
        return (img if exc.image is None else exc.image), 0.0, exc
    return out, score, None
