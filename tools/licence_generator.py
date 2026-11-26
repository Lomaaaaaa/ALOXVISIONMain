#!/usr/bin/env python3
r"""Aloxtec's licence generator for ALOX Vision (owner's decision L2; the licence itself: ``alox.licence``).

Back-office use only: the private key never goes to a customer PC.

1. Once: create the key pair, keep ``licence_private_key.pem`` safe (it signs every licence) and build the application
   with the printed public key (``alox.licence.PUBLIC_KEY``, or a ``licence_public_key.txt`` file next to the
   executable)::

       py -3 tools\licence_generator.py keygen --out D:\aloxtec\licence-keys

2. On the customer PC, read its identity (a hash; it reveals nothing about the PC)::

       py -3 tools\licence_generator.py machine-id

3. Issue the licence for that identity (``--expires`` for a time-limited or demo licence) and copy the file to the
   application folder as ``licence.alox``; then set ``alox.ini [general] licence_BOOL = TRUE``::

       py -3 tools\licence_generator.py issue --key D:\aloxtec\licence-keys\licence_private_key.pem
           --machine <id> --customer "Fab 1, tool 3" --expires 2027-12-31 --out licence.alox

4. ``verify`` checks a licence file with the public key, on any PC (``--machine`` to check for another PC).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import sys
from pathlib import Path

from alox.licence import LicenceError, machine_id, sign_licence, verify_licence


def keygen(out: Path) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out.mkdir(parents=True, exist_ok=True)
    private_path = out / "licence_private_key.pem"
    if private_path.exists():
        print(f"{private_path} exists: not overwritten (every licence issued with it would become useless)")
        return 1
    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                            serialization.PublicFormat.Raw)).decode("ascii")
    (out / "licence_public_key.txt").write_text(public + "\n", encoding="ascii")
    print(f"private key: {private_path}  (keep it secret and backed up)")
    print(f"public key:  {public}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ALOX Vision licence generator (Aloxtec back office).")
    sub = p.add_subparsers(dest="command", required=True)
    k = sub.add_parser("keygen", help="create the signing key pair")
    k.add_argument("--out", type=Path, required=True)
    sub.add_parser("machine-id", help="print this PC's identity")
    i = sub.add_parser("issue", help="sign a licence for a PC")
    i.add_argument("--key", type=Path, required=True, help="the private key (PEM)")
    i.add_argument("--machine", required=True, help="the PC's identity (machine-id on that PC)")
    i.add_argument("--customer", required=True)
    i.add_argument("--expires", type=dt.date.fromisoformat, help="last valid day, YYYY-MM-DD (none: no expiry)")
    i.add_argument("--extra", default="", help="free text stored in the licence")
    i.add_argument("--out", type=Path, default=Path("licence.alox"))
    v = sub.add_parser("verify", help="check a licence file")
    v.add_argument("licence", type=Path)
    v.add_argument("--public-key", required=True, help="the public key (base64) or a file holding it")
    v.add_argument("--machine", help="the PC to check for (default: this PC)")
    args = p.parse_args(argv)

    if args.command == "keygen":
        return keygen(args.out)
    if args.command == "machine-id":
        try:
            print(machine_id())
        except LicenceError as exc:
            print(f"error: {exc}")
            return 1
        return 0
    if args.command == "issue":
        text = sign_licence(args.key.read_bytes(), customer=args.customer, machine=args.machine.strip(),
                            expires=args.expires, extra=args.extra)
        args.out.write_text(text, encoding="utf-8")
        print(f"licence written: {args.out}")
        return 0
    key = args.public_key
    if Path(key).is_file():
        key = Path(key).read_text(encoding="ascii").strip()
    status = verify_licence(args.licence.read_text(encoding="utf-8"), key, machine=args.machine or machine_id())
    print(f"{'valid' if status.valid else 'NOT valid'}: {status.reason}"
          + (f" (customer {status.customer})" if status.customer else "")
          + (f", expires {status.expires} ({status.remaining_days} days left)" if status.expires else ""))
    return 0 if status.valid else 1


if __name__ == "__main__":
    sys.exit(main())
