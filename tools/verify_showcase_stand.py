"""Verify the interactive showcase against the owned Mattermost 11.1 stand."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
from datetime import UTC, date, datetime
from typing import Any

import aiohttp

from tools._common import ROOT, read_json, write_json


def _owned_stand() -> tuple[str, str]:
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / "build/native-stand.env"),
        "-p",
        "mattergram-native-stand",
        "-f",
        str(ROOT / "infra/native-stand.compose.yml"),
    ]
    port = subprocess.run(
        [*compose, "port", "mattermost", "8065"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not port.startswith("127.0.0.1:"):
        raise RuntimeError("Owned Mattermost is not bound to loopback")
    container = subprocess.run(
        [*compose, "ps", "-q", "mattermost"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    labels = json.loads(
        subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Labels}}", container],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if labels.get("com.docker.compose.project") != "mattergram-native-stand":
        raise RuntimeError("Refusing a Mattermost server outside the owned stand")
    network = json.loads(
        subprocess.run(
            ["docker", "network", "inspect", "mattergram-native-stand_default"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    subnet = network[0]["IPAM"]["Config"][0]["Subnet"]
    if not isinstance(subnet, str) or not subnet:
        raise RuntimeError("Owned stand network has no explicit subnet")
    return "http://" + port, subnet


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_port(process: asyncio.subprocess.Process, port: int) -> None:
    async with asyncio.timeout(15):
        while True:
            if process.returncode is not None:
                stderr = await process.stderr.read() if process.stderr is not None else b""
                raise RuntimeError("Showcase process exited early: " + stderr.decode(errors="replace"))
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.05)
                continue
            writer.close()
            await writer.wait_closed()
            return


async def verify() -> dict[str, Any]:
    base, trusted_subnet = _owned_stand()
    state_path = ROOT / "build/native-stand-state.json"
    if not state_path.exists():
        from tools.verify_native_stand import verify as bootstrap

        await bootstrap()
    state = read_json(state_path)
    port = _free_port()
    endpoint = f"http://host.docker.internal:{port}"
    command_id: str | None = None
    created_posts: list[str] = []
    process: asyncio.subprocess.Process | None = None
    actor_socket: aiohttp.ClientWebSocketResponse | None = None

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as control:
        admin_token: str | None = None

        async def request(
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
            *,
            expected: tuple[int, ...] = (200, 201),
        ) -> dict[str, Any]:
            async with control.request(
                method,
                base + "/api/v4/" + path,
                json=body,
                headers={"Authorization": "Bearer " + admin_token} if admin_token else {},
            ) as response:
                if response.status not in expected:
                    detail = await response.text()
                    raise RuntimeError(
                        f"Owned Mattermost {method} {path} returned {response.status}: {detail[:300]}"
                    )
                if response.content_type != "application/json":
                    return {}
                result = await response.json()
                if not isinstance(result, dict):
                    raise RuntimeError("Owned Mattermost returned an unexpected payload")
                return result

        async def post(post_id: str) -> dict[str, Any]:
            return await request("GET", "posts/" + post_id)

        async def wait_post(predicate: Any) -> dict[str, Any]:
            async with asyncio.timeout(12):
                while True:
                    page = await request("GET", f"channels/{state['channel_id']}/posts?per_page=100")
                    for post_id, item in page.get("posts", {}).items():
                        if post_id not in existing_posts and predicate(item):
                            if post_id not in created_posts:
                                created_posts.append(post_id)
                            return dict(item)
                    await asyncio.sleep(0.05)

        async def wait_text(post_id: str, fragment: str) -> dict[str, Any]:
            async with asyncio.timeout(12):
                while True:
                    current = await post(post_id)
                    if fragment in current.get("message", ""):
                        return current
                    await asyncio.sleep(0.05)

        try:
            async with control.post(
                base + "/api/v4/users/login",
                json={"login_id": state["admin_username"], "password": state["admin_password"]},
            ) as login:
                admin = await login.json()
                admin_token = login.headers.get("Token")
                if login.status != 200 or not admin_token or "system_admin" not in admin["roles"]:
                    raise RuntimeError("Owned stand admin login failed")

            existing_page = await request("GET", f"channels/{state['channel_id']}/posts?per_page=100")
            existing_posts = set(existing_page.get("posts", {}))
            command = await request(
                "POST",
                "commands",
                {
                    "team_id": state["team_id"],
                    "trigger": "showcase",
                    "method": "P",
                    "url": endpoint + "/mattergram/slash",
                    "display_name": "Mattergram showcase verification",
                },
            )
            command_id = command["id"]
            environment = {
                **os.environ,
                "MM_URL": base,
                "MM_BOT_TOKEN": state["bot_token"],
                "MM_TEAM_ID": state["team_id"],
                "MM_SLASH_TOKEN": command["token"],
                "MM_CALLBACK_BASE_URL": endpoint,
                # Docker Desktop forwards host.docker.internal through loopback;
                # Linux engines normally preserve the owned bridge subnet.
                "MM_HTTP_TRUSTED_PEERS": "127.0.0.1/32," + trusted_subnet,
                "MM_ACTION_SIGNING_KEY_HEX": secrets.token_hex(32),
                "MM_SERVER_VERSION": "11.1.0",
                "MM_HTTP_HOST": "0.0.0.0",
                "MM_HTTP_PORT": str(port),
            }
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(ROOT / "examples/showcase_bot.py"),
                cwd=ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await _wait_port(process, port)

            actor_socket = await control.ws_connect(base + "/api/v4/websocket", heartbeat=10)
            await actor_socket.send_json(
                {"seq": 1, "action": "authentication_challenge", "data": {"token": admin_token}}
            )
            while True:
                reply = await actor_socket.receive_json(timeout=8)
                if reply.get("seq_reply") == 1:
                    if reply.get("status") != "OK":
                        raise RuntimeError("Actor WebSocket authentication failed")
                    break

            await request(
                "POST",
                "commands/execute",
                {"channel_id": state["channel_id"], "command": "/showcase"},
            )
            card = await wait_post(
                lambda item: str(item.get("message", "")).startswith("# Operations workspace")
            )
            card_id = card["id"]
            props = card["props"]
            if len(props.get("attachments", [])) != 3:
                raise RuntimeError("Live showcase card omitted an action group")
            action_count = sum(len(item.get("actions", [])) for item in props["attachments"])
            if action_count != 11:
                raise RuntimeError("Live showcase card action count is wrong")

            await request("POST", f"posts/{card_id}/actions/ordernew", {})
            async with asyncio.timeout(10):
                while True:
                    opened_event = await actor_socket.receive_json(timeout=8)
                    if opened_event.get("event") != "open_dialog":
                        continue
                    opened = json.loads(opened_event["data"]["dialog"])
                    form = opened["dialog"]
                    if form["callback_id"] == "showcase_order":
                        break
            customer = "Live customer " + secrets.token_hex(3)
            await request(
                "POST",
                "actions/dialogs/submit",
                {
                    "url": endpoint + "/mattergram/dialog",
                    "callback_id": form["callback_id"],
                    "state": form["state"],
                    "channel_id": state["channel_id"],
                    "team_id": state["team_id"],
                    "cancelled": False,
                    "submission": {
                        "customer": customer,
                        "status": "open",
                        "priority": "normal",
                        "due": date.today().isoformat(),
                        "gift": False,
                        "notes": "Created by the owned stand verifier",
                    },
                },
            )
            await wait_text(card_id, customer)

            await request("POST", f"posts/{card_id}/actions/ordernext", {})
            await wait_text(card_id, "## Orders · filter `all` · page 2/")
            after_order_page = await post(card_id)
            if "## Incidents · filter `all` · page 1/" not in after_order_page["message"]:
                raise RuntimeError("Orders pagination changed the incidents page")

            await request(
                "POST",
                f"posts/{card_id}/actions/incidentfilter",
                {"selected_option": "p1"},
            )
            filtered = await wait_text(card_id, "## Incidents · filter `p1` · page 1/1")
            if "## Orders · filter `all` · page 2/" not in filtered["message"]:
                raise RuntimeError("Incident filtering changed the orders page")

            live_message = await request(
                "POST",
                "posts",
                {"channel_id": state["channel_id"], "message": "showcase live"},
            )
            created_posts.append(live_message["id"])
            live = await wait_text(card_id, "Live revision: **1**")
            if len(live.get("props", {}).get("attachments", [])) != 3:
                raise RuntimeError("REST text edit did not preserve live card props")

            return {
                "schema_version": 1,
                "result": "passed",
                "checked_at": datetime.now(UTC).isoformat(),
                "scope": "Owned Mattermost 11.1 API/HTTP/WS; not browser rendering",
                "server_version": "11.1.0",
                "checks": [
                    "slash created one post with two tables, three action groups, and eleven controls",
                    "native order form submission updated the source card",
                    "orders pagination preserved incidents pagination",
                    "incidents filtering preserved orders pagination",
                    "WebSocket message event triggered a REST text patch that preserved card props",
                ],
                "temporary_resources": "removed before report write",
                "credentials": "omitted",
            }
        finally:
            if actor_socket is not None:
                await actor_socket.close()
            if process is not None:
                if process.returncode is None:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            if admin_token is not None:
                for post_id in created_posts:
                    await request("DELETE", "posts/" + post_id, expected=(200, 204, 404))
                if command_id is not None:
                    await request("DELETE", "commands/" + command_id, expected=(200, 204, 404))


def main() -> None:
    report = asyncio.run(verify())
    path = ROOT / "docs/reports/showcase.json"
    write_json(path, report)
    print(f"Showcase stand verification passed; evidence: {path}")


if __name__ == "__main__":
    main()
