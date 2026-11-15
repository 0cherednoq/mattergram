"""Build the Mattergram Menu server/webapp plugin bundle."""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]


def _normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.isdir() or "/server/dist/plugin-" in info.name else 0o644
    return info


def _remove_readonly(function: object, path: str, _error: object) -> None:
    os.chmod(path, stat.S_IWRITE)
    if callable(function):
        function(path)


def _linked_go_modules(executable: Path) -> set[str]:
    result = subprocess.run(
        ["go", "version", "-m", str(executable)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        fields[1]
        for line in result.stdout.splitlines()
        if len(fields := line.strip().split("\t")) >= 3 and fields[0] == "dep"
    }


def _go_modules(linked: set[str]) -> list[dict[str, object]]:
    result = subprocess.run(
        ["go", "list", "-m", "-json", "all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    decoder = json.JSONDecoder()
    modules: list[dict[str, object]] = []
    position = 0
    while position < len(result.stdout):
        while position < len(result.stdout) and result.stdout[position].isspace():
            position += 1
        if position >= len(result.stdout):
            break
        value, position = decoder.raw_decode(result.stdout, position)
        if not isinstance(value, dict):
            raise ValueError("go list returned a non-object module")
        if value.get("Path") in linked:
            modules.append(value)
    return modules


def _copy_licenses(bundle_root: Path, executable: Path) -> None:
    shutil.copy2(REPOSITORY / "LICENSE", bundle_root / "LICENSE")
    license_root = bundle_root / "third_party_licenses"
    notices = [
        "# Third-party notices",
        "",
        "The server executables include the following Go modules. License files are copied from the",
        "exact module versions resolved by go.mod/go.sum at build time.",
        "",
    ]
    linked = _linked_go_modules(executable)
    modules = _go_modules(linked)
    resolved = {str(module.get("Path")) for module in modules}
    if resolved != linked:
        raise ValueError(f"Linked Go modules lack metadata: {sorted(linked - resolved)}")
    for module in sorted(modules, key=lambda item: str(item.get("Path", ""))):
        path = module.get("Path")
        version = module.get("Version")
        directory = module.get("Dir")
        if not isinstance(path, str) or path == "github.com/mattergram/mattergram-menu":
            continue
        if not isinstance(version, str) or not isinstance(directory, str):
            raise ValueError(f"Go module lacks a resolved version or directory: {path}")
        sources = sorted(
            candidate
            for pattern in ("LICENSE*", "COPYING*", "NOTICE*")
            for candidate in Path(directory).glob(pattern)
            if candidate.is_file()
        )
        if not sources:
            raise ValueError(f"Go module has no root license file: {path}@{version}")
        module_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{path}@{version}")
        target = license_root / module_name
        target.mkdir(parents=True)
        copied: list[str] = []
        for source in sources:
            destination = target / source.name
            shutil.copyfile(source, destination)
            destination.chmod(0o644)
            copied.append(source.name)
        notices.append(f"- `{path}` `{version}` — {', '.join(copied)}")
    (bundle_root / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(notices) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    manifest_path = ROOT / "plugin.json"
    source_path = ROOT / "webapp" / "src" / "index.js"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = source_path.read_text(encoding="utf-8")

    plugin_id = manifest["id"]
    version = manifest["version"]
    declared_id = re.search(r'const PLUGIN_ID = "([^"]+)";', source)
    if declared_id is None or declared_id.group(1) != plugin_id:
        raise ValueError("plugin.json id and webapp PLUGIN_ID must match")

    webapp_dist = ROOT / "webapp" / "dist"
    webapp_dist.mkdir(parents=True, exist_ok=True)
    (webapp_dist / "main.js").write_text(source, encoding="utf-8", newline="\n")

    server_dist = ROOT / "server" / "dist"
    server_dist.mkdir(parents=True, exist_ok=True)
    for architecture in ("amd64", "arm64"):
        environment = os.environ.copy()
        environment.update({"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": architecture})
        subprocess.run(
            [
                "go",
                "build",
                "-trimpath",
                "-buildvcs=false",
                "-ldflags=-s -w -buildid=",
                "-o",
                str(server_dist / f"plugin-linux-{architecture}"),
                "./server",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )

    output_root = REPOSITORY / "build" / "plugins"
    bundle_root = output_root / plugin_id
    if bundle_root.exists():
        shutil.rmtree(bundle_root, onerror=_remove_readonly)
    (bundle_root / "webapp" / "dist").mkdir(parents=True)
    shutil.copy2(manifest_path, bundle_root / "plugin.json")
    shutil.copy2(webapp_dist / "main.js", bundle_root / "webapp" / "dist" / "main.js")
    (bundle_root / "server" / "dist").mkdir(parents=True)
    for executable in manifest["server"]["executables"].values():
        source_executable = ROOT / executable
        if not source_executable.is_file():
            raise FileNotFoundError(source_executable)
        target_executable = bundle_root / executable
        shutil.copy2(source_executable, target_executable)
    _copy_licenses(bundle_root, server_dist / "plugin-linux-amd64")

    archive = output_root / f"mattergram-menu-{version}.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    with archive.open("wb") as raw_archive:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
                bundle.add(bundle_root, arcname=plugin_id, filter=_normalized_tar_info)

    print(archive)


if __name__ == "__main__":
    main()
