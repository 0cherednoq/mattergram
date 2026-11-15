"""Fetch pinned donors/references, or verify/restore an existing lock.

Run from the repository root: python -m tools.fetch_upstream [--create-lock | --verify].
Creating a lock is explicit and refuses to replace a previous lock. All downloads
finish in a temporary directory before a new snapshot becomes visible.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools._common import ROOT, read_json, safe_path, sha256, tree_digest, verify_tree, write_json


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mattergram-upstream-lock/1"})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
        if not isinstance(data, bytes):
            raise TypeError("Expected a binary HTTP response")
        return data


def unpack_archive(data: bytes, destination: Path) -> None:
    """GitHub ZIPs have one enclosing directory. Never use extractall."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        top_levels = {item.filename.split("/", 1)[0] for item in archive.infolist()}
        if len(top_levels) != 1:
            raise ValueError("Expected exactly one archive root")
        seen: set[str] = set()
        for item in archive.infolist():
            safe_path(destination, item.orig_filename.rstrip("/"))
            if item.is_dir():
                continue
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError(f"Symlink in snapshot: {item.filename}")
            _, separator, relative = item.filename.partition("/")
            if not separator or not relative:
                raise ValueError(f"Unexpected archive path: {item.filename}")
            key = relative.casefold()
            if key in seen:
                raise ValueError(f"Duplicate archive path: {relative}")
            seen.add(key)
            target = safe_path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))


def fetch_source(source: dict[str, Any], destination: Path) -> str | None:
    commit = source["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"Unpinned commit for {source['id']}")
    repository = source["repository"]
    if not re.fullmatch(r"https://github.com/[\w.-]+/[\w.-]+", repository):
        raise ValueError("Only explicit GitHub repository URLs are supported")
    if source["kind"] == "archive":
        data = download(
            f"https://codeload.github.com/{repository.removeprefix('https://github.com/')}/zip/{commit}"
        )
        unpack_archive(data, destination)
        return sha256(data)
    if source["kind"] != "files":
        raise ValueError(f"Unknown source kind: {source['kind']}")
    paths = source["paths"]
    if len({name.casefold() for name in paths}) != len(paths):
        raise ValueError("Duplicate reference paths")

    def fetch_file(name: str) -> None:
        target = safe_path(destination, name)
        data = download(f"{repository.replace('github.com', 'raw.githubusercontent.com')}/{commit}/{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(fetch_file, paths))
    return None


def describe_files(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {"sha256": sha256(path.read_bytes()), "size": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def create_lock_file(root: Path) -> dict[str, Any]:
    lock_path = root / "upstream.lock.json"
    if lock_path.exists():
        raise ValueError("Lock already exists; upstream updates require a separately reviewed lock")
    config = read_json(root / "manifests/upstream-sources.json")
    lock: dict[str, Any] = {
        "schema_version": 1,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sources": [],
    }
    upstream = root / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    # Do not expose half-created snapshots when a later source fails to download.
    with tempfile.TemporaryDirectory(prefix=".lock-", dir=upstream) as temporary:
        staged_root = Path(temporary)
        for config_source in config["sources"]:
            relative = f"upstream/snapshots/{config_source['id']}-{config_source['commit']}"
            if safe_path(root, relative).exists():
                raise ValueError(f"Unowned pre-existing snapshot: {relative}")
            staged = safe_path(staged_root, relative)
            staged.mkdir(parents=True)
            archive_hash = fetch_source(config_source, staged)
            source = {**config_source, "snapshot": relative, "archive_sha256": archive_hash}
            source["files"] = describe_files(staged)
            source["tree_sha256"] = tree_digest(source["files"])
            for license_path in source["licenses"]:
                if license_path not in source["files"]:
                    raise ValueError(f"Missing declared license: {license_path}")
            lock["sources"].append(source)
        for source in lock["sources"]:
            destination = safe_path(root, source["snapshot"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(safe_path(staged_root, source["snapshot"])), str(destination))
        write_json(lock_path, lock)
    return lock


def run(root: Path, *, create_lock: bool = False, verify_only: bool = False) -> dict[str, Any]:
    lock_path = root / "upstream.lock.json"
    if create_lock:
        return create_lock_file(root)
    else:
        lock: dict[str, Any] = read_json(lock_path)
        sources = lock["sources"]
    for source in sources:
        relative = f"upstream/snapshots/{source['id']}-{source['commit']}"
        destination = safe_path(root, relative)
        if destination.exists():
            verify_tree(destination, source["files"])
        else:
            if verify_only:
                raise ValueError(f"Missing snapshot: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".fetch-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "snapshot"
                staged.mkdir()
                fetch_source(source, staged)
                verify_tree(staged, source["files"])
                # File hashes are authoritative; GitHub may re-compress an archive.
                shutil.move(str(staged), str(destination))
        if source["tree_sha256"] != tree_digest(source["files"]):
            raise ValueError(f"Corrupt lock tree digest: {source['id']}")
        print(f"verified {source['id']}: {len(source['files'])} files, {source['commit']}")
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create-lock", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    run(ROOT, create_lock=args.create_lock, verify_only=args.verify)


if __name__ == "__main__":
    main()
