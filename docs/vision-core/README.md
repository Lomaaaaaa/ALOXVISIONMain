# Vision Core: migration source material

Material extracted from the Vision Core LabVIEW sources (version folder `V1.2.6.2`) for its future migration. The camera server
specification in the parent folder covers only `alox_camera_server.vi`; this folder covers the whole application.

| File | Content |
|---|---|
| [map.md](map.md) | **The map of Vision Core**: programs, processes and ports, architecture of the main application, hardware layer and servers, the 13 measurement functions, shared libraries and formulas, REST/websocket API reference, thermal measurement, licensing, dead code, defects and decisions for the migration. |
| [typedefs.md](typedefs.md) | All 352 `.ctl` typedefs (plus the 10 of `Pyro/framework.llb`) decoded to text: enum items with their numeric values, cluster fields with their types, refnum kinds. |
| [call-graph.json](call-graph.json) | Direct dependencies (subVIs, typedefs) of each of the 759 VIs, including `Pyro/framework.llb`. |
| [rest-api-routes.md](rest-api-routes.md) | The 80 routes of the `alox` REST web service (method, URL, VI, response mode), from `alox.lvws`. |

## How the typedefs were obtained

1. A small LabVIEW VI opened each `.ctl`, read its control's value as a variant and wrote the `type string` of
   `Variant To Flattened String` (LabVIEW type descriptors, as 16-bit words) to one text file per typedef.
2. [`tools/vision_core/decode_typedefs.py`](../../tools/vision_core/decode_typedefs.py) decodes those files into `typedefs.md`:

   ```bash
   python tools/vision_core/decode_typedefs.py <folder of .txt dumps> docs/vision-core/typedefs.md
   ```

Check: the decoded Camera Command, TCP Datatype, Camera Type, Autofocus Speed, Autofocus Method and Refocus Method enums match
the camera server wire enums (`src/aloxcam/data/wire_enums.toml`) item for item. Camera Type decodes as an **enum U32**, which
confirms the network capture finding.

Limits: LabVIEW does not keep nested typedef names in this format (a cluster field that is itself a typedef shows its type, not
its typedef name). Some array elements and cluster fields have no label in LabVIEW; they show as `(no label)`.

## Not in the repository

The HTML export of the 747 VIs (one page per VI, every hidden case frame as an image; about 150 MB) and the text sources
(`alox.lvproj`, INI files, Python/C code, translations) were received during the analysis session but are not committed
yet (the HTML export is too large for the repository).
