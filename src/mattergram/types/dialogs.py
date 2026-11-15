"""Native form events; local session identity is distinct from platform identifiers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, PrivateAttr, SecretStr, field_validator

from mattergram.exceptions import ResponseStateError

from .base import Identifier, User
from .http import HTTPEvent

if TYPE_CHECKING:
    from mattergram.http.dialogs import DialogHandle
    from mattergram.ui.form_values import FormValues
    from mattergram.ui.forms import Form, Option


class DialogEvent(HTTPEvent):
    user_id: Identifier
    channel_id: Identifier
    team_id: Identifier | None = None
    _session_id: str | None = PrivateAttr(default=None)
    _thread_id: str | None = PrivateAttr(default=None)
    _values: FormValues | None = PrivateAttr(default=None)
    _form: Form | None = PrivateAttr(default=None)

    @field_validator("team_id", mode="before")
    @classmethod
    def empty_team(cls, value: Any) -> Any:
        return None if value == "" else value

    @property
    def from_user(self) -> User:
        return User(id=self.user_id)

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            raise ResponseStateError("Dialog event has no bound application session")
        return self._session_id

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def values(self) -> FormValues:
        if self._values is None:
            raise ResponseStateError("This event has no parsed submission values")
        return self._values

    @property
    def form(self) -> Form:
        if self._form is None:
            raise ResponseStateError("Dialog event has no bound form schema")
        return self._form

    @property
    def application_state(self) -> str:
        return self.form.state


class DialogSubmission(DialogEvent):
    type: Literal["dialog_submission"] = "dialog_submission"
    callback_id: Identifier
    state: SecretStr = Field(repr=False, exclude=True)
    submission: dict[str, Any] = Field(repr=False, exclude=True)
    cancelled: Literal[False] = False
    file_ids: list[Identifier] = Field(default_factory=list, repr=False, exclude=True)

    async def answer(self) -> None:
        self._controller().ack()

    async def field_errors(self, errors: Mapping[str, str]) -> None:
        self._controller().field_errors(errors)

    async def validation_error(self, message: str) -> None:
        self._controller().validation_error(message)

    async def next(self, form: Form) -> None:
        await self._controller().replace_form(form)


class DialogRefresh(DialogEvent):
    type: Literal["refresh"] = "refresh"
    callback_id: Identifier
    state: SecretStr = Field(repr=False, exclude=True)
    submission: dict[str, Any] = Field(repr=False, exclude=True)
    cancelled: Literal[False] = False
    file_ids: list[Identifier] = Field(default_factory=list, repr=False, exclude=True)

    async def replace_form(self, form: Form) -> None:
        await self._controller().replace_form(form)

    async def field_errors(self, errors: Mapping[str, str]) -> None:
        self._controller().field_errors(errors)

    async def validation_error(self, message: str) -> None:
        self._controller().validation_error(message)


class DialogLookup(DialogEvent):
    type: Literal["dialog_lookup"] = "dialog_lookup"
    callback_id: Identifier
    state: SecretStr = Field(repr=False, exclude=True)
    submission: dict[str, Any] = Field(repr=False, exclude=True)
    cancelled: Literal[False] = False

    @property
    def query(self) -> str:
        value = self.submission.get("query", "")
        if not isinstance(value, str):
            raise ResponseStateError("Lookup query is not text")
        return value

    @property
    def selected_field(self) -> str:
        value = self.submission.get("selected_field")
        if not isinstance(value, str):
            raise ResponseStateError("Lookup request has no selected field")
        return value

    async def answer(self, options: Sequence[Option]) -> None:
        self._controller().lookup_answer(options)


class DialogCancel(DialogEvent):
    type: Literal["dialog_submission"] = "dialog_submission"
    callback_id: Identifier
    state: SecretStr = Field(repr=False, exclude=True)
    submission: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)
    cancelled: Literal[True] = True

    async def answer(self) -> None:
        self._controller().ack()


class DialogAction(DialogEvent):
    type: Literal["dialog_action"] = "dialog_action"
    trigger_id: SecretStr = Field(repr=False, exclude=True)
    action_context: dict[str, str] = Field(alias="context", repr=False, exclude=True)
    _field_name: str | None = PrivateAttr(default=None)
    _application_context: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def field_name(self) -> str:
        if self._field_name is None:
            raise ResponseStateError("Dialog action is not bound to a declared button")
        return self._field_name

    @property
    def application_context(self) -> Mapping[str, str]:
        return self._application_context.copy()

    async def answer(self) -> None:
        self._controller().ack()

    async def open_dialog(self, form: Form) -> DialogHandle:
        return await self._controller().open_dialog(form)
