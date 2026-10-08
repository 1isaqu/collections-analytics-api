"""Write ``docs/openapi.json`` from the application. No database is needed.

python scripts/export_openapi.py            # rewrite the file
python scripts/export_openapi.py --check    # exit 1 if the file is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from collections_api.contract_doc import export_openapi, render_openapi_json  # noqa: E402

TARGET = ROOT / "docs" / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Do not write; fail if stale.")
    args = parser.parse_args()
    text = render_openapi_json(export_openapi())
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is stale. Run scripts/export_openapi.py.")
            return 1
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.relative_to(ROOT)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
