"""Fetch/verify pinned provisioning protocol facts without vendoring provider source."""

from __future__ import annotations

import argparse

from tools._common import ROOT, read_json
from tools._references import verify_primary_references


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    count = verify_primary_references(
        [read_json(ROOT / "manifests/provisioning-profile.json")],
        ROOT / "build/provisioning-refs",
        download=not args.verify,
    )
    print(f"Provisioning primary references verified: {count} files")


if __name__ == "__main__":
    main()
