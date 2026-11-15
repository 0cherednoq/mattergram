"""Immutable pinned primary references shared by native feature profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tools._common import safe_path, sha256


def verify_primary_references(
    profiles: list[dict[str, Any]], destination: Path, *, download: bool = False
) -> int:
    count = 0
    seen: dict[tuple[str, str], str] = {}
    for release in [r for profile in profiles for r in profile["primary_references"]]:
        for reference in release["files"]:
            if "sha256" not in reference:
                continue  # Recorded 404 on an optional renamed file; not evidence of absent features.
            identity = (release["commit"], reference["path"])
            url = reference["url"]
            expected_url = (
                "https://raw.githubusercontent.com/mattermost/mattermost/"
                + release["commit"]
                + "/"
                + reference["path"]
            )
            if url != expected_url:
                raise ValueError("Reference must use the pinned primary repository commit")
            if identity in seen:
                if seen[identity] != reference["sha256"]:
                    raise ValueError("Conflicting hashes for a shared primary reference")
                continue
            seen[identity] = reference["sha256"]
            target = safe_path(destination, release["version"] + "/" + reference["path"].replace("/", "__"))
            if not target.exists() and download:
                with urlopen(
                    Request(url, headers={"User-Agent": "mattergram-reference-verifier"}), timeout=40
                ) as response:
                    data = response.read()
                if sha256(data) != reference["sha256"]:
                    raise ValueError("Downloaded primary reference hash differs")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(data)
            if sha256(target.read_bytes()) != reference["sha256"]:
                raise ValueError(f"Primary reference differs: {release['version']} {reference['path']}")
            count += 1
    return count
