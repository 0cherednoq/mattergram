from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any

import pytest

from mattergram_dialog import Button, Const


class Manager:
    def __init__(self) -> None:
        self.context = SimpleNamespace(
            intent_id="intent",
            state=SimpleNamespace(state="Dialog:state"),
            widget_data={},
        )

    def current_context(self) -> Any:
        return self.context


async def action_token(button: Button, manager: Manager) -> str:
    controls = await button.render_controls({}, manager)
    value = controls[0].value
    assert isinstance(value, str)
    return value


@pytest.mark.asyncio
async def test_async_and_sync_manager_only_callbacks() -> None:
    manager = Manager()
    calls: list[tuple[str, object]] = []

    async def async_click(value: Any) -> None:
        calls.append(("async", value))

    def sync_click(value: Any) -> None:
        calls.append(("sync", value))

    for identifier, callback in (("asyncClick", async_click), ("syncClick", sync_click)):
        button = Button(Const("Run"), id=identifier, on_click=callback)
        token = await action_token(button, manager)
        assert await button.process_event(token, object(), manager)

    assert calls == [("async", manager), ("sync", manager)]


@pytest.mark.asyncio
async def test_full_callback_keeps_event_button_and_manager() -> None:
    manager = Manager()
    event = object()
    received: list[tuple[object, Button, object]] = []

    async def full_click(value: object, button: Button, dialog_manager: Any) -> None:
        received.append((value, button, dialog_manager))

    button = Button(Const("Run"), id="fullClick", on_click=full_click)
    token = await action_token(button, manager)
    assert await button.process_event(token, event, manager)
    assert received == [(event, button, manager)]


@pytest.mark.asyncio
async def test_bound_method_partial_and_callable_object_use_final_signature() -> None:
    manager = Manager()
    calls: list[str] = []

    class Bound:
        async def click(self, value: Any) -> None:
            assert value is manager
            calls.append("bound")

    class CallableObject:
        def __call__(self, value: Any) -> None:
            assert value is manager
            calls.append("callable")

    async def configured(prefix: str, event: Any, button: Button, value: Any) -> None:
        assert event is marker
        assert value is manager
        calls.append(prefix + button.widget_id)

    marker = object()
    callbacks = (
        ("boundClick", Bound().click, object()),
        ("callableClick", CallableObject(), object()),
        ("partialClick", partial(configured, "partial:"), marker),
    )
    for identifier, callback, event in callbacks:
        button = Button(Const("Run"), id=identifier, on_click=callback)
        token = await action_token(button, manager)
        assert await button.process_event(token, event, manager)

    assert calls == ["bound", "callable", "partial:partialClick"]


def zero_arguments() -> None:
    pass


def two_arguments(first: Any, second: Any) -> None:
    del first, second


def four_arguments(first: Any, second: Any, third: Any, fourth: Any) -> None:
    del first, second, third, fourth


@pytest.mark.parametrize("callback", [zero_arguments, two_arguments, four_arguments])
def test_unsupported_positional_counts_fail_at_button_creation(callback: Any) -> None:
    with pytest.raises(TypeError, match=callback.__name__) as error:
        Button(Const("Run"), id="invalidCount", on_click=callback)
    assert "(manager) or (event, button, manager)" in str(error.value)


def test_variadic_and_keyword_only_signatures_fail_at_button_creation() -> None:
    def variadic(*values: Any) -> None:
        del values

    def keyword_only(*, manager: Any) -> None:
        del manager

    for callback in (variadic, keyword_only):
        with pytest.raises(TypeError, match="must accept exactly"):
            Button(Const("Run"), id="invalidShape", on_click=callback)


@pytest.mark.asyncio
async def test_callback_type_error_is_not_retried_and_token_stays_one_shot() -> None:
    manager = Manager()
    calls = 0

    def broken(value: Any) -> None:
        nonlocal calls
        assert value is manager
        calls += 1
        raise TypeError("business failure")

    button = Button(Const("Run"), id="brokenClick", on_click=broken)
    token = await action_token(button, manager)
    with pytest.raises(TypeError, match="business failure"):
        await button.process_event(token, object(), manager)
    assert not await button.process_event(token, object(), manager)
    assert calls == 1
