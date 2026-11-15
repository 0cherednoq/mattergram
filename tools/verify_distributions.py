"""Check exact archives, then install wheel and sdist-built wheel outside checkout."""

from __future__ import annotations

import argparse
import email.parser
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.requirements import Requirement

from tools._common import ROOT, safe_path, sha256, write_json


def command(*args: str, cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args!r}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def installed_tests(
    python: Path,
    directory: Path,
    temporary: Path,
    *,
    pattern: str = "test*.py",
    required: list[str] | None = None,
) -> int:
    script = """import json, sys, unittest
suite = unittest.defaultTestLoader.discover(sys.argv[1], pattern=sys.argv[2])
def names(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from names(case)
        else:
            yield case.id().rsplit('.', 1)[-1]
expected = suite.countTestCases()
assert expected > 0 and set(json.loads(sys.argv[3])).issubset(set(names(suite)))
result = unittest.TextTestRunner(verbosity=0).run(suite)
assert result.wasSuccessful() and result.testsRun == expected and not result.skipped
print(json.dumps({'passed': result.testsRun}))
"""
    output = command(
        str(python),
        "-I",
        "-B",
        "-W",
        "error",
        "-c",
        script,
        str(directory),
        pattern,
        json.dumps(required or []),
        cwd=temporary,
    )
    return int(json.loads(output)["passed"])


def verify_wheel_sources(wheel: Path) -> dict[str, str]:
    source = {
        path.relative_to(ROOT).as_posix(): path.read_bytes()
        for package in ("mattergram", "mattergram_dialog")
        for path in (ROOT / "src" / package).rglob("*")
        if path.is_file() and path.suffix in {".py", ".typed"}
    }
    with zipfile.ZipFile(wheel) as archive:
        packaged = {
            "src/" + name
            for name in archive.namelist()
            if name.startswith(("mattergram/", "mattergram_dialog/"))
            and (name.endswith(".py") or name.endswith("/py.typed"))
        }
        if packaged != source.keys() or any(
            archive.read(path.removeprefix("src/")) != data for path, data in source.items()
        ):
            raise ValueError("Wheel source bytes or file set differ from maintained source")
    return {path: sha256(data) for path, data in source.items()}


def inspect_archives(wheel: Path, sdist: Path, project: dict[str, Any]) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("Expected one wheel METADATA")
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata["Name"] != project["name"] or metadata["Version"] != project["version"]:
            raise ValueError("Distribution identity differs from pyproject")
        if metadata["Requires-Python"] != project["requires-python"]:
            raise ValueError("Requires-Python differs from pyproject")
        expected_requirements = {Requirement(value) for value in project["dependencies"]}
        for extra, dependencies in project.get("optional-dependencies", {}).items():
            for value in dependencies:
                requirement = Requirement(value)
                marker = f'extra == "{extra}"'
                requirement.marker = Marker(
                    f"({requirement.marker}) and {marker}" if requirement.marker else marker
                )
                expected_requirements.add(requirement)
        if {Requirement(value) for value in metadata.get_all("Requires-Dist", [])} != expected_requirements:
            raise ValueError("Runtime dependencies differ from pyproject")
        if set(metadata.get_all("Provides-Extra", [])) != set(project.get("optional-dependencies", {})):
            raise ValueError("Optional extras differ from pyproject")
        if "mattergram/THIRD_PARTY_NOTICES.md" not in names:
            raise ValueError("Missing wheel attribution")
        if not {"mattergram/py.typed", "mattergram_dialog/py.typed"}.issubset(names) or not any(
            name.endswith("/licenses/LICENSE") for name in names
        ):
            raise ValueError("Missing typing marker or license")
        for name in names:
            if not (name.startswith(("mattergram/", "mattergram_dialog/")) or ".dist-info/" in name):
                raise ValueError(f"Unexpected wheel entry: {name}")
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        prefix = names[0].split("/")[0] + "/"
        relative = [name.removeprefix(prefix) for name in names]
        if not {
            "src/mattergram/py.typed",
            "src/mattergram_dialog/py.typed",
            "LICENSE",
            "upstream.lock.json",
            "manifests/core.json",
            "manifests/native-api-overlays.json",
            "manifests/native-api-coverage.json",
            "manifests/dialog-plan.json",
            "manifests/dialog.json",
            "docs/reports/native-api-coverage.json",
            "docs/reports/dialog-inventory.json",
        }.issubset(relative):
            raise ValueError("Missing source distribution inputs")
        for name in relative:
            if name.startswith(("upstream/", "build/", ".venv/", ".env", ".git/")) or "__pycache__" in name:
                raise ValueError(f"Development-only or secret file in sdist: {name}")
    return verify_wheel_sources(wheel)


