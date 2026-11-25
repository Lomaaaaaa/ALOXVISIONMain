# Vision Core: migration source material

Material extracted from the Vision Core LabVIEW sources (version folder `V1.2.6.2`) for its future migration. The camera server
specification in the parent folder covers only `alox_camera_server.vi`; this folder covers the whole application.

| File | Content |
|---|---|
| [typedefs.md](typedefs.md) | All 352 `.ctl` typedefs decoded to text: enum items with their numeric values, cluster fields with their types, refnum kinds. |
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
(`alox.lvproj`, INI files, Python/C code, translations) were received during the analysis session but are not committed:
they are large, and `alox_default.ini` contains user passwords in clear text.
