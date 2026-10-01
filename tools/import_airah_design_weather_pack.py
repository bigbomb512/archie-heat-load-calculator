#!/usr/bin/env python3
"""Administrator-only importer for an authorised AIRAH design-weather pack.

This tool intentionally has no HTTP route. Run it only on the protected server
with a licensed source export and a destination outside project review folders.
"""

import argparse
import json
import os
from pathlib import Path

from ai.site_design_weather_resolution import validate_licensed_airah_pack


def main():
    parser = argparse.ArgumentParser(description="Import a licensed AIRAH design-weather pack for Archie.")
    parser.add_argument("--source", required=True, help="Licensed structured source export (JSON).")
    parser.add_argument("--destination", required=True, help="Protected absolute server-side destination (JSON).")
    parser.add_argument("--replace", action="store_true", help="Replace an existing protected pack after validation.")
    args = parser.parse_args()
    source, destination = Path(args.source), Path(args.destination)
    protected_root = os.environ.get("ARCHIE_LICENSED_SOURCE_PACK_DIR", "").strip()
    if not destination.is_absolute() or not protected_root:
        raise SystemExit("Set ARCHIE_LICENSED_SOURCE_PACK_DIR and use an absolute destination beneath that protected server directory.")
    try:
        destination.resolve().relative_to(Path(protected_root).resolve())
    except ValueError as error:
        raise SystemExit("Destination must remain inside ARCHIE_LICENSED_SOURCE_PACK_DIR, outside project review workspaces.") from error
    if destination.exists() and not args.replace:
        raise SystemExit("Destination exists; pass --replace only after confirming the authorised new release.")
    checked = validate_licensed_airah_pack(json.loads(source.read_text(encoding="utf-8")))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(destination.name + ".stage")
    staged.write_text(json.dumps(checked, indent=2, ensure_ascii=True), encoding="utf-8")
    staged.replace(destination)
    print(f"Imported licensed AIRAH pack {checked['pack']['pack_id']} {checked['pack']['pack_version']} with {len(checked['pack']['records'])} records.")


if __name__ == "__main__":
    main()