def verify_install(wheel: Path, temporary: Path, expected: str) -> dict[str, Any]:
    environment = temporary / "environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    command(str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel), cwd=temporary)
    script = """import importlib.metadata, importlib.resources, json, pathlib, sys
import mattergram
import mattergram_dialog
expected = sys.argv[1]
assert mattergram.__version__ == expected
assert importlib.metadata.version('mattergram') == expected
assert importlib.resources.files('mattergram').joinpath('py.typed').is_file()
assert importlib.resources.files('mattergram').joinpath('THIRD_PARTY_NOTICES.md').is_file()
assert importlib.resources.files('mattergram_dialog').joinpath('py.typed').is_file()
assert pathlib.Path(mattergram.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())
assert pathlib.Path(mattergram_dialog.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())
assert __import__('importlib.util').util.find_spec('redis') is None, 'Base install pulled in the optional Redis dependency'
assert __import__('importlib.util').util.find_spec('cryptography') is None, 'Base install pulled in the optional provisioning dependency'
from mattergram.provisioning import FernetCodec
from pydantic import SecretBytes
try:
    FernetCodec(SecretBytes(b'not-a-key'))
except ImportError:
    pass
else:
    raise AssertionError('Missing provisioning extra was not diagnosed')
print(json.dumps({'version': mattergram.__version__, 'import_file': mattergram.__file__, 'prefix': sys.prefix}))
"""
    output = command(str(python), "-I", "-c", script, expected, cwd=temporary)
    record: dict[str, Any] = json.loads(output)
    # Exercise the installed client's real local HTTP boundary without adding
    # checkout/src or a test runner that could reinstall the editable package.
    tests = temporary / "consumer-tests"
    tests.mkdir()
    for name in (
        "unit/test_native_models.py",
        "unit/test_routing.py",
        "unit/test_http_events.py",
        "unit/test_ui.py",
        "unit/test_ui_importers.py",
        "unit/test_form_values.py",
        "unit/test_provisioning_commands.py",
        "unit/test_native_api.py",
        "unit/test_input_file.py",
        "integration/test_native_http.py",
        "integration/test_native_api.py",
        "integration/test_high_level_api.py",
        "integration/test_setup_http.py",
        "integration/test_dispatcher_ws.py",
        "integration/test_http_ingress.py",
        "integration/test_dialogs.py",
        "integration/test_dialog_runtime.py",
    ):
        target = tests / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "tests" / name, target)
    shutil.copytree(ROOT / "tests/fixtures", tests / "fixtures")
    record["consumer_test_counts"] = {
        profile: installed_tests(python, tests / profile, temporary) for profile in ("unit", "integration")
    }
    record["consumer_native_tests"] = (
        "passed (models, routing/FSM, HTTP ingress/ASGI, local HTTP/WebSocket; real Mattermost checked separately)"
    )
    command(
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        str(wheel) + "[redis]",
        cwd=temporary,
    )
    config = tests / "unit/test_redis_config.py"
    shutil.copyfile(ROOT / "tests/unit/test_redis_config.py", config)
    record["redis_config_tests"] = installed_tests(python, tests / "unit", temporary, pattern=config.name)
    record["redis_extra"] = "installed; configuration tests passed"
    record["redis_real_profile"] = "not requested"
    if os.environ.get("MATTERGRAM_REDIS_URL"):
        remote = tests / "redis"
        remote.mkdir()
        shutil.copyfile(ROOT / "tests/redis/test_storage.py", remote / "test_storage.py")
        shutil.copyfile(ROOT / "tests/redis/test_dialogs.py", remote / "test_dialogs.py")
        profile = json.loads((ROOT / "manifests/redis-profile.json").read_text(encoding="utf-8"))
        record["redis_profile_tests"] = installed_tests(
            python,
            remote,
            temporary,
            required=profile["required_native_cases"] + profile.get("required_dialog_cases", []),
        )
        record["redis_real_profile"] = "passed"
    command(
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        str(wheel) + "[provisioning]",
        cwd=temporary,
    )
    storage = tests / "provisioning"
    storage.mkdir()
    for name in ("test_store.py", "test_bootstrap.py"):
        shutil.copyfile(ROOT / "tests/provisioning" / name, storage / name)
    record["provisioning_tests"] = installed_tests(python, storage, temporary)
    record["provisioning_extra"] = (
        "installed; Bootstrap/runtime handoff, native OS ACL, encrypted restart, journal/rollback "
        "and subprocess lock/crash tests passed"
    )
    return record


def unpack_sdist(sdist: Path, destination: Path) -> Path:
    with tarfile.open(sdist, "r:gz") as archive:
        roots = {member.name.split("/", 1)[0] for member in archive.getmembers()}
        if len(roots) != 1:
            raise ValueError("Expected one sdist root")
        for member in archive.getmembers():
            target = safe_path(destination, member.name.rstrip("/"))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                data = archive.extractfile(member)
                if data is None:
                    raise ValueError(f"Unreadable sdist member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with data:
                    target.write_bytes(data.read())
            else:
                raise ValueError(f"Unsupported sdist member: {member.name}")
        return safe_path(destination, roots.pop())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and sdist; use a fresh distribution directory")
    wheel, sdist = wheels[0].resolve(), sdists[0].resolve()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    sources = inspect_archives(wheel, sdist, project)
    results: dict[str, Any] = {
        "version": project["version"],
        "scope": "installed SDK metadata/resources/model/routing/FSM/local HTTP+WS tests; real Mattermost checked separately",
        "artifacts": {file.name: sha256(file.read_bytes()) for file in (wheel, sdist)},
        "source_sha256": sources,
    }
    with tempfile.TemporaryDirectory(prefix="mattergram-wheel-") as temporary:
        results["wheel_install"] = verify_install(wheel, Path(temporary), project["version"])
    with tempfile.TemporaryDirectory(prefix="mattergram-sdist-") as temporary:
        root = Path(temporary)
        source = unpack_sdist(sdist, root / "source")
        command(
            sys.executable, "-m", "build", "--wheel", "--outdir", str(root / "rebuilt"), str(source), cwd=root
        )
        rebuilt = list((root / "rebuilt").glob("*.whl"))
        if len(rebuilt) != 1:
            raise ValueError("Expected one rebuilt wheel")
        if verify_wheel_sources(rebuilt[0]) != sources:
            raise ValueError("Sdist-built wheel source differs from the original wheel")
        results["sdist_rebuilt_source_sha256"] = sources
        results["sdist_install"] = verify_install(rebuilt[0], root, project["version"])
    output = ROOT / "build/reports/distributions.json"
    write_json(output, results)
    print(f"wheel and sdist installed outside checkout; report: {output}")


if __name__ == "__main__":
    main()
