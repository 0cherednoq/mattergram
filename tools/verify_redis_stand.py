"""Require real Redis profile on the explicitly owned, image-pinned loopback stand."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from tools._common import ROOT, read_json, sha256, write_json


def command(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--distributions", action="store_true", help="Also verify existing wheel/sdist with this real profile"
    )
    parser.add_argument(
        "--mattermost", action="store_true", help="Also verify real Mattermost WS routing with Redis FSM"
    )
    args = parser.parse_args()
    profile = read_json(ROOT / "manifests/redis-profile.json")
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / "build/native-stand.env"),
        "-p",
        profile["compose_project"],
        "-f",
        str(ROOT / "infra/native-stand.compose.yml"),
    ]
    identifier = command(*compose, "ps", "-q", "redis")
    if not identifier or len(identifier.splitlines()) != 1:
        raise RuntimeError("Owned Redis stand is not running; start its explicit compose service")
    labels = json.loads(command("docker", "inspect", "--format", "{{json .Config.Labels}}", identifier))
    image = command("docker", "inspect", "--format", "{{.Config.Image}}", identifier)
    if (
        image != profile["server_image"]
        or labels.get("com.docker.compose.project") != profile["compose_project"]
        or labels.get("com.docker.compose.service") != profile["compose_service"]
    ):
        raise RuntimeError("Redis fixture ownership or pinned image differs")
    port = command(*compose, "port", "redis", "6379")
    if not port.startswith("127.0.0.1:") or not port.removeprefix("127.0.0.1:").isdigit():
        raise RuntimeError("Redis stand must expose only one loopback endpoint")
    if command(*compose, "exec", "-T", "redis", "redis-cli", "ping") != "PONG":
        raise RuntimeError("Owned Redis stand is not ready")
    server = command(*compose, "exec", "-T", "redis", "redis-server", "--version")
    env = {**os.environ, profile["env"]: "redis://" + port + "/0"}
    xml = ROOT / "build/reports/redis-tests.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/redis",
            "tests/unit/test_redis_config.py",
            "tests/ported/test_fsm/storage",
            "-q",
            f"--junitxml={xml}",
        ],
        cwd=ROOT,
        env=env,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Real Redis profile failed")
    cases = list(ET.parse(xml).iter("testcase"))
    names = {item.attrib["name"] for item in cases if "RedisStorageTests" in item.attrib.get("classname", "")}
    if set(profile["required_native_cases"]) - names:
        raise RuntimeError("Real Redis profile did not execute all required native cases")
    dialog_names = {
        item.attrib["name"] for item in cases if "RedisDialogTests" in item.attrib.get("classname", "")
    }
    if set(profile.get("required_dialog_cases", [])) - dialog_names:
        raise RuntimeError("Real Redis profile did not execute all required dialog session cases")
    if any(
        item.find("skipped") is not None or item.find("failure") is not None or item.find("error") is not None
        for item in cases
    ):
        raise RuntimeError("Skipped or unsuccessful tests cannot satisfy the real Redis profile")
    mattermost: dict[str, Any] | str = "not requested"
    if args.mattermost:
        subprocess.run(
            [sys.executable, "-m", "tools.verify_native_stand"], cwd=ROOT, env=env, check=True, timeout=180
        )
        joined: dict[str, Any] = read_json(ROOT / "build/reports/native-stand-redis.json")
        if joined["routing"]["fsm_profile"] != "redis" or not joined["routing"]["state_transition_and_clear"]:
            raise RuntimeError("Real Mattermost did not execute the Redis FSM transition")
        mattermost = joined
    distributions: dict[str, Any] | str = "not requested"
    if args.distributions:
        subprocess.run(
            [sys.executable, "-m", "tools.verify_distributions"], cwd=ROOT, env=env, check=True, timeout=600
        )
        installed: dict[str, Any] = read_json(ROOT / "build/reports/distributions.json")
        if any(
            installed[key].get("redis_real_profile") != "passed" for key in ("wheel_install", "sdist_install")
        ):
            raise RuntimeError("Installed artifacts did not run the real Redis profile")
        distributions = installed
    write_json(
        ROOT / "build/reports/redis-stand.json",
        {
            "result": "passed",
            "checked_at": datetime.now(UTC).isoformat(),
            "scope": profile["scope"],
            "server_image": image,
            "server_version": server,
            "client_version": importlib.metadata.version("redis"),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "required_native_cases": sorted(names),
            "required_dialog_cases": sorted(dialog_names),
            "junit_testcases": len(cases),
            "skipped": 0,
            "tests_xml_sha256": sha256(xml.read_bytes()),
            "profile_sha256": sha256((ROOT / "manifests/redis-profile.json").read_bytes()),
            "distributions": distributions,
            "mattermost_fsm": mattermost,
        },
    )
    print("Real Redis profile passed without skips; report: build/reports/redis-stand.json")


if __name__ == "__main__":
    main()
