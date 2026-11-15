"""Managed native form sessions, opaque scoped capabilities and replay control.

The record is claimed before a business handler starts. An interrupted claimed
request is indeterminate and requires explicit reconciliation, never blind retry.
This is not an atomic transaction with application business systems.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from mattergram.bot import Bot
from mattergram.exceptions import (
    IngressAuthenticationError,
    IngressCapacityError,
    ProtocolError,
    ResponseStateError,
    StorageDataError,
)
from mattergram.fsm.storage.base import StorageKey
from mattergram.types import (
    CallbackQuery,
    DialogAction,
    DialogCancel,
    DialogEvent,
    DialogLookup,
    DialogRefresh,
    DialogSubmission,
    SlashCommand,
)
from mattergram.ui.base import integration_url
from mattergram.ui.capabilities import UIProfile
from mattergram.ui.form_defaults import preserve_form_values
from mattergram.ui.form_snapshot import restore_form, snapshot_form
from mattergram.ui.form_values import input_fields, parse_form_values
from mattergram.ui.forms import Collapsible, DialogButton, Form, FormElement, Select

from .dialog_store import DialogStore, MemoryDialogStore
from .responses import ResponseController, ResponseKind

if TYPE_CHECKING:
    from .adapter import HTTPResult


class _Capability(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    revision: int
    callback_id: str
    operation: Literal["form", "action"]
    field: str | None = None


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    expires_at: float
    profile: str
    user_id: str
    channel_id: str
    team_id: str | None
    thread_id: str | None = None
    parent_id: str | None = None
    active_child: str | None = None
    depth: int = Field(default=1, ge=1, le=3)
    revision: int = Field(default=0, ge=0)
    status: Literal["opening", "open", "running", "indeterminate", "closed"] = "opening"
    form: str = Field(repr=False)
    prior_forms: list[str] = Field(default_factory=list, repr=False)
    answers: dict[str, Any] = Field(default_factory=dict, repr=False)
    file_ids: list[str] = Field(default_factory=list, repr=False)
    capabilities: dict[str, _Capability] = Field(default_factory=dict, repr=False)
    responses: dict[str, dict[str, Any]] = Field(default_factory=dict, repr=False)
    final_response: dict[str, Any] | None = Field(default=None, repr=False)
    running_fingerprint: str | None = None


@dataclass(frozen=True)
class DialogHandle:
    """Application session identity and secret capability, never a platform dialog ID."""

    session_id: str
    state: SecretStr = dataclass_field(repr=False)
    expires_at: float


def _result(status: int, payload: dict[str, Any]) -> HTTPResult:
    from .adapter import HTTPResult

    return HTTPResult(status, payload)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _all_elements(form: Form) -> dict[str, FormElement]:
    result: dict[str, FormElement] = {}

    def visit(elements: Any) -> None:
        for element in elements:
            result[element.name] = element
            if isinstance(element, Collapsible):
                visit(element.elements)

    visit(form.elements)
    return result


class DialogManager:
    def __init__(
        self,
        bot: Bot,
        *,
        profile: UIProfile,
        callback_url: str | None = None,
        store: DialogStore | None = None,
        ttl: float = 900,
        max_steps: int = 32,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("Dialog lifetime must be positive and finite")
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("A positive managed step limit is required")
        self.bot = bot
        self.profile = profile
        self.store = store if store is not None else MemoryDialogStore(clock=clock)
        self._owns_store = store is None
        if ttl > self.store.max_ttl:
            raise ValueError("Dialog lifetime cannot exceed the backend record TTL")
        self.ttl = float(ttl)
        self.max_steps = max_steps
        self._clock = clock
        self._bot_id: str | None = None
        self._closed = False
        self._issued = 0
        self._callback_url: str | None = None
        self._profile_hash = hashlib.sha256(
            _json(
                {
                    "server": profile.server_version,
                    "client": profile.client,
                    "features": sorted(profile.client_features),
                    "flags": profile.mm_blocks_enabled,
                    "overrides": sorted(profile.overrides),
                }
            ).encode()
        ).hexdigest()
        if callback_url is not None:
            self.set_callback_url(callback_url)

    def set_callback_url(self, value: str) -> None:
        if self._issued or self._closed:
            raise RuntimeError("Configure the callback endpoint before issuing form sessions")
        integration_url(value)
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or parsed.query or parsed.fragment:
            raise ValueError("A managed form endpoint must be an absolute URL without query/fragment")
        self._callback_url = value

    @property
    def callback_url(self) -> str:
        if self._callback_url is None:
            raise RuntimeError("Configure the reachable managed form callback URL before opening a form")
        return self._callback_url

    async def startup(self) -> None:
        if self._closed:
            raise RuntimeError("Dialog manager is closed")
        me = self.bot._me or await self.bot.get_me()
        self._bot_id = me.id

    async def close(self) -> None:
        if self._owns_store:
            await self.store.close()
        self._closed = True

    def _key(self, session_id: str) -> StorageKey:
        if self._bot_id is None or self._closed:
            raise RuntimeError("Dialog manager is not started or is closed")
        return StorageKey(
            self.bot.instance_id,
            self._bot_id,
            None,
            None,
            dialog_session_id=session_id,
            destiny="mattergram-dialog-record",
        )

    @staticmethod
    def _token_parts(token: str) -> tuple[str, str]:
        if not re.fullmatch(r"mgd1\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", token):
            raise IngressAuthenticationError("Invalid form session capability")
        return token.split(".")[1], hashlib.sha256(token.encode()).hexdigest()

    def _issue(
        self,
        record: _Record,
        session_id: str,
        *,
        operation: Literal["form", "action"] = "form",
        field: str | None = None,
    ) -> str:
        if len(record.capabilities) >= 512:
            raise IngressCapacityError("Managed form capability history is exhausted")
        token = f"mgd1.{session_id}.{secrets.token_urlsafe(32)}"
        record.capabilities[hashlib.sha256(token.encode()).hexdigest()] = _Capability(
            revision=record.revision,
            callback_id=self._transport_callback_id(record),
            operation=operation,
            field=field,
        )
        return token

    @staticmethod
    def _transport_callback_id(record: _Record) -> str:
        # Native web dialog adapters retain the opening callback ID when a
        # response replaces the current form. The current declaration remains
        # available as event.form; transport authentication keeps its original
        # callback binding, independently of step state/revision rotation.
        opening = {
            capability.callback_id
            for capability in record.capabilities.values()
            if capability.revision == 0 and capability.operation == "form"
        }
        if len(opening) > 1:
            raise StorageDataError("Stored form has conflicting opening callback identities")
        return next(iter(opening)) if opening else restore_form(record.form).callback_id

    def _prepare(self, record: _Record, session_id: str, form: Form) -> tuple[dict[str, Any], str]:
        record.form = snapshot_form(form)
        wire = form.to_wire(self.profile)
        token = self._issue(record, session_id)
        wire["callback_id"] = self._transport_callback_id(record)
        wire["state"] = token
        elements = _all_elements(form)
        if any(
            isinstance(e, Select) and (e.refresh or e.data_source == "dynamic") for e in elements.values()
        ):
            if {"query", "selected_field"}.intersection(elements):
                raise ValueError("Managed lookup/refresh metadata collides with a field name")
        for element in elements.values():
            if isinstance(element, Select):
                if element.refresh and form.source_url != self.callback_url:
                    raise ValueError("Managed refresh must use this manager's callback endpoint")
                if element.data_source == "dynamic" and element.data_source_url != self.callback_url:
                    raise ValueError("Managed lookup must use this manager's callback endpoint")
            if isinstance(element, DialogButton) and element.action_button.url != self.callback_url:
                raise ValueError("Managed action buttons must use this manager's callback endpoint")

        def bind(items: list[dict[str, Any]]) -> None:
            for item in items:
                if item["type"] == "collapsible":
                    bind(item["collapsible_config"]["elements"])
                elif item["type"] == "action_button":
                    context = item["action_button"].setdefault("context", {})
                    if "mattergram_dialog" in context:
                        raise ValueError("Reserved managed action context key")
                    context["mattergram_dialog"] = self._issue(
                        record, session_id, operation="action", field=item["name"]
                    )

        bind(wire["elements"])
        return wire, token

    async def _read(self, key: StorageKey) -> _Record:
        data = await self.store.get(key)
        if data is None:
            raise IngressAuthenticationError("Form session is absent or expired")
        try:
            record = _Record.model_validate(data)
        except ValidationError:
            raise StorageDataError("Invalid stored form session") from None
        if record.expires_at <= self._clock() or record.profile != self._profile_hash:
            raise IngressAuthenticationError("Form session expired or belongs to another profile")
        return record

    async def _put(self, key: StorageKey, record: _Record) -> None:
        await self.store.put(key, record.model_dump(mode="json"))

    async def _mark_unknown(self, key: StorageKey, record: _Record) -> None:
        record.status = "indeterminate"
        task = asyncio.create_task(self._put(key, record))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # The preceding running/opening claim is already durable. Finish
            # bounded backend I/O before releasing the session lock.
            await task
            raise

    async def open(
        self,
        event: SlashCommand | CallbackQuery | DialogAction,
        form: Form,
        *,
        parent: tuple[str, _Record] | None = None,
    ) -> DialogHandle:
        if self._bot_id is None:
            await self.startup()
        trigger = event.trigger_id
        if trigger is None or not trigger.get_secret_value():
            raise ResponseStateError("This native event has no usable dialog trigger")
        if parent is not None and parent[1].depth >= 3:
            raise ResponseStateError("Native modal stack is limited to three forms")
        thread = event.root_id if isinstance(event, SlashCommand) else event.thread_id
        session_id = uuid4().hex
        record = _Record(
            expires_at=min(self._clock() + self.ttl, parent[1].expires_at)
            if parent is not None
            else self._clock() + self.ttl,
            profile=self._profile_hash,
            user_id=event.user_id,
            channel_id=event.channel_id,
            team_id=event.team_id,
            thread_id=thread,
            parent_id=parent[0] if parent is not None else None,
            depth=parent[1].depth + 1 if parent is not None else 1,
            form=snapshot_form(form),
        )
        wire, token = self._prepare(record, session_id, form)
        key = self._key(session_id)
        async with self.store.lock(key):
            await self._put(key, record)
            if parent is not None:
                parent[1].active_child = session_id
            self._issued += 1
            try:
                await self.bot._open_dialog_wire(trigger, url=self.callback_url, dialog=wire)
            except BaseException:
                await self._mark_unknown(key, record)
                raise
            record.status = "open"
            await self._put(key, record)
        return DialogHandle(session_id, SecretStr(token), record.expires_at)

    async def handle(
        self,
        payload: Mapping[str, Any],
        *,
        deadline: float,
        serve: Callable[[DialogEvent, ResponseController], Awaitable[HTTPResult]],
    ) -> HTTPResult:
        try:
            raw = deepcopy(dict(payload))
            operation = raw.get("type")
            if operation not in ("dialog_submission", "dialog_lookup", "refresh", "dialog_action"):
                raise ProtocolError("Unknown native form event type")
            if "cancelled" in raw and type(raw["cancelled"]) is not bool:
                raise ProtocolError("Dialog cancelled must be a native boolean")
            if operation != "dialog_submission" and raw.get("cancelled") is True:
                raise ProtocolError("Cancellation is a submission event")
            if operation != "dialog_action" and not isinstance(raw.get("submission", {}), dict):
                raise ProtocolError("Dialog submission must be an object")
            if operation == "dialog_action":
                context = raw.get("context")
                token = context.get("mattergram_dialog") if isinstance(context, dict) else None
            else:
                token = raw.get("state")
            if not isinstance(token, str):
                raise IngressAuthenticationError("Form session capability is missing")
            session_id, digest = self._token_parts(token)
            key = self._key(session_id)
            async with asyncio.timeout_at(deadline):
                async with self.store.lock(key):
                    record = await self._read(key)
                    capability = record.capabilities.get(digest)
                    if capability is None:
                        raise IngressAuthenticationError("Unknown form capability")
                    if (
                        any(raw.get(name) != getattr(record, name) for name in ("user_id", "channel_id"))
                        or (raw.get("team_id") or None) != record.team_id
                    ):
                        raise IngressAuthenticationError("Form actor or native scope does not match")
                    if (operation == "dialog_action") != (capability.operation == "action"):
                        raise IngressAuthenticationError("Form capability is not valid for this operation")
                    if capability.operation == "form" and raw.get("callback_id") != capability.callback_id:
                        raise IngressAuthenticationError("Form callback ID does not match")
                    fingerprint = hashlib.sha256(_json(raw).encode()).hexdigest()
                    if record.status == "closed":
                        if operation not in ("dialog_submission",):
                            return _result(409, {"error": "dialog_session_finished"})
                        return _result(200, record.final_response or {})
                    if record.parent_id is not None:
                        parent = await self._read(self._key(record.parent_id))
                        if parent.status != "open" or parent.active_child != session_id:
                            return _result(409, {"error": "dialog_parent_not_active"})
                    if record.status in ("opening", "running", "indeterminate"):
                        return _result(409, {"error": "dialog_outcome_requires_reconciliation"})
                    if fingerprint in record.responses:
                        return _result(200, deepcopy(record.responses[fingerprint]))
                    # Native adapters keep the opening state in their lookup
                    # and cancel callbacks, while submit/refresh use the current
                    # form state. Only these session operations retain opening
                    # capability rights; stale writes/actions remain rejected.
                    opening_session_operation = capability.revision == 0 and (
                        operation == "dialog_lookup"
                        or (operation == "dialog_submission" and raw.get("cancelled") is True)
                    )
                    if capability.revision != record.revision and not opening_session_operation:
                        return _result(409, {"error": "stale_form_step"})
                    return await self._handle_locked(
                        raw, session_id, key, record, capability, fingerprint, deadline, serve
                    )
        except TimeoutError:
            return _result(504, {"error": "deadline"})
        except IngressAuthenticationError:
            return _result(403, {"error": "unauthorized"})
        except (ProtocolError, ValueError, TypeError):
            return _result(400, {"error": "invalid_payload"})
        except StorageDataError:
            return _result(503, {"error": "dialog_storage_unavailable"})
        except ResponseStateError:
            return _result(500, {"error": "invalid_dialog_response"})
        except Exception:
            return _result(503, {"error": "dialog_service_unavailable"})

    async def _handle_locked(
        self,
        raw: dict[str, Any],
        session_id: str,
        key: StorageKey,
        record: _Record,
        capability: _Capability,
        fingerprint: str,
        deadline: float,
        serve: Callable[[DialogEvent, ResponseController], Awaitable[HTTPResult]],
    ) -> HTTPResult:
        form = restore_form(record.form)
        operation = raw.get("type")
        kind: ResponseKind
        model: type[DialogEvent]
        if operation == "dialog_action":
            kind, model = "dialog_action", DialogAction
        elif operation == "dialog_lookup":
            self.profile.require("forms.lookup")
            kind, model = "dialog_lookup", DialogLookup
        elif operation == "refresh":
            self.profile.require("forms.refresh")
            kind, model = "dialog_refresh", DialogRefresh
        elif operation == "dialog_submission" and raw.get("cancelled") is True:
            kind, model = "dialog_cancel", DialogCancel
        elif operation == "dialog_submission":
            kind, model = "dialog_submit", DialogSubmission
        else:
            raise ProtocolError("Unknown native form event type")
        event = model.from_wire(raw)
        event._session_id, event._thread_id, event._form = session_id, record.thread_id, form
        current_fields = input_fields(form)
        merged = deepcopy(record.answers)
        files = record.file_ids.copy()
        if isinstance(event, (DialogSubmission, DialogRefresh, DialogLookup)):
            for name, value in event.submission.items():
                if name in merged and name not in current_fields and _json(value) != _json(merged[name]):
                    raise IngressAuthenticationError("A previous step value was changed")
                merged[name] = deepcopy(value)
            supplied_files = getattr(event, "file_ids", [])
            files = list(dict.fromkeys([*files, *supplied_files]))
            if len(files) > 10:
                raise ProtocolError("Too many native form files")
            if kind in ("dialog_lookup", "dialog_refresh"):
                selected = event.submission.get("selected_field")
                field = current_fields.get(selected) if isinstance(selected, str) else None
                if (
                    not isinstance(field, Select)
                    or (kind == "dialog_lookup" and field.data_source != "dynamic")
                    or (kind == "dialog_refresh" and not field.refresh)
                ):
                    raise ProtocolError("Form request does not target an enabled select field")
                if kind == "dialog_lookup" and not isinstance(event.submission.get("query", ""), str):
                    raise ProtocolError("Lookup query must be text")
            event._values = parse_form_values(
                form,
                merged,
                file_ids=files,
                prior_forms=[restore_form(f) for f in record.prior_forms],
                partial=kind != "dialog_submit",
                source="lookup"
                if kind == "dialog_lookup"
                else "refresh"
                if kind == "dialog_refresh"
                else "submission",
            )
            if event.values.errors:
                if kind == "dialog_lookup":
                    return _result(400, {"error": "invalid_lookup_values"})
                return _result(200, {"errors": dict(event.values.errors)})
        if isinstance(event, DialogAction):
            if record.depth >= 3:
                return _result(409, {"error": "native_modal_stack_limit"})
            if record.active_child is not None:
                child_data = await self.store.get(self._key(record.active_child))
                if (
                    child_data is not None
                    and child_data.get("expires_at", 0) > self._clock()
                    and child_data.get("status") != "closed"
                ):
                    return _result(409, {"error": "dialog_child_active"})
            field = _all_elements(form).get(capability.field or "")
            if not isinstance(field, DialogButton):
                raise IngressAuthenticationError("Unknown declared dialog action")
            event._field_name = field.name
            event._application_context = field.action_button.context.copy()
        pending: _Record | None = None

        async def replace(next_form: Form) -> dict[str, Any]:
            nonlocal pending
            self.profile.require("forms.refresh" if kind == "dialog_refresh" else "forms.steps")
            if record.revision + 1 >= self.max_steps:
                raise IngressCapacityError("Managed form step limit reached")
            next_form = preserve_form_values(
                next_form,
                event.values,
                previous_forms=[*(restore_form(f) for f in record.prior_forms), form],
            )
            pending = record.model_copy(deep=True)
            pending.revision += 1
            if kind == "dialog_submit":
                pending.prior_forms.append(record.form)
                known = set(current_fields) | set(record.answers)
                pending.answers = {name: deepcopy(value) for name, value in merged.items() if name in known}
                pending.file_ids = files.copy()
            wire, _ = self._prepare(pending, session_id, next_form)
            return wire

        async def open_child(child: Form) -> DialogHandle:
            if not isinstance(event, DialogAction):
                raise ResponseStateError("Only a native dialog action has a child trigger")
            return await self.open(event, child, parent=(session_id, record))

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return _result(504, {"error": "deadline"})
        response = ResponseController(
            kind,
            timeout=remaining,
            replace_form=replace,
            open_dialog=open_child if kind == "dialog_action" else None,
            field_names=frozenset(current_fields),
        )
        event._response = response
        claim = kind in ("dialog_submit", "dialog_cancel", "dialog_action")
        if claim:
            record.status = "running"
            record.running_fingerprint = fingerprint
            await self._put(key, record)
        before_result = record.model_copy(deep=True)
        try:
            result = await serve(event, response)
            if result.status != 200:
                if claim:
                    await self._mark_unknown(key, record)
                return result
            if result.payload.get("type") == "form":
                if pending is None or result.payload.get("form", {}).get("state") is None:
                    raise ResponseStateError("Managed form continuations require next/replace_form")
                record = pending
            record.status = "open"
            record.running_fingerprint = None
            if kind in ("dialog_submit", "dialog_cancel") and not (
                result.payload.get("errors")
                or result.payload.get("error")
                or result.payload.get("type") == "form"
            ):
                record.status, record.final_response = "closed", deepcopy(result.payload)
            if kind != "dialog_lookup":
                record.responses[fingerprint] = deepcopy(result.payload)
                while len(record.responses) > 16:
                    del record.responses[next(iter(record.responses))]
            await self._put(key, record)
            return result
        except BaseException:
            if claim:
                before_result.active_child = record.active_child
                await self._mark_unknown(key, before_result)
            raise

    async def reconcile(self, state: SecretStr, *, response: Mapping[str, Any] | None = None) -> None:
        """Application recovery only, after checking external business outcome.

        response=None explicitly permits retry. A native success/error response
        closes the uncertain session. This method is never exposed as HTTP input.
        """
        session_id, digest = self._token_parts(state.get_secret_value())
        await self._reconcile(session_id, response=response, capability_digest=digest)

    async def reconcile_session(self, session_id: str, *, response: Mapping[str, Any] | None = None) -> None:
        """Trusted application/operator recovery by local ID; never an HTTP route."""
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("Expected a local managed session ID")
        await self._reconcile(session_id, response=response)

    async def _reconcile(
        self, session_id: str, *, response: Mapping[str, Any] | None, capability_digest: str | None = None
    ) -> None:
        key = self._key(session_id)
        async with self.store.lock(key):
            record = await self._read(key)
            if (
                capability_digest is not None and capability_digest not in record.capabilities
            ) or record.status not in (
                "running",
                "indeterminate",
                "opening",
            ):
                raise ResponseStateError("Only an uncertain session can be reconciled")
            if response is None:
                record.status = "open"
            else:
                controller = ResponseController(
                    "dialog_submit", field_names=frozenset(input_fields(restore_form(record.form)))
                )
                controller.complete(response)
                if response.get("type") == "form":
                    raise ResponseStateError("Reconciliation cannot invent a managed next step")
                if response.get("errors") or response.get("error"):
                    record.status = "open"
                    if record.running_fingerprint is not None:
                        record.responses[record.running_fingerprint] = dict(response)
                else:
                    record.status = "closed"
                    record.final_response = dict(response)
            record.running_fingerprint = None
            await self._put(key, record)
