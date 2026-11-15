"""Owned E5 UI fixtures and a real SDK callback/form host, never production provisioning.

Administrative setup is isolated here. Runtime handlers use a separately issued
bot token; the browser acts as an ordinary human. Secrets stay in ignored build.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import secrets
import shutil
import ssl
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.http import CallbackSigner, DialogManager, HttpAdapter, HTTPResult, SlashBinding
from mattergram.types import (
    CallbackQuery,
    DialogAction,
    DialogCancel,
    DialogLookup,
    DialogRefresh,
    DialogSubmission,
    ErrorEvent,
    SlashCommand,
)
from mattergram.ui import UIProfile, from_adaptive_cards, from_slack_blocks
from mattergram.ui.actions import ActionContext, Actions, Attachment, AttachmentField, External
from mattergram.ui.actions import Button as LegacyButton
from mattergram.ui.actions import Select as LegacySelect
from mattergram.ui.blocks import (
    Button,
    Collapsible,
    Column,
    ColumnSet,
    Container,
    Divider,
    Image,
    StaticSelect,
    Text,
)
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram.ui.form_values import MISSING
from mattergram.ui.forms import (
    Checkbox,
    CheckboxGroup,
    CheckboxMatrix,
    DatePicker,
    DateTimePicker,
    DialogButton,
    FileInput,
    Form,
    MatrixConfig,
    MultiSelect,
    Option,
    Radio,
    Select,
    TextArea,
    TextInput,
)
from mattergram.ui.forms import Collapsible as FieldGroup
from mattergram.ui.forms import (
    DialogAction as ChildAction,
)
from tools._common import ROOT, read_json, sha256, write_json

STATE = ROOT / "build/ui-stand-state.json"
REPORT = ROOT / "build/reports/ui-stand.json"
TLS = ROOT / "build/ui-tls"
STOP = ROOT / "build/ui-stand.stop"
BROWSER_STATE = ROOT / "build/ui-browser-state.json"
SETUP_REPORT = ROOT / "build/reports/ui-setup.json"
PRIVATE_CAPTURE = ROOT / "build/ui-private-http.json"
PRIVATE_ERROR = ROOT / "build/ui-private-error.json"
VERSION = "12.0.2"
SERVICE = "mattermost_ui"
RELEASES = {"12.0.2": "ui", "11.7.0": "ui117", "11.11.0": "ui1111"}
COMPOSE_BASE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / "build/native-stand.env"),
    "-p",
    "mattergram-native-stand",
    "-f",
    str(ROOT / "infra/native-stand.compose.yml"),
    "-f",
    str(ROOT / "infra/ui-stand.compose.yml"),
]
COMPOSE = [*COMPOSE_BASE, "--profile", "ui"]
OPTIONS = (Option(text="Alpha", value="a"), Option(text="Beta", value="b"))


def configure_profile(version: str) -> None:
    """Select one explicit CLI fixture; never infer another tenant or release."""
    global STATE, REPORT, STOP, BROWSER_STATE, SETUP_REPORT, PRIVATE_CAPTURE, PRIVATE_ERROR
    global VERSION, SERVICE, COMPOSE
    key = RELEASES[version]
    VERSION = version
    SERVICE = "mattermost_" + key
    STATE = ROOT / f"build/{key}-stand-state.json"
    REPORT = ROOT / f"build/reports/{key}-stand.json"
    STOP = ROOT / f"build/{key}-stand.stop"
    BROWSER_STATE = ROOT / f"build/{key}-browser-state.json"
    SETUP_REPORT = ROOT / f"build/reports/{key}-setup.json"
    PRIVATE_CAPTURE = ROOT / f"build/{key}-private-http.json"
    PRIVATE_ERROR = ROOT / f"build/{key}-private-error.json"
    extra = ["-f", str(ROOT / "infra/ui-client-matrix.compose.yml")] if key != "ui" else []
    COMPOSE = [*COMPOSE_BASE, *extra, "--profile", key]


def base_url() -> str:
    port = subprocess.run(
        [*COMPOSE, "port", SERVICE, "8065"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not port.startswith("127.0.0.1:") or not port.removeprefix("127.0.0.1:").isdigit():
        raise RuntimeError("UI stand must publish only on loopback")
    return "http://" + port


def make_tls() -> None:
    """Create one local trust anchor and a SAN leaf; never install into host trust."""
    if (TLS / "ca.pem").exists():
        for name in ("ca.pem", "server.pem", "server.key"):
            if not (TLS / name).is_file():
                raise RuntimeError("Incomplete UI TLS fixture; inspect it rather than replacing keys")
        return
    executable = shutil.which("openssl")
    if executable is None:
        candidate = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise RuntimeError("OpenSSL is required to create the owned UI TLS fixture")
    TLS.mkdir(parents=True, exist_ok=True)
    (TLS / "extensions.cnf").write_text(
        "subjectAltName=DNS:host.docker.internal,DNS:localhost,IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n",
        encoding="ascii",
    )
    commands = [
        [
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "30",
            "-subj",
            "/CN=Mattergram owned UI test CA",
            "-keyout",
            "ca.key",
            "-out",
            "ca.pem",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ],
        [
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=host.docker.internal",
            "-keyout",
            "server.key",
            "-out",
            "server.csr",
        ],
        [
            "x509",
            "-req",
            "-in",
            "server.csr",
            "-CA",
            "ca.pem",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            "server.pem",
            "-days",
            "30",
            "-extfile",
            "extensions.cnf",
        ],
        ["verify", "-CAfile", "ca.pem", "-verify_hostname", "host.docker.internal", "server.pem"],
    ]
    for command in commands:
        result = subprocess.run([executable, *command], cwd=TLS, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError("UI TLS fixture generation failed; keys/output are not logged")
    print("Owned UI CA/leaf generated and hostname verified; host trust unchanged")


class Control:
    def __init__(self, base: str, client: aiohttp.ClientSession) -> None:
        self.base = base
        self.client = client
        self.response_cookies: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        token: str | None = None,
        expected: tuple[int, ...] = (200, 201),
        browser_session: bool = False,
    ) -> tuple[Any, dict[str, str]]:
        async with self.client.request(
            method,
            self.base + "/api/v4/" + path,
            json=body,
            headers={
                **({"Authorization": "Bearer " + token} if token else {}),
                **({"X-Requested-With": "XMLHttpRequest"} if browser_session else {}),
            },
            allow_redirects=False,
        ) as response:
            if response.status not in expected:
                write_json(
                    PRIVATE_ERROR,
                    {
                        "operation": method + " " + path,
                        "status": response.status,
                        "response": await response.json(),
                    },
                )
                raise RuntimeError(f"Owned UI fixture {method} {path} returned HTTP {response.status}")
            self.response_cookies = [
                {
                    "name": name,
                    "value": item.value,
                    "domain": item["domain"] or urlsplit(self.base).hostname,
                    "path": item["path"] or "/",
                    "expires": time.time() + int(item["max-age"]) if item["max-age"] else -1,
                    "httpOnly": bool(item["httponly"]),
                    "secure": bool(item["secure"]),
                    "sameSite": item["samesite"].capitalize() if item["samesite"] else "Lax",
                }
                for name, item in response.cookies.items()
            ]
            return await response.json(), dict(response.headers)

    async def login(
        self, state: dict[str, Any], who: str, *, browser: bool = False
    ) -> tuple[dict[str, Any], str]:
        user, headers = await self.request(
            "POST",
            "users/login",
            {"login_id": state[who + "_username"], "password": state[who + "_password"]},
            browser_session=browser,
        )
        token = headers.get("Token")
        if not isinstance(user, dict) or not token:
            raise RuntimeError("Owned UI login did not issue a real session")
        return user, token


async def setup() -> None:
    base = base_url()
    state: dict[str, Any] = (
        read_json(STATE)
        if STATE.exists()
        else {
            "admin_username": "mattergram-ui-admin",
            "admin_password": "MG!" + secrets.token_urlsafe(24),
            "actor_username": "mattergram-ui-actor",
            "actor_password": "MG!" + secrets.token_urlsafe(24),
        }
    )
    write_json(STATE, state)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
        control = Control(base, client)
        _, headers = await control.request("GET", "system/ping")
        if not headers.get("X-Version-Id", "").startswith(VERSION + "."):
            raise RuntimeError("UI fixtures require their explicitly selected server version")
        state["base_url"] = base
        state["profile_version"] = VERSION
        state["server_version"] = headers["X-Version-Id"]
        if not state.get("admin_id"):
            admin, _ = await control.request(
                "POST",
                "users",
                {
                    "username": state["admin_username"],
                    "email": "mattergram-ui-admin@example.test",
                    "password": state["admin_password"],
                },
            )
            state["admin_id"] = admin["id"]
            write_json(STATE, state)
        admin, admin_token = await control.login(state, "admin")
        if admin["id"] != state["admin_id"] or "system_admin" not in admin["roles"].split():
            raise RuntimeError("UI setup identity must be the owned stand administrator")
        state["admin_token"] = admin_token
        config, _ = await control.request("GET", "config", token=admin_token)
        if config["ServiceSettings"]["SiteURL"] != base:
            config["ServiceSettings"]["SiteURL"] = base
            config, _ = await control.request("PUT", "config", config, token=admin_token)
        if config["ServiceSettings"]["EnableInsecureOutgoingConnections"] is not False:
            raise RuntimeError("UI stand requires normal TLS certificate verification")
        client_config, _ = await control.request("GET", "config/client?format=old", token=admin_token)
        state["mm_blocks_enabled"] = config.get("FeatureFlags", {}).get("MmBlocksEnabled")
        if state["mm_blocks_enabled"] is None:
            flag = client_config.get("FeatureFlagMmBlocksEnabled")
            state["mm_blocks_enabled"] = {"true": True, "false": False}.get(flag)
        if not state.get("actor_id"):
            actor, _ = await control.request(
                "POST",
                "users",
                {
                    "username": state["actor_username"],
                    "email": "mattergram-ui-actor@example.test",
                    "password": state["actor_password"],
                },
                token=admin_token,
            )
            state["actor_id"] = actor["id"]
            write_json(STATE, state)
        actor, actor_token = await control.login(state, "actor", browser=True)
        actor_cookies = control.response_cookies
        if not actor_cookies:
            raise RuntimeError("Native browser login did not issue cookies")
        if actor["id"] != state["actor_id"] or "system_admin" in actor["roles"].split():
            raise RuntimeError("UI actor must be an ordinary, separately authenticated human")
        state["actor_token"] = actor_token
        if not state.get("team_id"):
            team, _ = await control.request(
                "POST",
                "teams",
                {"name": "mattergram-ui", "display_name": "Mattergram UI stand", "type": "O"},
                token=admin_token,
            )
            state["team_id"] = team["id"]
            write_json(STATE, state)
        if not state.get("bot_id"):
            record, _ = await control.request(
                "POST",
                "bots",
                {"username": "mattergram-ui-bot", "display_name": "Mattergram UI bot"},
                token=admin_token,
            )
            state["bot_id"] = record["user_id"]
            write_json(STATE, state)
        if not state.get("bot_token"):
            record, _ = await control.request(
                "POST",
                f"users/{state['bot_id']}/tokens",
                {"description": "Owned UI stand runtime bot"},
                token=admin_token,
            )
            state["bot_token"] = record["token"]
            write_json(STATE, state)
        for user_id in (state["actor_id"], state["bot_id"]):
            member, _ = await control.request(
                "GET", f"teams/{state['team_id']}/members/{user_id}", token=admin_token, expected=(200, 404)
            )
            if member.get("user_id") != user_id:
                await control.request(
                    "POST",
                    f"teams/{state['team_id']}/members",
                    {"team_id": state["team_id"], "user_id": user_id},
                    token=admin_token,
                )
        if not state.get("channel_id"):
            channel, _ = await control.request(
                "POST",
                "channels",
                {
                    "team_id": state["team_id"],
                    "name": "ui-scenarios",
                    "display_name": "UI scenarios",
                    "type": "O",
                },
                token=admin_token,
            )
            state["channel_id"] = channel["id"]
            write_json(STATE, state)
        for user_id in (state["actor_id"], state["bot_id"]):
            member, _ = await control.request(
                "GET",
                f"channels/{state['channel_id']}/members/{user_id}",
                token=admin_token,
                expected=(200, 404),
            )
            if member.get("user_id") != user_id:
                await control.request(
                    "POST", f"channels/{state['channel_id']}/members", {"user_id": user_id}, token=admin_token
                )
        async with Bot(token=state["bot_token"], base_url=base) as bot:
            if bot.user_id != state["bot_id"] or bot.user_id == state["admin_id"]:
                raise RuntimeError("Runtime bot identity differs from its issued credential")
        write_json(STATE, state)
        # Preserve all actual browser-login cookies, including user/CSRF state.
        write_json(
            BROWSER_STATE,
            {
                "cookies": actor_cookies,
                "origins": [],
            },
        )
        write_json(
            SETUP_REPORT,
            {
                "server_version": state["server_version"],
                "base_url": base,
                "actor_roles": actor["roles"],
                "admin_and_runtime_separate": True,
                "actor_is_ordinary_human": True,
                "channel_url": base + "/mattergram-ui/channels/ui-scenarios",
                "credentials": f"ignored {STATE.relative_to(ROOT)} and {BROWSER_STATE.relative_to(ROOT)} only",
            },
        )
    print("UI fixtures ready: separate admin / ordinary human / runtime bot; browser state saved privately")


class Scenario:
    def __init__(self, state: dict[str, Any], bot: Bot, dispatcher: Dispatcher) -> None:
        self.state = state
        self.bot = bot
        self.profile = UIProfile(
            state["profile_version"],
            client="owned-web-experiment",
            client_features=SERVER_FEATURES[state["profile_version"]],
            client_evidence="Explicit UI harness declaration; per-feature evidence recorded separately",
            mm_blocks_enabled=state["mm_blocks_enabled"],
        )
        self.manager = DialogManager(bot, profile=self.profile, ttl=1800)
        self.signer = CallbackSigner(
            instance_id=bot.instance_id,
            bot_id=state["bot_id"],
            keys={"ui": secrets.token_bytes(32)},
            active_key_id="ui",
        )
        self.endpoint = ""
        self.events: list[dict[str, Any]] = []
        self.http: list[dict[str, Any]] = []
        self.posts: list[str] = []
        self.status: dict[str, Any] = {
            "scope": "Interactive owned web UI exercise; outcomes recorded, no unexecuted client capability claimed",
            "server_version": state["server_version"],
            "actor_is_ordinary_human": True,
            "admin_and_runtime_separate": True,
            "events": self.events,
            "http": self.http,
            "posts": self.posts,
        }
        self.bind(dispatcher)

    def flush(self) -> None:
        write_json(REPORT, self.status)

    def record(self, kind: str, **data: Any) -> None:
        self.events.append({"kind": kind, **data})
        self.flush()

    def form(self, mode: str, *, category: str | None = None) -> Form:
        common: dict[str, Any] = {
            "callback_id": mode,
            "title": "SDK " + mode,
            "state": mode,
            "notify_on_cancel": True,
        }
        if mode == "basic":
            return Form(
                **common,
                elements=(
                    TextInput(name="title", label="Title"),
                    TextArea(name="detail", label="Detail", optional=True),
                    Select(name="choice", label="Choice", options=OPTIONS, default="a"),
                    MultiSelect(name="choices", label="Choices", options=OPTIONS, default_values=("a",)),
                    Checkbox(name="enabled", label="Enabled", default=False, optional=True),
                    Radio(name="plan", label="Plan", options=OPTIONS, default="a"),
                ),
            )
        if mode == "dates":
            return Form(
                **common,
                elements=(
                    DatePicker(
                        name="date",
                        label="Date",
                        default="2026-11-12",
                        min_date="2026-01-01",
                        max_date="2027-01-01",
                    ),
                    DateTimePicker(
                        name="time",
                        label="Date and time",
                        default="2026-11-12T10:30:00+03:00",
                        time_interval=30,
                        location_timezone="Europe/Simferopol",
                        manual_time_entry=True,
                    ),
                ),
            )
        if mode == "bounds":
            return Form(
                **common,
                elements=(
                    DatePicker(
                        name="bounded_date",
                        label="Bounded date",
                        default="2026-11-12",
                        min_date="2026-11-10",
                        max_date="2026-11-14",
                    ),
                ),
            )
        if mode == "timezone":
            return Form(
                **common,
                elements=(
                    DateTimePicker(
                        name="offset_time",
                        label="Kolkata time",
                        default="2026-11-12T18:30:00+05:30",
                        time_interval=30,
                        location_timezone="Asia/Kolkata",
                        manual_time_entry=False,
                    ),
                ),
            )
        if mode == "labels":
            return Form(
                **common,
                elements=tuple(
                    field
                    for position in ("before", "after")
                    for field in (
                        Checkbox(
                            name="bool_" + position,
                            label="Checkbox " + position,
                            placeholder="Inline caption " + position,
                            label_position=position,
                            optional=True,
                            default=False,
                        ),
                        Radio(
                            name="radio_" + position,
                            label="Radio " + position,
                            label_position=position,
                            options=OPTIONS,
                            default="a",
                        ),
                        CheckboxGroup(
                            name="group_" + position,
                            label="Group " + position,
                            label_position=position,
                            options=OPTIONS,
                            default_values=("a",),
                        ),
                    )
                ),
            )
        if mode == "modern":
            return Form(
                **common,
                elements=(
                    TextInput(name="title", label="Modern title"),
                    FileInput(name="file", label="Modern file", optional=True),
                    DialogButton(
                        name="child",
                        label="Open child",
                        action_button=ChildAction(url=self.manager.callback_url, context={"level": "1"}),
                    ),
                ),
            )
        if mode == "advanced":
            return Form(
                **common,
                elements=(
                    CheckboxGroup(
                        name="group",
                        label="Group",
                        options=OPTIONS,
                        default_values=("a",),
                        label_position="after",
                    ),
                    CheckboxMatrix(
                        name="matrix",
                        label="Matrix",
                        matrix_config=MatrixConfig(
                            rows=(Option(text="Row one", value="r1"), Option(text="Row two", value="r2")),
                            columns=(
                                Option(text="Column one", value="c1"),
                                Option(text="Column two", value="c2"),
                            ),
                        ),
                        default_values={"r1": ("c1",)},
                        optional=True,
                    ),
                    FieldGroup(
                        name="details",
                        label="More details",
                        collapsed=True,
                        elements=(TextInput(name="nested", label="Nested text", optional=True),),
                    ),
                    FileInput(name="file", label="Upload file", optional=True),
                    DialogButton(
                        name="child",
                        label="Open child",
                        action_button=ChildAction(url=self.manager.callback_url, context={"level": "1"}),
                    ),
                ),
            )
        if mode.startswith("child-"):
            level = int(mode[-1])
            return Form(
                **common,
                elements=(
                    TextInput(name="child_text_" + str(level), label="Child text", optional=True),
                    DialogButton(
                        name="next_child",
                        label="Open next child",
                        action_button=ChildAction(
                            url=self.manager.callback_url, context={"level": str(level + 1)}
                        ),
                    ),
                ),
            )
        if mode == "wizard":
            return Form(**common, submit_label="Next", elements=(TextInput(name="project", label="Project"),))
        if mode == "wizard-final":
            return Form(**common, elements=(TextInput(name="summary", label="Summary"),))
        if mode == "lookup":
            return Form(
                **common,
                source_url=self.manager.callback_url,
                introduction_text="Refresh " + (category or "initial"),
                elements=(
                    Select(
                        name="category", label="Category", options=OPTIONS, default=category, refresh=True
                    ),
                    Select(
                        name="pick",
                        label="Remote choice",
                        data_source="dynamic",
                        data_source_url=self.manager.callback_url,
                    ),
                    TextInput(name="comment", label="Comment", optional=True),
                ),
            )
        if mode == "cancel":
            return Form(**common, elements=(TextInput(name="title", label="Title", optional=True),))
        raise ValueError("Unknown owned UI scenario")

    def bind(self, dispatcher: Dispatcher) -> None:
        @dispatcher.slash_command()
        async def slash(event: SlashCommand) -> None:
            mode = event.text.strip()
            self.record("slash", mode=mode, actor_matches=event.from_user.id == self.state["actor_id"])
            if mode in ("blocks", "slack", "adaptive", "legacy"):
                await self.post_ui(mode)
                await event.answer("SDK " + mode + " posted")
            else:
                await event.open_dialog(self.form(mode))

        @dispatcher.dialog_submit()
        async def submit(event: DialogSubmission) -> None:
            mode = event.application_state
            if mode == "basic" and event.values["title"] == "reject":
                self.record("field_error", mode=mode)
                await event.field_errors({"title": "Choose a different title"})
                return
            values = {
                key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
                for key, value in event.values.items()
                if value is not MISSING
            }
            self.record(
                "submit",
                mode=mode,
                values=values,
                file_ids_count=len(event.values.file_ids),
                actor_matches=event.from_user.id == self.state["actor_id"],
            )
            if mode == "wizard":
                await event.next(self.form("wizard-final"))
            else:
                await event.answer()
                await self.bot.send_message(self.state["channel_id"], "SDK UI accepted " + mode)

        @dispatcher.dialog_refresh()
        async def refresh(event: DialogRefresh) -> None:
            category = event.values["category"]
            if not isinstance(category, str):
                raise ValueError("Category must be a native selected value")
            self.record("refresh", category=category)
            await event.replace_form(self.form("lookup", category=category))

        @dispatcher.dialog_lookup()
        async def lookup(event: DialogLookup) -> None:
            self.record("lookup", query=event.query, field=event.selected_field)
            await event.answer(
                (Option(text="Remote Alpha", value="remote-a"), Option(text="Remote Beta", value="remote-b"))
            )

        @dispatcher.dialog_cancel()
        async def cancel(event: DialogCancel) -> None:
            self.record("cancel", mode=event.application_state)
            await event.answer()

        @dispatcher.dialog_action()
        async def child(event: DialogAction) -> None:
            level = event.application_context["level"]
            self.record("child_action", level=level, field=event.field_name)
            await event.open_dialog(self.form("child-" + level))

        @dispatcher.callback_query()
        async def action(event: CallbackQuery) -> None:
            self.record(
                "callback",
                data=event.data,
                selected_option=event.selected_option,
                actor_matches=event.from_user.id == self.state["actor_id"],
            )
            await event.answer("SDK callback verified")

        @dispatcher.error()
        async def error(event: ErrorEvent) -> None:
            self.record("handler_error", exception=type(event.exception).__name__)

    async def post_ui(self, mode: str) -> None:
        external = External(url=self.endpoint + "/callback", data="ui:" + mode)
        if mode == "legacy":
            binding = ActionContext(self.signer, self.state["channel_id"], user_id=self.state["actor_id"])
            post = await self.bot.send_message(
                self.state["channel_id"],
                "SDK legacy attachment fallback",
                attachments=(
                    Attachment(
                        fallback="Legacy rich attachment with signed actions",
                        color="#336699",
                        pretext="Legacy UI fixture",
                        title="SDK legacy attachment",
                        text="**Native attachment text**",
                        author_name="Owned SDK fixture",
                        fields=(AttachmentField(title="Short field", value="Visible value", short=True),),
                        footer="SDK legacy footer",
                        actions=Actions(
                            items=(
                                LegacyButton(
                                    id="legacyrun",
                                    name="Run legacy action",
                                    style="primary",
                                    action=external,
                                ),
                                LegacyButton(
                                    id="legacydisabled",
                                    name="Disabled legacy action",
                                    disabled=True,
                                    action=external,
                                ),
                                LegacySelect(
                                    id="legacypick",
                                    name="Pick legacy option",
                                    options=OPTIONS,
                                    action=External(url=self.endpoint + "/callback", data="ui:legacy-select"),
                                ),
                            )
                        ),
                    ),
                ),
                action_context=binding,
                ui_profile=self.profile,
            )
            self.posts.append(post.post_id)
            self.flush()
            return
        if mode == "slack":
            blocks = from_slack_blocks(
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "*Imported Slack section*"}},
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Run Slack action"},
                                "action_id": "slack_run",
                                "value": "ui:slack",
                            }
                        ],
                    },
                ],
                actions={"slack_run": external},
            )
        elif mode == "adaptive":
            blocks = from_adaptive_cards(
                [
                    {
                        "type": "AdaptiveCard",
                        "version": "1.5",
                        "body": [{"type": "TextBlock", "text": "**Imported Adaptive card**", "wrap": True}],
                        "actions": [
                            {
                                "type": "Action.Submit",
                                "id": "adaptive_run",
                                "title": "Run Adaptive action",
                                "data": "ui:adaptive",
                            }
                        ],
                    }
                ],
                actions={"adaptive_run": external},
            )
        else:
            blocks = (
                Text(text="**Native Blocks catalog**"),
                Image(
                    url=self.state["base_url"] + "/static/images/favicon/android-chrome-192x192.png",
                    alt_text="Mattermost fixture image",
                    size="small",
                ),
                Divider(),
                Container(
                    border=True,
                    accent_color="primary",
                    background="gray",
                    gap="small",
                    content=(
                        Text(text="Grouped native controls"),
                        Button(text="Run native action", action_id="native_run", action=external),
                        StaticSelect(
                            action_id="native_pick",
                            action=External(url=self.endpoint + "/callback", data="ui:select"),
                            placeholder="Pick native option",
                            options=OPTIONS,
                        ),
                    ),
                ),
                Collapsible(
                    header=(Text(text="Expand native content"),),
                    content=(Text(text="Expanded native content"),),
                    collapsed=True,
                ),
                ColumnSet(
                    columns=(
                        Column(width="auto", items=(Text(text="Left native column"),)),
                        Column(width="stretch", items=(Text(text="Right native column"),)),
                    )
                ),
            )
        binding = ActionContext(self.signer, self.state["channel_id"], user_id=self.state["actor_id"])
        message = "SDK " + mode + " notification fallback"
        post = await self.bot.send_message(
            self.state["channel_id"], message, blocks=blocks, ui_profile=self.profile, action_context=binding
        )
        self.posts.append(post.post_id)
        self.flush()


class RecordingAdapter(HttpAdapter):
    def __init__(self, *args: Any, scenario: Scenario, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scenario = scenario
        self.private: list[dict[str, Any]] = []

    async def handle(self, **kwargs: Any) -> HTTPResult:
        result = await super().handle(**kwargs)
        self.private.append(
            {
                "path": kwargs["path"],
                "body": kwargs["body"].decode("utf-8"),
                "status": result.status,
                "response": result.payload,
            }
        )
        write_json(PRIVATE_CAPTURE, self.private)
        self.scenario.http.append(
            {
                "path": kwargs["path"],
                "status": result.status,
                "response_type": result.payload.get("type"),
                "has_field_errors": "errors" in result.payload,
            }
        )
        self.scenario.flush()
        return result


async def serve() -> None:
    state = read_json(STATE)
    base = base_url()
    if state["base_url"] != base:
        raise RuntimeError("UI stand address changed; refresh its setup/browser state first")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(TLS / "server.pem", TLS / "server.key")
    if STOP.exists():
        STOP.unlink()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
        control = Control(base, client)
        _, admin_token = await control.login(state, "admin")
        _, actor_token = await control.login(state, "actor")
        for identifier in state.get("owned_commands", []):
            await control.request("DELETE", "commands/" + identifier, token=admin_token)
        state["owned_commands"] = []
        write_json(STATE, state)
        command: dict[str, Any] = {}
        peers: list[str] = []

        async def probe(request: web.Request) -> web.Response:
            payload = await request.post()
            if (
                not command
                or not secrets.compare_digest(str(payload.get("token", "")), command["token"])
                or payload.get("user_id") != state["actor_id"]
            ):
                return web.json_response({}, status=403)
            if request.remote is None:
                raise RuntimeError("Owned UI probe did not have a transport peer")
            peers.append(str(ipaddress.ip_address(request.remote)))
            return web.json_response({"text": "UI transport probe accepted"})

        probe_app = web.Application()
        probe_app.router.add_post("/probe", probe)
        probe_runner = web.AppRunner(probe_app, access_log=None)
        await probe_runner.setup()
        await web.TCPSite(probe_runner, "127.0.0.1", 0, ssl_context=context).start()
        port = probe_runner.addresses[0][1]
        trigger = "mgui" + secrets.token_hex(3)
        try:
            command, _ = await control.request(
                "POST",
                "commands",
                {
                    "team_id": state["team_id"],
                    "trigger": trigger,
                    "method": "P",
                    "url": f"https://host.docker.internal:{port}/probe",
                    "display_name": "Owned UI SDK",
                    "auto_complete": True,
                },
                token=admin_token,
            )
            state.setdefault("owned_commands", []).append(command["id"])
            write_json(STATE, state)
            await control.request(
                "POST",
                "commands/execute",
                {"channel_id": state["channel_id"], "command": "/" + trigger + " probe"},
                token=actor_token,
            )
            if not peers:
                raise RuntimeError("TLS/native issued-command probe never reached the owned callback host")
        except BaseException:
            if command:
                await control.request("DELETE", "commands/" + command["id"], token=admin_token)
                state["owned_commands"].remove(command["id"])
                write_json(STATE, state)
            raise
        finally:
            await probe_runner.cleanup()
        bot = Bot(token=state["bot_token"], base_url=base)
        dispatcher = Dispatcher()
        await bot.get_me()
        scenario = Scenario(state, bot, dispatcher)
        adapter = RecordingAdapter(
            bot,
            dispatcher,
            scenario=scenario,
            trusted_peers=[peers[-1] + "/32"],
            slash_bindings=[SlashBinding(state["team_id"], trigger, SecretStr(command["token"]))],
            callback_signer=scenario.signer,
            dialogs=scenario.manager,
            own_dialogs=True,
            request_timeout=2.5,
        )
        runner = web.AppRunner(adapter.create_app(), access_log=None)
        try:
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context).start()
            port = runner.addresses[0][1]
            scenario.endpoint = f"https://host.docker.internal:{port}/mattergram"
            scenario.manager.set_callback_url(scenario.endpoint + "/dialog")
            await control.request(
                "PUT",
                "commands/" + command["id"],
                {**command, "url": scenario.endpoint + "/slash"},
                token=admin_token,
            )
            scenario.status.update(
                {
                    "state": "ready",
                    "command": "/" + trigger,
                    "endpoint": scenario.endpoint,
                    "peer": peers[-1],
                    "ca_sha256": sha256((TLS / "ca.pem").read_bytes()),
                    "started_at": time.time(),
                }
            )
            scenario.flush()
            print(
                json.dumps(
                    {
                        "state": "ready",
                        "command": "/" + trigger,
                        "channel": base + "/mattergram-ui/channels/ui-scenarios",
                    }
                ),
                flush=True,
            )
            while not STOP.exists():
                await asyncio.sleep(0.5)
        finally:
            await runner.cleanup()
            await dispatcher.close()
            await bot.close()
            await control.request("DELETE", "commands/" + command["id"], token=admin_token)
            state["owned_commands"].remove(command["id"])
            write_json(STATE, state)
            scenario.status["state"] = "closed"
            scenario.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tls", "setup", "serve", "stop"))
    parser.add_argument("--profile", choices=tuple(RELEASES), default="12.0.2")
    args = parser.parse_args()
    configure_profile(args.profile)
    if args.command == "tls":
        make_tls()
    elif args.command == "stop":
        STOP.touch()
    else:
        asyncio.run(setup() if args.command == "setup" else serve())


if __name__ == "__main__":
    main()
