"""Pure authenticated normalization; peer comes from transport, never body/XFF."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from mattergram.types import CallbackQuery, SlashCommand

from .auth import CallbackSigner, SlashBinding, TrustedPeers
from .responses import ResponseController


def normalize_slash(
    payload: Mapping[str, Any],
    *,
    binding: SlashBinding,
    peers: TrustedPeers,
    peer: str | None,
    method: str = "POST",
    authorization: str | None = None,
    response: ResponseController | None = None,
) -> SlashCommand:
    peers.verify(peer)
    binding.verify(payload, method=method, authorization=authorization)
    event = SlashCommand.from_wire(payload)
    event._response = response
    return event


def normalize_callback(
    payload: Mapping[str, Any],
    *,
    signer: CallbackSigner,
    peers: TrustedPeers,
    peer: str | None,
    response: ResponseController | None = None,
) -> CallbackQuery:
    peers.verify(peer)
    checked = signer.verify(payload)
    event = CallbackQuery.from_wire({**payload, **checked})
    event._raw = deepcopy(dict(payload))
    event._response = response
    return event
