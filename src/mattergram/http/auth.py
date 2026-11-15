from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
import time
from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qsl

from pydantic import SecretStr

from mattergram.exceptions import IngressAuthenticationError, ProtocolError


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Native identity must be a nonempty string")
    return value


@dataclass(frozen=True)
class SlashBinding:
    team_id: str | None
    command: str
    token: SecretStr = field(repr=False)
    method: Literal["GET", "POST"] = "POST"

    def __post_init__(self) -> None:
        if self.team_id is not None:
            _identifier(self.team_id)
        _identifier(self.command)
        if self.command.startswith("/") or any(char.isspace() for char in self.command):
            raise ValueError("Binding command is the trigger without slash or whitespace")
        if not isinstance(self.token, SecretStr) or not self.token.get_secret_value():
            raise ValueError("Binding requires the actual integration token as SecretStr")
        if self.method not in ("GET", "POST"):
            raise ValueError("Slash bindings support GET or POST")

    def verify(self, payload: Mapping[str, Any], *, method: str, authorization: str | None = None) -> None:
        expected = self.token.get_secret_value().encode()
        supplied = payload.get("token")
        if (
            method != self.method
            or payload.get("team_id", "") != (self.team_id or "")
            or payload.get("command") != "/" + self.command
            or not isinstance(supplied, str)
            or not hmac.compare_digest(supplied.encode(), expected)
        ):
            raise IngressAuthenticationError("Invalid slash credentials or scope")
        if authorization is not None:
            scheme, _, header_token = authorization.partition(" ")
            if scheme.casefold() == "token" and not hmac.compare_digest(header_token.encode(), expected):
                raise IngressAuthenticationError("Conflicting slash authorization header")


class TrustedPeers:
    """Explicit transport peers. Never uses user-controlled Forwarded/X-Forwarded-For."""

    def __init__(self, networks: Collection[str]) -> None:
        self.networks = tuple(ipaddress.ip_network(value, strict=True) for value in networks)
        if not self.networks:
            raise ValueError("Configure a trusted server-to-server path explicitly")

    def verify(self, peer: str | None) -> None:
        try:
            address = ipaddress.ip_address(peer or "")
        except ValueError:
            raise IngressAuthenticationError("Untrusted integration source") from None
        if not any(address.version == network.version and address in network for network in self.networks):
            raise IngressAuthenticationError("Untrusted integration source")


class CallbackSigner:
    """Protect application context and explicit bindings, not the entire HTTP body.

    Actor identity still requires the separately trusted transport peer/path.
    Repeated intentional presses remain valid until expiry; this is not deduplication.
    """

    def __init__(
        self,
        *,
        instance_id: str,
        bot_id: str,
        keys: Mapping[str, bytes],
        active_key_id: str,
        max_ttl: float = 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.instance_id = _identifier(instance_id)
        self.bot_id = _identifier(bot_id)
        self._keys = dict(keys)
        if not self._keys or any(
            not isinstance(key, str) or not key or not isinstance(value, bytes) or len(value) < 32
            for key, value in self._keys.items()
        ):
            raise ValueError("Each signing key needs an ID and at least 32 secret bytes")
        if active_key_id not in self._keys:
            raise ValueError("Active signing key is missing")
        if (
            isinstance(max_ttl, bool)
            or not isinstance(max_ttl, (int, float))
            or not math.isfinite(max_ttl)
            or max_ttl <= 0
        ):
            raise ValueError("max_ttl must be positive and finite")
        self._active_key_id = active_key_id
        self.max_ttl = float(max_ttl)
        self._clock = clock

    @staticmethod
    def _canonical(payload: Mapping[str, Any]) -> bytes:
        return json.dumps(
            dict(payload), sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()

    def seal(
        self,
        data: str,
        *,
        channel_id: str,
        post_id: str | None = None,
        user_id: str | None = None,
        team_id: str | None = None,
        thread_id: str | None = None,
        allowed_options: Collection[str] | None = None,
        ttl: float = 900,
    ) -> dict[str, Any]:
        if not isinstance(data, str) or len(data.encode()) > 4096:
            raise ValueError("Callback data exceeds the SDK limit of 4096 UTF-8 bytes")
        for value in (channel_id,):
            _identifier(value)
        for optional_value in (post_id, user_id, team_id, thread_id):
            if optional_value is not None:
                _identifier(optional_value)
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (float, int))
            or not math.isfinite(ttl)
            or not 0 < ttl <= self.max_ttl
        ):
            raise ValueError("Callback TTL is outside the configured bound")
        if allowed_options is not None and (
            not isinstance(allowed_options, Collection)
            or isinstance(allowed_options, str)
            or any(not isinstance(value, str) for value in allowed_options)
        ):
            raise ValueError("allowed_options must be a collection of strings")
        now = self._clock()
        payload = {
            "v": 1,
            "kid": self._active_key_id,
            "iat": now,
            "exp": now + ttl,
            "instance_id": self.instance_id,
            "bot_id": self.bot_id,
            "channel_id": channel_id,
            "post_id": post_id,
            "user_id": user_id,
            "team_id": team_id,
            "thread_id": thread_id,
            "data": data,
            "allowed_options": list(allowed_options) if allowed_options is not None else None,
        }
        signature = hmac.new(
            self._keys[self._active_key_id], self._canonical(payload), hashlib.sha256
        ).hexdigest()
        return {"mattergram": {"payload": payload, "signature": signature}}

    def verify(self, wire: Mapping[str, Any]) -> dict[str, Any]:
        try:
            context = wire["context"]
            record = context["mattergram"]
            payload = record["payload"]
            signature = record["signature"]
            if not isinstance(payload, dict) or not isinstance(signature, str):
                raise ValueError
            key = self._keys[payload["kid"]]
            expected = hmac.new(key, self._canonical(payload), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            now = self._clock()
            issued, expires = payload["iat"], payload["exp"]
            if any(
                isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)
                for value in (issued, expires)
            ):
                raise ValueError
            if (
                type(payload["v"]) is not int
                or payload["v"] != 1
                or not issued <= now < expires
                or not 0 < expires - issued <= self.max_ttl
            ):
                raise ValueError
            if payload["instance_id"] != self.instance_id or payload["bot_id"] != self.bot_id:
                raise ValueError
            for name in ("channel_id", "post_id", "user_id", "team_id"):
                if payload[name] is not None and payload[name] != wire.get(name):
                    raise ValueError
            if not isinstance(payload["data"], str) or len(payload["data"].encode()) > 4096:
                raise ValueError
            selected = context.get("selected_option")
            if selected is not None and not isinstance(selected, str):
                raise ValueError
            if payload["allowed_options"] is not None and selected not in payload["allowed_options"]:
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise IngressAuthenticationError("Invalid, expired or out-of-scope callback context") from None
        return {"data": payload["data"], "selected_option": selected, "thread_id": payload["thread_id"]}


def decode_json(body: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> Any:
        raise ValueError("Non-finite JSON constant")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in values:
            if key in output:
                raise ValueError
            output[key] = value
        return output

    try:
        data = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("Invalid or ambiguous integration JSON") from None
    if not isinstance(data, dict):
        raise ProtocolError("Integration body must be an object")
    return deepcopy(data)


def decode_form(body: bytes) -> dict[str, str]:
    try:
        text = body.decode("utf-8")
        if re.search(r"%(?![0-9a-fA-F]{2})", text):
            raise ValueError
        pairs = parse_qsl(
            text,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=64,
        )
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError
        return data
    except (ValueError, UnicodeError):
        raise ProtocolError("Invalid or ambiguous integration form") from None
