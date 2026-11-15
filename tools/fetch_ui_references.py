"""Fetch or verify immutable primary UI references; never execute or vendor their source."""

from __future__ import annotations

import argparse

from tools._common import ROOT, read_json
from tools._references import verify_primary_references


def verify(*, download: bool = False) -> int:
    profiles = [read_json(ROOT / "manifests/ui-profile.json")]
    forms = ROOT / "manifests/forms-profile.json"
    if forms.exists():
        profiles.append(read_json(forms))
    inputs = ROOT / "manifests/ui-input-profile.json"
    if inputs.exists():
        profiles.append(read_json(inputs))
    clients = ROOT / "manifests/ui-client-profile.json"
    if clients.exists():
        profiles.append(read_json(clients))
    return verify_primary_references(profiles, ROOT / "build/ui-refs", download=download)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Read-only verification; no network or writes")
    args = parser.parse_args()
    print(f"UI primary references verified: {verify(download=not args.verify)} files")


if __name__ == "__main__":
    main()
