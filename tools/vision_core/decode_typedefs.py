"""Decode LabVIEW type-descriptor dumps of the Vision Core typedefs into readable Markdown.

Input: a folder with one text file per ``.ctl``, named ``NNNN <name>.ctl.txt``. Each file holds the ``type string`` output of
``Variant To Flattened String`` (the type descriptor as signed 16-bit words, tab separated), written by a small LabVIEW VI.

A type descriptor is ``[U16 size][U8 flags][U8 type code][type data][label]``; flag 0x40 means a label (Pascal string) ends the
descriptor. Clusters and arrays embed their element descriptors inline.

Usage::

    python tools/vision_core/decode_typedefs.py <dump folder> <output .md>
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path
from typing import Any

NUMERIC = {0x01: "I8", 0x02: "I16", 0x03: "I32", 0x04: "I64", 0x05: "U8", 0x06: "U16", 0x07: "U32", 0x08: "U64",
           0x09: "SGL", 0x0A: "DBL", 0x0B: "EXT", 0x0C: "CSG", 0x0D: "CDB", 0x0E: "CXT"}
ENUM = {0x15: "enum U8", 0x16: "enum U16", 0x17: "enum U32"}
OPAQUE = {0x53: "Variant", 0x54: "Waveform", 0x70: "Refnum"}
HAS_LABEL = 0x40


def load(path: Path) -> bytes:
    """The descriptor bytes: each 16-bit word big-endian."""
    return b"".join(struct.pack(">h", int(word)) for word in path.read_text().split())


def pascal(b: bytes, pos: int) -> tuple[str, int]:
    """A Pascal string at ``pos`` and the position after it."""
    n = b[pos]
    return b[pos + 1:pos + 1 + n].decode("latin-1"), pos + 1 + n


def label_at(b: bytes, pos: int, end: int) -> str | None:
    """The label when it starts exactly at ``pos`` and ends the descriptor (one pad byte allowed)."""
    if pos < end and pos + 1 + b[pos] <= end:
        text, after = pascal(b, pos)
        if after in (end, end - 1) and all(ord(c) >= 32 for c in text):
            return text
    return None


def label_scan(b: bytes, start: int, end: int) -> str | None:
    """For descriptors whose type data is not parsed: the first Pascal string that ends the descriptor."""
    for pos in range(start, end):
        n = b[pos]
        if n and pos + 1 + n in (end, end - 1):
            text = b[pos + 1:pos + 1 + n].decode("latin-1")
            if all(32 <= ord(c) < 127 or ord(c) >= 160 for c in text):
                return text
    return None


def printable_runs(data: bytes) -> list[str]:
    """Distinct printable ASCII runs of 4+ characters (refnum class names such as ``IMAQdx``)."""
    return list(dict.fromkeys(m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e]{4,}", data)))


def parse(b: bytes, off: int) -> dict[str, Any]:
    """Parse the descriptor at ``off``. ``end`` in the result is the offset just after it."""
    size, flags, code = struct.unpack_from(">HBB", b, off)
    end = off + size
    node: dict[str, Any] = {"code": code, "end": end}
    pos: int | None = off + 4
    if code in NUMERIC:
        node["type"] = NUMERIC[code]
    elif code in ENUM:
        count = struct.unpack_from(">H", b, pos)[0]
        pos += 2
        items = []
        for _ in range(count):
            item, pos = pascal(b, pos)
            items.append(item)
        pos += pos % 2
        node.update(type=ENUM[code], items=items)
    elif code == 0x21:
        node["type"] = "Boolean"
    elif code in (0x30, 0x32):
        node["type"] = "String" if code == 0x30 else "Path"
        pos += 4                                   # I32 dimension (-1 = variable length)
    elif code == 0x40:
        dims = struct.unpack_from(">H", b, pos)[0]
        element = parse(b, pos + 2 + 4 * dims)
        pos = element["end"]
        node.update(type=f"{dims}D array", element=element)
    elif code == 0x50:
        count = struct.unpack_from(">H", b, pos)[0]
        pos += 2
        fields = []
        for _ in range(count):
            field = parse(b, pos)
            fields.append(field)
            pos = field["end"]
        node.update(type="Cluster", fields=fields)
    else:
        node["type"] = OPAQUE.get(code, f"type 0x{code:02X}")
        if code == 0x70:
            node["kind"] = struct.unpack_from(">H", b, pos)[0]
            node["hints"] = printable_runs(b[pos:end])
        pos = None
    if flags & HAS_LABEL:
        label = label_at(b, pos, end) if pos is not None else None
        node["label"] = label if label is not None else label_scan(b, off + 4, end)
        if code == 0x70 and node["label"]:
            node["hints"] = [h for h in node["hints"] if h not in node["label"]]
    return node


def render(node: dict[str, Any], depth: int, out: list[str]) -> None:
    pad = "  " * depth
    kind = node["type"]
    if node["code"] == 0x70:
        hints = f": {', '.join(node['hints'])}" if node["hints"] else ""
        kind += f" (kind {node['kind']}{hints})"
    out.append(f"{pad}- {node.get('label') or '(no label)'}: {kind}")
    for i, item in enumerate(node.get("items", [])):
        out.append(f"{pad}    {i} = {item}")
    if "element" in node:
        render(node["element"], depth + 1, out)
    for field in node.get("fields", []):
        render(field, depth + 1, out)


def main(src: Path, dst: Path) -> int:
    intro = ("One section per `.ctl`. Labels and enum items are exact. Nested typedef names are not kept by LabVIEW in "
             "this format.")
    out = ["# Vision Core typedefs (decoded from LabVIEW type descriptors)", "", intro, ""]
    errors = []
    for path in sorted(src.glob("*.txt")):
        name = re.sub(r"^\d+ ", "", path.name).removesuffix(".txt")
        try:
            node = parse(load(path), 0)
        except (struct.error, IndexError, ValueError) as exc:
            errors.append(f"{path.name}: {exc!r}")
            continue
        out += [f"## {name}", ""]
        render(node, 0, out)
        out.append("")
    dst.write_text("\n".join(out) + "\n")
    for error in errors:
        print("not decoded:", error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
