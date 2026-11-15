"""Owned aiohttp transport for the first native Mattermost REST slice."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

import aiohttp
from pydantic import SecretStr

from mattergram._http_client import _HTTPClient
from mattergram.exceptions import (
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RequestOutcomeUnknown,
    TransportError,
)
from mattergram.types import Channel, InputFile, Message, User

if TYPE_CHECKING:
    from mattergram.provisioning.resources import RuntimeResources
    from mattergram.ui.actions import Action, ActionContext, Actions, Attachment
    from mattergram.ui.blocks import Block
    from mattergram.ui.capabilities import UIProfile
    from mattergram.ui.composer import ComposerKeyboard, ComposerKeyboardState
    from mattergram.ui.forms import Form


def _segment(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in "/\\?#%" or character.isspace() or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} must be one nonempty native ID path segment")
    return quote(value, safe="")


class Bot(_HTTPClient):
    """One owner, one event loop, one HTTP pool; close is terminal and idempotent.

    base_url is the server root including an optional deployment subpath. This
    slice never retries requests or follows redirects with a bot credential.
    """

    _resources: RuntimeResources | None = None

    @property
    def resources(self) -> RuntimeResources:
        if self._resources is None:
            raise RuntimeError("This Bot has no prepared provisioning resources")
        return self._resources

    async def _events(self) -> AsyncGenerator[dict[str, Any], None]:
        """One authenticated WS connection; the dispatcher owns reconnect policy."""
        session = self._get_session()
        try:
            async with session.ws_connect(self._api_url + "websocket", heartbeat=30) as socket:
                await socket.send_json(
                    {"seq": 1, "action": "authentication_challenge", "data": {"token": self._token}}
                )
                pending: list[dict[str, Any]] = []
                for _ in range(32):
                    try:
                        reply = await socket.receive_json(timeout=self._timeout)
                    except (ValueError, TypeError):
                        raise ProtocolError("Invalid WebSocket authentication response") from None
                    if not isinstance(reply, dict):
                        raise ProtocolError("Invalid WebSocket authentication response")
                    if reply.get("seq_reply") == 1:
                        if reply.get("status") != "OK":
                            raise AuthenticationError(401)
                        break
                    pending.append(reply)
                else:
                    raise ProtocolError("WebSocket authentication was not acknowledged")
                for event in pending:
                    yield event
                async for frame in socket:
                    if frame.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(frame.data)
                        except ValueError:
                            raise ProtocolError("WebSocket event contains invalid JSON") from None
                        if not isinstance(event, dict):
                            raise ProtocolError("WebSocket event must be an object")
                        if "event" in event:
                            yield event
                    elif frame.type == aiohttp.WSMsgType.ERROR:
                        raise TransportError("Mattermost WebSocket failed")
        except aiohttp.ClientResponseError as error:
            kind = {401: AuthenticationError, 403: PermissionDeniedError}.get(error.status, APIError)
            raise kind(error.status) from None
        except (aiohttp.ClientError, TimeoutError):
            raise TransportError("Mattermost WebSocket connection failed") from None

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        root_id: str | None = None,
        actions: Actions | None = None,
        blocks: Sequence[Block] | None = None,
        attachments: Sequence[Attachment] | None = None,
        ui_profile: UIProfile | None = None,
        action_context: ActionContext | None = None,
        markdown_actions: dict[str, Action] | None = None,
    ) -> Message:
        if not isinstance(chat_id, str) or not chat_id:
            raise ValueError("chat_id must be a nonempty channel ID string")
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if root_id is not None and (not isinstance(root_id, str) or not root_id):
            raise ValueError("root_id must be a nonempty post ID string or None")
        payload: dict[str, Any] = {"channel_id": chat_id, "message": text}
        if root_id is not None:
            payload["root_id"] = root_id
        if action_context is not None and action_context.channel_id != chat_id:
            raise ValueError("UI action context belongs to another channel")
        if any(value is not None for value in (actions, blocks, attachments, markdown_actions)):
            from mattergram.ui.blocks import compile_post_ui

            payload["props"] = compile_post_ui(
                blocks=blocks,
                actions=actions,
                attachments=attachments,
                profile=ui_profile,
                context=action_context,
                markdown_actions=markdown_actions,
                message=text,
            )
        return Message.from_wire(await self._request("POST", "posts", 201, payload), bot=self)

    async def set_composer_keyboard(self, chat_id: str, keyboard: ComposerKeyboard) -> ComposerKeyboardState:
        """Create or replace the plugin-backed persistent keyboard for one channel."""
        from mattergram.ui.composer import ComposerKeyboard, ComposerKeyboardState

        channel_id = _segment(chat_id, "chat_id")
        if not isinstance(keyboard, ComposerKeyboard):
            raise TypeError("keyboard must be a ComposerKeyboard")
        wire = await self._request_plugin_json(
            "com.mattergram.menu",
            "api/v1/channels/" + channel_id + "/keyboard",
            "PUT",
            200,
            keyboard.to_wire(),
        )
        try:
            return ComposerKeyboardState.model_validate(wire)
        except (TypeError, ValueError):
            raise ProtocolError("Mattergram Menu returned invalid keyboard state") from None

    async def get_composer_keyboard(self, chat_id: str) -> ComposerKeyboardState | None:
        """Read the active keyboard; ``None`` means no keyboard is configured."""
        from mattergram.ui.composer import ComposerKeyboardState

        channel_id = _segment(chat_id, "chat_id")
        wire = await self._request_plugin_json(
            "com.mattergram.menu",
            "api/v1/channels/" + channel_id + "/keyboard",
            "GET",
            200,
        )
        if wire == {"keyboard": None}:
            return None
        try:
            return ComposerKeyboardState.model_validate(wire)
        except (TypeError, ValueError):
            raise ProtocolError("Mattergram Menu returned invalid keyboard state") from None

    async def delete_composer_keyboard(self, chat_id: str) -> None:
        """Remove the plugin-backed persistent keyboard from one channel."""
        channel_id = _segment(chat_id, "chat_id")
        wire = await self._request_plugin_json(
            "com.mattergram.menu",
            "api/v1/channels/" + channel_id + "/keyboard",
            "DELETE",
            200,
        )
        if wire != {"status": "OK"}:
            raise ProtocolError("Mattergram Menu did not confirm keyboard deletion")

    async def _get_message(self, message_id: str) -> Message:
        message = Message.from_wire(
            await self._request("GET", "posts/" + _segment(message_id, "message_id"), 200),
            bot=self,
        )
        if message.post_id != message_id:
            raise ProtocolError("Mattermost returned another post")
        delete_at = message.raw.get("delete_at", 0)
        if type(delete_at) is int and delete_at > 0:
            raise APIError(404)
        return message

    async def _write_message(
        self,
        method: str,
        path: str,
        status: int,
        payload: Any,
        *,
        message_id: str | None = None,
        channel_id: str | None = None,
    ) -> Message:
        try:
            message = Message.from_wire(await self._request(method, path, status, payload), bot=self)
        except ProtocolError:
            raise RequestOutcomeUnknown(
                "Native post write response was not confirmed; reconcile before retrying"
            ) from None
        if (message_id is not None and message.post_id != message_id) or (
            channel_id is not None and message.channel_id != channel_id
        ):
            raise RequestOutcomeUnknown(
                "Native post write returned another resource; reconcile before retrying"
            )
        return message

    async def get_user(self, user_id: str) -> User:
        return User.from_wire(await self._request("GET", "users/" + _segment(user_id, "user_id"), 200))

    async def get_users_in_team(
        self,
        team_id: str,
        *,
        page: int = 0,
        per_page: int = 100,
        active: bool = True,
    ) -> list[User]:
        """Return one native page of users belonging to a Mattermost team.

        The authenticated bot must be able to view the team. Applications own
        pagination and must keep their application roles separate from native
        Mattermost roles.
        """

        _segment(team_id, "team_id")
        if type(page) is not int or page < 0:
            raise ValueError("page must be a nonnegative integer")
        if type(per_page) is not int or per_page < 1 or per_page > 200:
            raise ValueError("per_page must be an integer between 1 and 200")
        if type(active) is not bool:
            raise TypeError("active must be a bool")
        query = urlencode(
            {
                "in_team": team_id,
                "page": page,
                "per_page": per_page,
                "active": str(active).lower(),
            }
        )
        payload = await self._request_json("GET", "users?" + query, 200)
        if not isinstance(payload, list):
            raise ProtocolError("Mattermost team users response must be a JSON array")
        users: list[User] = []
        for item in payload:
            users.append(User.from_wire(item))
        if len({user.id for user in users}) != len(users):
            raise ProtocolError("Mattermost team users response contains duplicate IDs")
        return users

    async def get_channel(self, channel_id: str) -> Channel:
        return Channel.from_wire(
            await self._request("GET", "channels/" + _segment(channel_id, "channel_id"), 200)
        )

    async def create_direct_channel(self, user_id: str) -> Channel:
        target_id = _segment(user_id, "user_id")
        own_id = _segment(self.user_id or (await self.get_me()).id, "bot user_id")
        if target_id == own_id:
            raise ValueError("A direct channel requires another user")
        try:
            channel = Channel.from_wire(
                await self._request("POST", "channels/direct", 201, [own_id, target_id])
            )
        except ProtocolError:
            raise RequestOutcomeUnknown(
                "Direct-channel write response was not confirmed; reconcile before retrying"
            ) from None
        if channel.type != "D":
            raise RequestOutcomeUnknown(
                "Direct-channel write returned another channel type; reconcile before retrying"
            )
        return channel

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        props: Mapping[str, Any] | None = None,
    ) -> Message:
        channel_id = _segment(chat_id, "chat_id")
        post_id = _segment(message_id, "message_id")
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if props is not None and not isinstance(props, Mapping):
            raise TypeError("props must be a mapping or None")
        current = await self._get_message(post_id)
        if current.channel_id != channel_id:
            raise ValueError("message_id belongs to another channel")
        payload: dict[str, Any] = {"id": post_id, "message": text}
        if props is not None:
            payload["props"] = deepcopy(dict(props))
        return await self._write_message(
            "PUT",
            f"posts/{post_id}/patch",
            200,
            payload,
            message_id=post_id,
            channel_id=channel_id,
        )

    async def delete_message(self, chat_id: str, message_id: str) -> None:
        channel_id = _segment(chat_id, "chat_id")
        post_id = _segment(message_id, "message_id")
        current = await self._get_message(post_id)
        if current.channel_id != channel_id:
            raise ValueError("message_id belongs to another channel")
        try:
            result = await self._request("DELETE", "posts/" + post_id, 200)
        except ProtocolError:
            raise RequestOutcomeUnknown(
                "Native post deletion was not confirmed; reconcile before retrying"
            ) from None
        if result.get("status") != "OK":
            raise RequestOutcomeUnknown("Native post deletion lacked status OK; reconcile before retrying")

    async def send_document(
        self,
        chat_id: str,
        document: InputFile,
        *,
        text: str | None = None,
        root_id: str | None = None,
    ) -> Message:
        channel_id = _segment(chat_id, "chat_id")
        if not isinstance(document, InputFile):
            raise TypeError("document must be an InputFile")
        if text is not None and not isinstance(text, str):
            raise TypeError("text must be a string or None")
        if root_id is not None:
            _segment(root_id, "root_id")
        form = aiohttp.FormData()
        form.add_field("channel_id", channel_id)
        form.add_field(
            "files",
            document.read(),
            filename=document.filename,
            content_type=document.content_type,
        )
        try:
            upload = await self._request_multipart("POST", "files", 201, form)
            if not isinstance(upload, dict):
                raise ProtocolError("File upload response must be an object")
            file_infos = upload.get("file_infos")
            if (
                not isinstance(file_infos, list)
                or len(file_infos) != 1
                or not isinstance(file_infos[0], dict)
                or not isinstance(file_infos[0].get("id"), str)
                or not file_infos[0]["id"]
            ):
                raise ProtocolError("File upload response omitted one native file ID")
        except ProtocolError:
            raise RequestOutcomeUnknown(
                "Native file upload response was not confirmed; reconcile before retrying"
            ) from None
        file_id = file_infos[0]["id"]
        payload: dict[str, Any] = {
            "channel_id": channel_id,
            "message": text or "",
            "file_ids": [file_id],
        }
        if root_id is not None:
            payload["root_id"] = root_id
        try:
            return await self._write_message("POST", "posts", 201, payload, channel_id=channel_id)
        except (APIError, TransportError) as error:
            error.add_note(f"Uploaded native file {file_id!r} may remain unattached")
            raise

    async def open_dialog(self, trigger_id: SecretStr, form: Form, *, url: str, profile: UIProfile) -> None:
        """Native operation; only a real, unexpired user trigger can open a form."""
        await self._open_dialog_wire(trigger_id, url=url, dialog=form.to_wire(profile))

    async def _open_dialog_wire(self, trigger_id: SecretStr, *, url: str, dialog: Mapping[str, Any]) -> None:
        from mattergram.ui.base import integration_url

        if not isinstance(trigger_id, SecretStr) or not trigger_id.get_secret_value():
            raise ValueError("A nonempty SecretStr user trigger is required")
        integration_url(url)
        secrets = (trigger_id.get_secret_value(), str(dialog.get("state", "")))
        result = await self._request(
            "POST",
            "actions/dialogs/open",
            200,
            {"trigger_id": trigger_id.get_secret_value(), "url": url, "dialog": dict(dialog)},
            secrets=secrets,
        )
        if result.get("status") != "OK":
            raise ProtocolError("Dialog opening was not confirmed by a native OK response")
