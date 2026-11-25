# Vision Core REST API routes

Extracted from `alox.lvws` (the built LabVIEW web service of Vision Core V1.2.6.2): `WebService.ini` and `WebService.lvwsdsc`.

- Service name: `alox`, type `G_REST`, standalone `true`, LabVIEW 18.0.1f4 (Windows x64).
- 80 routing templates; 44 GET, 36 PUT.
- No route requires an API key or a permission (`RequireAPIKey=false`, `Permissions=` empty), including `shutdown` and `reset`.
- Response mode: "Stream" means the VI writes the HTTP body itself (JKI JSON serializer is bundled); "Terminal" means LabVIEW formats the VI outputs (format XML).
- `abort` is a GET while `stop` is a PUT.
- Naming: every `highmag` route is served by a `lowangle` VI and every `lowmag` route by a `highangle` VI (consistent across all 16 camera routes). The routes were renamed from angle to magnification; the VIs kept the old names.

| Method | Route | VI | Response mode |
|---|---|---|---|
| GET | `/` | (static `/Public`) |  |
| GET | `/alox_api_v1_get_system_imagesfolder/` | `alox_api_v1_get_system_imagesfolder.vi` | Terminal |
| PUT | `/v1/camera/grabhighmag` | `alox_api_v1_put_camera_grablowangle.vi` | Stream |
| PUT | `/v1/camera/grablowmag` | `alox_api_v1_put_camera_grabhighangle.vi` | Stream |
| GET | `/v1/camera/highmagstatus` | `alox_api_v1_get_lowangle_camera_status.vi` | Stream |
| GET | `/v1/camera/jpegimage` | `alox_api_v1_get_camera_jpegimage.vi` | Stream |
| GET | `/v1/camera/liveimage` | `alox_api_v1_get_camera_liveimage.vi` | Stream |
| GET | `/v1/camera/lowmagstatus` | `alox_api_v1_get_highangle_camera_status.vi` | Stream |
| GET | `/v1/camera/overlay/autoexposure` | `alox_api_v1_get_camera_overlay_autoexposure.vi` | Stream |
| PUT | `/v1/camera/overlay/autoexposure` | `alox_api_v1_put_camera_overlay_autoexposure.vi` | Stream |
| GET | `/v1/camera/overlay/autofocus` | `alox_api_v1_get_camera_overlay_autofocus.vi` | Stream |
| PUT | `/v1/camera/overlay/autofocus` | `alox_api_v1_put_camera_overlay_autofocus.vi` | Stream |
| GET | `/v1/camera/overlay/bowing` | `alox_api_v1_get_camera_overlay_bowing.vi` | Stream |
| PUT | `/v1/camera/overlay/bowing` | `alox_api_v1_put_camera_overlay_bowing.vi` | Stream |
| GET | `/v1/camera/overlay/detectcircle` | `alox_api_v1_get_camera_overlay_detectcircle.vi` | Stream |
| PUT | `/v1/camera/overlay/detectcircle` | `alox_api_v1_put_camera_overlay_detectcircle.vi` | Stream |
| GET | `/v1/camera/overlay/detectobject` | `alox_api_v1_get_camera_overlay_detectobject.vi` | Stream |
| PUT | `/v1/camera/overlay/detectobject` | `alox_api_v1_put_camera_overlay_detectobject.vi` | Stream |
| GET | `/v1/camera/overlay/diameters` | `alox_api_v1_get_camera_overlay_diameters.vi` | Stream |
| PUT | `/v1/camera/overlay/diameters` | `alox_api_v1_put_camera_overlay_diameters.vi` | Stream |
| GET | `/v1/camera/overlay/edges` | `alox_api_v1_get_camera_overlay_edges.vi` | Stream |
| PUT | `/v1/camera/overlay/edges` | `alox_api_v1_put_camera_overlay_edges.vi` | Stream |
| GET | `/v1/camera/overlay/highmagcamera` | `alox_api_v1_get_lowangle_camera_overlay_camera.vi` | Stream |
| PUT | `/v1/camera/overlay/highmagcamera` | `alox_api_v1_put_lowangle_camera_overlay_camera.vi` | Stream |
| GET | `/v1/camera/overlay/highmaginfos` | `alox_api_v1_get_lowangle_camera_overlay_infos.vi` | Stream |
| PUT | `/v1/camera/overlay/highmaginfos` | `alox_api_v1_put_lowangle_camera_overlay_infos.vi` | Stream |
| GET | `/v1/camera/overlay/highmagscale` | `alox_api_v1_get_lowangle_camera_overlay_scale.vi` | Stream |
| PUT | `/v1/camera/overlay/highmagscale` | `alox_api_v1_put_lowangle_camera_overlay_scale.vi` | Stream |
| GET | `/v1/camera/overlay/lowmagcamera` | `alox_api_v1_get_highangle_camera_overlay_camera.vi` | Stream |
| PUT | `/v1/camera/overlay/lowmagcamera` | `alox_api_v1_put_highangle_camera_overlay_camera.vi` | Stream |
| GET | `/v1/camera/overlay/lowmaginfos` | `alox_api_v1_get_highangle_camera_overlay_infos.vi` | Stream |
| PUT | `/v1/camera/overlay/lowmaginfos` | `alox_api_v1_put_highangle_camera_overlay_infos.vi` | Stream |
| GET | `/v1/camera/overlay/lowmagscale` | `alox_api_v1_get_highangle_camera_overlay_scale.vi` | Stream |
| PUT | `/v1/camera/overlay/lowmagscale` | `alox_api_v1_put_highangle_camera_overlay_scale.vi` | Stream |
| GET | `/v1/camera/overlay/mesa` | `alox_api_v1_get_camera_overlay_mesa.vi` | Stream |
| PUT | `/v1/camera/overlay/mesa` | `alox_api_v1_put_camera_overlay_mesa.vi` | Stream |
| GET | `/v1/camera/overlay/oxyd` | `alox_api_v1_get_camera_overlay_oxyd.vi` | Stream |
| PUT | `/v1/camera/overlay/oxyd` | `alox_api_v1_put_camera_overlay_oxyd.vi` | Stream |
| GET | `/v1/camera/overlay/refocus` | `alox_api_v1_get_camera_overlay_refocus.vi` | Stream |
| PUT | `/v1/camera/overlay/refocus` | `alox_api_v1_put_camera_overlays_refocus.vi` | Stream |
| GET | `/v1/camera/overlay/refocusoxyd` | `alox_api_v1_get_camera_overlay_refocus_oxyd.vi` | Stream |
| PUT | `/v1/camera/overlay/refocusoxyd` | `alox_api_v1_put_camera_overlay_refocusoxyd.vi` | Stream |
| GET | `/v1/camera/overlay/target` | `alox_api_v1_get_camera_overlay_target.vi` | Stream |
| PUT | `/v1/camera/overlay/target` | `alox_api_v1_put_camera_overlay_target.vi` | Stream |
| GET | `/v1/camera/overlay/tilt` | `alox_api_v1_get_camera_overlay_tilt.vi` | Stream |
| PUT | `/v1/camera/overlay/tilt` | `alox_api_v1_put_camera_overlay_tilt.vi` | Stream |
| GET | `/v1/camera/overlay/tracking` | `alox_api_v1_get_camera_overlay_tracking.vi` | Stream |
| PUT | `/v1/camera/overlay/tracking` | `alox_api_v1_put_camera_overlay_tracking.vi` | Stream |
| GET | `/v1/camera/overlay/wavelength` | `alox_api_v1_get_camera_overlay_wavelength.vi` | Stream |
| PUT | `/v1/camera/overlay/wavelength` | `alox_api_v1_put_camera_overlay_wavelength.vi` | Stream |
| GET | `/v1/camera/overlays` | `alox_api_v1_get_camera_overlays.vi` | Stream |
| PUT | `/v1/camera/streamstart` | `alox_api_v1_put_stream_image_start.vi` | Stream |
| PUT | `/v1/camera/streamstop` | `alox_api_v1_put_stream_image_stop.vi` | Stream |
| GET | `/v1/functions/abort` | `alox_api_v1_put_functions_abort.vi` | Terminal |
| PUT | `/v1/functions/measurebowing` | `alox_api_v1_put_functions_measure_bowing.vi` | Stream |
| PUT | `/v1/functions/measuremesa` | `alox_api_v1_put_functions_measure_mesa.vi` | Stream |
| PUT | `/v1/functions/measuretilt` | `alox_api_v1_put_functions_measure_tilt.vi` | Stream |
| PUT | `/v1/functions/stop` | `alox_api_v1_put_functions_stop.vi` | Stream |
| GET | `/v1/system/bowingstatus` | `alox_api_v1_get_system_bowing_status.vi` | Stream |
| GET | `/v1/system/configuration` | `alox_api_v1_get_system_configuration.vi` | Stream |
| PUT | `/v1/system/configuration` | `alox_api_v1_put_system_configuration.vi` | Stream |
| GET | `/v1/system/events` | `alox_api_v1_get_system_events.vi` | Stream |
| GET | `/v1/system/heartbeat` | `alox_api_v1_get_system_heartbeat.vi` | Stream |
| GET | `/v1/system/heartbeatfreq` | `alox_api_v1_get_system_heartbeat_frequency.vi` | Stream |
| GET | `/v1/system/heartbeatretries` | `alox_api_v1_get_system_heartbeat_retries.vi` | Stream |
| GET | `/v1/system/mesastatus` | `alox_api_v1_get_system_mesa_status.vi` | Stream |
| GET | `/v1/system/mode` | `alox_api_v1_get_system_mode.vi` | Stream |
| PUT | `/v1/system/mode` | `alox_api_v1_put_system_mode.vi` | Stream |
| GET | `/v1/system/profile` | `alox_api_v1_get_system_profile.vi` | Stream |
| PUT | `/v1/system/profile` | `alox_api_v1_put_system_profile.vi` | Stream |
| GET | `/v1/system/profiles` | `alox_api_v1_get_system_profiles.vi` | Stream |
| PUT | `/v1/system/reset` | `alox_api_v1_put_system_reset.vi` | Stream |
| PUT | `/v1/system/showui` | `alox_api_v1_put_system_showui.vi` | Stream |
| PUT | `/v1/system/shutdown` | `alox_api_v1_put_system_shutdown.vi` | Stream |
| GET | `/v1/system/status` | `alox_api_v1_get_system_status.vi` | Stream |
| GET | `/v1/system/tilt` | `alox_api_v1_get_system_tilt.vi` | Stream |
| PUT | `/v1/system/tiltreset` | `alox_api_v1_put_system_tiltreset.vi` | Stream |
| GET | `/v1/system/tiltstatus` | `alox_api_v1_get_system_tilt_status.vi` | Stream |
| GET | `/v1/system/version` | `alox_api_v1_get_system_version.vi` | Stream |
| GET | `/v1/watchdog/visibility` | `alox_api_v1_get_watchdog_visibility.vi` | Stream |
