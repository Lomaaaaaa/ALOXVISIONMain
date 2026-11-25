"""The REST API of Vision Core on FastAPI, route for route (``docs/vision-core/map.md`` Part 5, Appendix A;
``rest-api-routes.md``).

Kept from LabVIEW (the supervisor depends on it): the paths under ``/alox/v1``, the arguments read from the query string
or a form body, the ``on``/other booleans and ``.`` decimals, the JSON keys and their string-formatted numbers, the PUT
replies as the localized plain text of a language-file code, and the status codes (200; 500 for errors including invalid
arguments; 503 when a function cannot start). ``abort`` stays a GET. No authentication, as in LabVIEW.

Fixed (owner's decisions, map §3.1): ``GET /v1/system/version`` only reads (LabVIEW also queued an Abort); the events
route gives the configured websocket port instead of a hard-coded 6123 (same value by default).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from alox.api.backend import ApiBackend, Measure, ReplyCode
from alox.core.commands import Method
from alox.core.functions import JobInfo

__all__ = ["EVENTS", "OVERLAYS", "create_app"]

log = logging.getLogger(__name__)

#: ``GET /v1/system/events`` list, as LabVIEW returns it (``error`` is sent but not listed, map Part 5 note 13).
EVENTS = ("heartbeat", "status", "measure_tilt", "measure_mesa", "measure_bowing", "finished", "started", "failed",
          "hardware_warning", "hardware_error", "hardware_reset", "mesa_status", "tilt_status", "bowing_status", "image")
#: Overlay names of ``/v1/camera/overlay/<name>`` (Appendix A.3).
OVERLAYS = ("highmagcamera", "lowmagcamera", "highmaginfos", "lowmaginfos", "highmagscale", "lowmagscale", "bowing",
            "tilt", "mesa", "oxyd", "target", "diameters", "edges", "autofocus", "refocus", "refocusoxyd",
            "detectcircle", "tracking", "wavelength", "detectobject", "autoexposure")
CAMERAS = {"highmag": "High Mag.", "lowmag": "Low Mag."}
LIVE_PAGE = """<html><head><title>ALOX live image</title></head><body>
<img id="live" src="{url}" width="{width}" height="{height}">
<script>
var img = document.getElementById("live");
setInterval(function () {{ img.src = "{url}&t=" + Date.now(); }}, 100);
</script></body></html>"""


class InvalidArguments(ValueError):
    """A request argument cannot be used (HTTP 500, code 009002)."""


# ================================================================================================== helpers
async def _params(request: Request) -> dict[str, str]:
    """Query string and, for a form body, its fields (LabVIEW ``Read All Form Data`` reads both)."""
    params = dict(request.query_params)
    if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        body = (await request.body()).decode("utf-8", "replace")
        params.update(parse_qsl(body, keep_blank_values=True))
    return params


def _float(params: Mapping[str, str], key: str) -> float | None:
    """A ``.``-decimal number, or None when absent; :class:`InvalidArguments` when malformed or not finite."""
    text = params.get(key)
    if text is None or text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        raise InvalidArguments(f"{key}={text!r}") from None
    if not math.isfinite(value):
        raise InvalidArguments(f"{key}={text!r}")
    return value


def _flag(params: Mapping[str, str], key: str) -> bool | None:
    """``on`` → TRUE, any other value → FALSE, absent → None (LabVIEW compares with ``on``)."""
    text = params.get(key)
    return None if text is None else text.strip().lower() == "on"


def _int(params: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    text = params.get(key)
    if text is None or text == "":
        return default
    try:
        return min(max(int(float(text)), lo), hi)
    except ValueError:
        raise InvalidArguments(f"{key}={text!r}") from None


def _job(params: Mapping[str, str]) -> JobInfo:
    return JobInfo(control_job_id=params.get("ctrljobid", ""), process_job_id=params.get("procjobid", ""),
                   lot_id=params.get("lotid", ""), wafer_id=params.get("waferid", ""),
                   zone_id=params.get("zoneid", ""))


def _number_text(value: float) -> str:
    """LabVIEW ``%f``: 6 decimals, ``.`` separator."""
    return f"{value:f}"


# ================================================================================================== application
def create_app(backend: ApiBackend, *, websocket_port: int = 6123, log_requests: bool = True) -> FastAPI:
    """The FastAPI application serving ``/alox/v1/...`` on ``backend``."""
    app = FastAPI(title="ALOX Vision Core API", docs_url=None, redoc_url=None, openapi_url=None)

    def reply(code: ReplyCode, status: int = 200) -> PlainTextResponse:
        try:
            text = backend.text(int(code))
        except Exception:                                   # noqa: BLE001 - a missing text must not break the reply
            text = f"{int(code):06d}"
        return PlainTextResponse(text, status_code=status)

    def route(method: str, path: str) -> Callable[[Callable[..., Awaitable[Response]]], Any]:
        """Register ``path`` under ``/alox``; any exception → 500 with the code text (LabVIEW error case)."""
        def decorator(handler: Callable[..., Awaitable[Response]]) -> Any:
            async def wrapper(request: Request) -> Response:
                if log_requests:
                    log.info("api '%s' (%s) received='%s'", path, method.lower(), request.url.query)
                try:
                    return await handler(request)
                except InvalidArguments as exc:
                    log.warning("api %s %s: invalid arguments: %s", method, path, exc)
                    return reply(ReplyCode.INVALID_ARGUMENTS, 500)
                except Exception:
                    log.exception("api %s %s failed", method, path)
                    return reply(ReplyCode.UNKNOWN_ERROR, 500)
            app.add_api_route("/alox" + path, wrapper, methods=[method], include_in_schema=False)
            return handler
        return decorator

    def queue(command: Method, payload: Any = None) -> PlainTextResponse:
        backend.post(command, payload)
        return reply(ReplyCode.NO_ERROR)

    # ---------------------------------------------------------------------------------------- system
    @route("GET", "/v1/system/status")
    async def system_status(_r: Request) -> Response:
        code, description = backend.system_status()
        return JSONResponse({"status": int(code), "description": description})

    for measure in Measure:
        def make(m: Measure) -> Callable[[Request], Awaitable[Response]]:
            async def status(_r: Request) -> Response:
                code, description = backend.measure_status(m)
                return JSONResponse({"status": int(code), "description": description})
            return status
        route("GET", f"/v1/system/{measure.name.lower()}status")(make(measure))

    @route("GET", "/v1/system/heartbeat")
    async def heartbeat(_r: Request) -> Response:
        return JSONResponse({"Heartbeat": str(int(backend.heartbeat()))})

    @route("GET", "/v1/system/heartbeatfreq")
    async def heartbeat_frequency(_r: Request) -> Response:
        return JSONResponse({"Frequency": str(backend.get_property("heartbeat.frequency", 1000))})

    @route("GET", "/v1/system/heartbeatretries")
    async def heartbeat_retries(_r: Request) -> Response:
        return JSONResponse({"Retries": str(backend.get_property("general.watchdogmaxfails", 3))})

    @route("GET", "/v1/watchdog/visibility")
    async def watchdog_visibility(_r: Request) -> Response:
        return JSONResponse({"visible": bool(backend.get_property("general.watchdogui", False))})

    @route("GET", "/v1/system/version")
    async def version(_r: Request) -> Response:
        v = backend.version()
        return JSONResponse({"major": v.major, "minor": v.minor, "build": v.build, "api": v.api,
                             "release": v.release})

    @route("GET", "/v1/system/mode")
    async def get_mode(_r: Request) -> Response:
        mode = int(backend.get_property("general.mode", 0))
        return JSONResponse({"mode": "remote" if mode else "local"})

    @route("PUT", "/v1/system/mode")
    async def put_mode(request: Request) -> Response:
        kind = (await _params(request)).get("type", "").strip().lower()
        if kind not in ("local", "remote"):
            raise InvalidArguments(f"type={kind!r}")
        backend.set_property("general.mode", 1 if kind == "remote" else 0)
        return queue(Method.REFRESH_CORE_INFOS)

    @route("GET", "/v1/system/profile")
    async def get_profile(_r: Request) -> Response:
        path, filename = backend.current_profile()
        return JSONResponse({"path": path, "filename": filename})

    @route("GET", "/v1/system/profiles")
    async def get_profiles(_r: Request) -> Response:
        return JSONResponse({"profiles": list(backend.profiles())})

    @route("PUT", "/v1/system/profile")
    async def put_profile(request: Request) -> Response:
        name = (await _params(request)).get("filename", "")
        if not name or name not in backend.profiles():
            raise InvalidArguments(f"filename={name!r}")
        return queue(Method.LOAD_PROFILE, name)

    @route("GET", "/v1/system/configuration")
    async def get_configuration(_r: Request) -> Response:
        path = backend.profiles_folder()
        if not path:
            return reply(ReplyCode.UNKNOWN_ERROR, 500)
        return JSONResponse({"path": path})

    @route("PUT", "/v1/system/configuration")
    async def put_configuration(request: Request) -> Response:
        path = (await _params(request)).get("path", "")
        if not path:
            raise InvalidArguments("path missing")
        backend.set_profiles_folder(path)
        return queue(Method.PROFILES_FOLDER_CHANGED)

    @route("GET", "/alox_api_v1_get_system_imagesfolder/")
    async def images_folder(_r: Request) -> Response:
        return JSONResponse({"path": backend.images_folder()})

    @route("GET", "/v1/system/events")
    async def events(_r: Request) -> Response:
        return JSONResponse({"url": f"ws://{backend.hostname()}:{websocket_port}", "events": list(EVENTS)})

    @route("GET", "/v1/system/tilt")
    async def tilt(_r: Request) -> Response:
        return JSONResponse({"Tilt": _number_text(backend.tilt()), "Units": "degrees"})

    @route("PUT", "/v1/system/tiltreset")
    async def tilt_reset(_r: Request) -> Response:
        backend.reset_tilt()
        return reply(ReplyCode.NO_ERROR)

    @route("PUT", "/v1/system/reset")
    async def system_reset(_r: Request) -> Response:
        return queue(Method.SYSTEM_RESET)

    @route("PUT", "/v1/system/shutdown")
    async def system_shutdown(_r: Request) -> Response:
        return queue(Method.SYSTEM_SHUTDOWN)

    @route("PUT", "/v1/system/showui")
    async def show_ui(request: Request) -> Response:
        visible = (await _params(request)).get("visible", "").strip().lower() in ("true", "on")
        return queue(Method.SHOW_UI, visible)

    # ---------------------------------------------------------------------------------------- functions
    async def measure(request: Request, which: Measure, keys: dict[str, str]) -> Response:
        if not backend.can_start(which):
            backend.not_ready(which)
            return reply(ReplyCode.NOT_READY, 503)
        params = await _params(request)
        overrides: dict[str, Any] = {}
        for key, kind in keys.items():
            value = _float(params, key) if kind == "float" else _flag(params, key)
            if value is not None:
                overrides[key] = value
        if "indexID" in params:
            overrides["indexID"] = params["indexID"]
        profile = params.get("profile")
        if profile:
            if profile not in backend.profiles():
                raise InvalidArguments(f"profile={profile!r}")
            backend.post(Method.LOAD_PROFILE, profile)
        payload = backend.measure_request(which, _job(params), overrides)
        return queue(Method(int(which)), payload)

    @route("PUT", "/v1/functions/measuremesa")
    async def measure_mesa(request: Request) -> Response:
        return await measure(request, Measure.MESA, {"x": "float", "y": "float", "target": "float", "tilt": "flag",
                                                     "singleshot": "flag", "positionsctrl": "flag",
                                                     "analysis": "flag"})

    @route("PUT", "/v1/functions/measuretilt")
    async def measure_tilt(request: Request) -> Response:
        return await measure(request, Measure.TILT, {"x": "float", "y": "float"})

    @route("PUT", "/v1/functions/measurebowing")
    async def measure_bowing(request: Request) -> Response:
        return await measure(request, Measure.BOWING, {"x": "float"})

    @route("PUT", "/v1/functions/stop")
    async def stop(_r: Request) -> Response:
        return queue(Method.STOP)

    @route("GET", "/v1/functions/abort")
    async def abort(_r: Request) -> Response:
        return queue(Method.ABORT)

    # ---------------------------------------------------------------------------------------- cameras
    for short, camera_type in CAMERAS.items():
        def make_status(name: str) -> Callable[[Request], Awaitable[Response]]:
            async def status(_r: Request) -> Response:
                return JSONResponse(dict(backend.camera_status(name)))
            return status

        def make_grab(kind: str) -> Callable[[Request], Awaitable[Response]]:
            async def grab(_r: Request) -> Response:
                if not backend.can_start(None):
                    return reply(ReplyCode.GET_IMAGE_NOT_READY, 503)
                return queue(Method.GET_IMAGE, kind)
            return grab

        route("GET", f"/v1/camera/{short}status")(make_status(short))
        route("PUT", f"/v1/camera/grab{short}")(make_grab(camera_type))

    def image_size(params: Mapping[str, str], width: int, height: int) -> tuple[int, int, int]:
        return (_int(params, "quality", 750, 0, 1000), _int(params, "width", width, 1, 10000),
                _int(params, "height", height, 1, 10000))

    @route("GET", "/v1/camera/jpegimage")
    async def jpeg_image(request: Request) -> Response:
        quality, width, height = image_size(await _params(request), 612, 512)
        try:
            data = backend.jpeg(quality, width, height)
        except Exception:
            log.exception("jpeg image failed")
            data = b""
        return Response(data, media_type="image/jpeg")

    @route("GET", "/v1/camera/liveimage")
    async def live_image(request: Request) -> Response:
        quality, width, height = image_size(await _params(request), 640, 480)
        url = f"/alox/v1/camera/jpegimage?quality={quality}&width={width}&height={height}"
        return Response(LIVE_PAGE.format(url=url, width=width, height=height),
                        media_type="text/html; charset=us-ascii")

    @route("PUT", "/v1/camera/streamstart")
    async def stream_start(request: Request) -> Response:
        backend.stream_start(*image_size(await _params(request), 612, 512))
        return reply(ReplyCode.NO_ERROR)

    @route("PUT", "/v1/camera/streamstop")
    async def stream_stop(_r: Request) -> Response:
        backend.stream_stop()
        return reply(ReplyCode.NO_ERROR)

    @route("GET", "/v1/camera/overlays")
    async def overlays(_r: Request) -> Response:
        return JSONResponse({"overlays": list(backend.overlays())})

    for name in OVERLAYS:
        def make_get(overlay: str) -> Callable[[Request], Awaitable[Response]]:
            async def get(_r: Request) -> Response:
                return JSONResponse({overlay: "on" if backend.overlay(overlay) else "off"})
            return get

        def make_put(overlay: str) -> Callable[[Request], Awaitable[Response]]:
            async def put(request: Request) -> Response:
                state = (await _params(request)).get("state", "").strip().lower()
                if state not in ("on", "off"):
                    raise InvalidArguments(f"state={state!r}")
                backend.set_overlay(overlay, state == "on")
                return reply(ReplyCode.NO_ERROR)
            return put

        route("GET", f"/v1/camera/overlay/{name}")(make_get(name))
        route("PUT", f"/v1/camera/overlay/{name}")(make_put(name))

    return app
