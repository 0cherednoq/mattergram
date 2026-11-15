"""Native ingress/lifecycle; routing/filter/middleware algorithms come from aiogram."""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Mapping
from contextlib import aclosing
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from mattergram.bot import Bot
from mattergram.exceptions import ClientClosedError, ProtocolError, ShutdownTimeoutError, TransportError
from mattergram.fsm.context import FSMContext
from mattergram.fsm.storage.base import BaseEventIsolation, BaseStorage, StorageKey
from mattergram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from mattergram.fsm.strategy import FSMStrategy, apply_strategy
from mattergram.normalizers import normalize_posted
from mattergram.types import (
    CallbackQuery,
    DialogAction,
    DialogCancel,
    DialogEvent,
    DialogLookup,
    DialogRefresh,
    ErrorEvent,
    Event,
    EventContext,
    Message,
    RawEvent,
    SlashCommand,
)

from .context import validate_application_data
from .event.bases import UNHANDLED, CancelHandler
from .router import Router

if TYPE_CHECKING:
    from mattergram.http.adapter import HttpAdapter
    from mattergram.http.server import HTTPServer

_STOP = object()


class Dispatcher(Router):
    """One sequential bounded ingress worker; per-session state locks also protect feed_event.

    WS is not a durable queue. A disconnect can lose events. Shutdown discards
    queued-but-unstarted events, drains the current handler up to shutdown_timeout,
    then cancels it. Dropped events and reconnects have explicit counters.
    User-supplied storage/isolation remain user-owned; defaults are owned here.
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        storage: BaseStorage | None = None,
        events_isolation: BaseEventIsolation | None = None,
        fsm_strategy: FSMStrategy = FSMStrategy.USER_IN_CHANNEL,
        queue_capacity: int = 64,
        shutdown_timeout: float = 10.0,
        reconnect_delay: float = 0.25,
        **workflow_data: Any,
    ) -> None:
        super().__init__(name=name)
        validate_application_data(workflow_data)
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise ValueError("queue_capacity must be a positive integer")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in (shutdown_timeout, reconnect_delay)
        ):
            raise ValueError("Lifecycle timeouts must be positive")
        if not isinstance(fsm_strategy, FSMStrategy):
            raise ValueError("fsm_strategy must be a native FSMStrategy")
        self.storage = storage if storage is not None else MemoryStorage()
        self.events_isolation = events_isolation if events_isolation is not None else SimpleEventIsolation()
        self.fsm_strategy = fsm_strategy
        self.workflow_data = dict(workflow_data)
        self.queue_capacity = queue_capacity
        self.shutdown_timeout = shutdown_timeout
        self.reconnect_delay = reconnect_delay
        self.dropped_events = 0
        self.reconnects = 0
        self.ignored_self_events = 0
        self._own_storage = storage is None
        self._own_isolation = events_isolation is None
        self._closed = False
        self._running = False
        self._stop_event: asyncio.Event | None = None
        self._active_feeds = 0
        self._queue: asyncio.Queue[Any] | None = None

    @property
    def pending_events(self) -> int:
        """Decoded events currently waiting for the sequential WS worker."""
        return 0 if self._queue is None else self._queue.qsize()

    async def _identity(self, bot: Bot) -> str:
        if bot.is_closed:
            raise ClientClosedError("Bot is closed")
        if bot.user_id is None:
            await bot.get_me()
        if bot.user_id is None:
            raise ProtocolError("Bot identity is unavailable")
        return bot.user_id

    async def feed_event(self, bot: Bot, event: Event, **context: Any) -> Any:
        self._active_feeds += 1
        try:
            return await self._feed_event(bot, event, **context)
        finally:
            self._active_feeds -= 1

    async def _feed_event(self, bot: Bot, event: Event, **context: Any) -> Any:
        if self._closed:
            raise RuntimeError("Dispatcher is closed")
        validate_application_data(context)
        validate_application_data(self.workflow_data)
        if not isinstance(event, Event):
            raise TypeError("feed_event requires a native Event")
        bot_id = await self._identity(bot)
        if event.context is None:
            event._context = EventContext(
                bot.instance_id,
                bot_id,
                (
                    "websocket"
                    if isinstance(event, Message) and event.event_raw is not None
                    else "http-slash"
                    if isinstance(event, SlashCommand)
                    else "http-callback"
                    if isinstance(event, CallbackQuery)
                    else "http-dialog"
                    if isinstance(event, DialogEvent)
                    else "feed"
                ),
                datetime.now(UTC),
                str(uuid4()),
            )
        elif event.context.instance_id != bot.instance_id or event.context.bot_id != bot_id:
            raise ValueError("Event belongs to another instance or bot")
        data = {
            **self.workflow_data,
            **context,
            "bot": bot,
            "dispatcher": self,
            "event_update": event,
            "event_context": event.context,
        }
        if isinstance(event, (Message, SlashCommand, CallbackQuery, DialogEvent)):
            thread_id: str | None
            if isinstance(event, Message) and event._bot is not None and event._bot is not bot:
                raise ValueError("Message is bound to another Bot")
            if isinstance(event, Message):
                event._bot = bot
                thread_id = event.root_id or event.post_id
                event_kind = "message"
            elif isinstance(event, SlashCommand):
                thread_id = event.root_id
                event_kind = "slash_command"
            elif isinstance(event, CallbackQuery):
                thread_id = event.thread_id
                event_kind = "callback_query"
            else:
                thread_id = event.thread_id
                event_kind = (
                    "dialog_action"
                    if isinstance(event, DialogAction)
                    else "dialog_cancel"
                    if isinstance(event, DialogCancel)
                    else "dialog_lookup"
                    if isinstance(event, DialogLookup)
                    else "dialog_refresh"
                    if isinstance(event, DialogRefresh)
                    else "dialog_submit"
                )
            if thread_id is None and self.fsm_strategy in (
                FSMStrategy.USER_IN_THREAD,
                FSMStrategy.CHANNEL_THREAD,
            ):
                raise ValueError("Thread-scoped HTTP FSM requires a confirmed native root/thread ID")
            channel, user, thread = apply_strategy(
                self.fsm_strategy, event.channel_id, event.user_id, thread_id
            )
            key = StorageKey(
                instance_id=bot.instance_id,
                bot_id=bot_id,
                channel_id=channel,
                user_id=user,
                thread_id=thread,
                team_id=None,
                dialog_session_id=event.session_id if isinstance(event, DialogEvent) else None,
            )
            async with self.events_isolation.lock(key):
                data.update(
                    state=FSMContext(self.storage, key),
                    raw_state=await self.storage.get_state(key),
                    event_raw=event.event_raw if isinstance(event, Message) else event.raw,
                )
                return await self._dispatch(event_kind, event, data)
        if isinstance(event, RawEvent):
            data["event_raw"] = event.raw
            return await self._dispatch("raw_event", event, data)
        raise TypeError("This ingress requires a supported native event and its typed normalizer")

    async def _dispatch(self, kind: str, event: Event, data: dict[str, Any]) -> Any:
        try:
            return await self.propagate_event(kind, event, **data)
        except CancelHandler:
            return UNHANDLED
        except Exception as error:
            failure = ErrorEvent(event=event, exception=error)
            failure._context = event.context
            response = await self.propagate_event(
                "error", failure, **{**data, "event_update": failure, "exception": error}
            )
            if response is UNHANDLED:
                raise
            return response

    async def feed_update(self, bot: Bot, update: Mapping[str, Any], **context: Any) -> Any:
        if self._closed:
            raise RuntimeError("Dispatcher is closed")
        validate_application_data(context)
        identity = await self._identity(bot)
        if not isinstance(update, Mapping) or not isinstance(update.get("event"), str) or not update["event"]:
            raise ProtocolError("WebSocket event name is missing or invalid")
        if update["event"] == "posted":
            event = normalize_posted(update, bot=bot, self_user_id=identity)
            if event is None:
                self.ignored_self_events += 1
                return UNHANDLED
            return await self.feed_event(bot, event, **context)
        raw = RawEvent(event_type=update["event"], payload=deepcopy(dict(update)))
        raw._raw = deepcopy(dict(update))
        raw._context = EventContext(bot.instance_id, identity, "websocket", datetime.now(UTC), str(uuid4()))
        return await self.feed_event(bot, raw, **context)

    async def stop(self) -> None:
        if self._stop_event is None:
            raise RuntimeError("Dispatcher has not been started")
        self._stop_event.set()

    async def close(self) -> None:
        if self._running:
            raise RuntimeError("Stop and await start_websocket before closing the dispatcher")
        if self._closed:
            return
        if self._active_feeds:
            raise RuntimeError("Await active feed_event calls before closing the dispatcher")
        self._closed = True
        try:
            if self._own_isolation:
                await self.events_isolation.close()
        finally:
            if self._own_storage:
                await self.storage.close()

    async def _reader(self, bot: Bot, queue: asyncio.Queue[Any]) -> None:
        failures = 0
        while True:
            try:
                async with aclosing(bot._events()) as updates:
                    async for update in updates:
                        failures = 0
                        try:
                            await queue.put(update)
                        except asyncio.CancelledError:
                            self.dropped_events += 1
                            raise
            except TransportError:
                pass
            failures += 1
            self.reconnects += 1
            delay = min(30.0, self.reconnect_delay * 2 ** min(failures - 1, 8))
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))

    async def _worker(self, bot: Bot, queue: asyncio.Queue[Any]) -> None:
        while True:
            update = await queue.get()
            try:
                if update is _STOP:
                    return
                await self.feed_update(bot, update)
            finally:
                queue.task_done()

    async def start_websocket(self, bot: Bot) -> None:
        await self._start_websocket(bot)

    async def start(self, bot: Bot, *, http: HttpAdapter, host: str = "127.0.0.1", port: int = 8080) -> None:
        """Own WS/HTTP and bot lifecycle; start HTTP after router startup, drain before shutdown."""
        from mattergram.http.server import HTTPServer

        if http.bot is not bot or http.dispatcher is not self:
            raise ValueError("HTTP ingress must share this dispatcher and exact Bot")
        server = HTTPServer(http, host=host, port=port)
        await self._start_websocket(bot, server=server)

    async def _start_websocket(self, bot: Bot, *, server: HTTPServer | None = None) -> None:
        if self._running or self._closed:
            raise RuntimeError("Dispatcher is running or closed")
        self._running = True
        self._stop_event = asyncio.Event()
        queue: asyncio.Queue[Any] = asyncio.Queue(self.queue_capacity)
        self._queue = queue
        started: list[Router] = []
        tasks: list[asyncio.Task[Any]] = []
        primary: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        try:
            async with bot:
                try:
                    for router in self.chain_tail:
                        started.append(router)
                        await router.startup.trigger(
                            **self.workflow_data, bot=bot, dispatcher=self, router=router
                        )
                    if server is not None:
                        await server.start()
                    reader = asyncio.create_task(self._reader(bot, queue))
                    worker = asyncio.create_task(self._worker(bot, queue))
                    stopper: asyncio.Task[Any] = asyncio.create_task(self._stop_event.wait())
                    tasks = [reader, worker, stopper]
                    completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in (reader, worker):
                        if task in completed:
                            task.result()
                except BaseException as error:
                    primary = error
                finally:
                    if server is not None:
                        try:
                            await server.stop_accepting()
                        except BaseException as error:
                            cleanup_errors.append(error)
                    if tasks:
                        reader, worker, stopper = tasks
                        reader.cancel()
                        stopper.cancel()
                        await asyncio.gather(reader, stopper, return_exceptions=True)
                        while not queue.empty():
                            queue.get_nowait()
                            queue.task_done()
                            self.dropped_events += 1
                        if not worker.done():
                            queue.put_nowait(_STOP)
                            try:
                                await asyncio.wait_for(asyncio.shield(worker), self.shutdown_timeout)
                            except TimeoutError:
                                worker.cancel()
                                await asyncio.gather(worker, return_exceptions=True)
                                cleanup_errors.append(
                                    ShutdownTimeoutError("Handler exceeded shutdown_timeout")
                                )
                            except BaseException as error:
                                cleanup_errors.append(error)
                        elif not worker.cancelled():
                            worker_error = worker.exception()
                            if worker_error is not None and primary is None:
                                primary = worker_error
                    if server is not None:
                        try:
                            await server.close()
                        except BaseException as error:
                            cleanup_errors.append(error)
                    for router in reversed(started):
                        for handler in router.shutdown.handlers:
                            try:
                                await asyncio.wait_for(
                                    handler.call(
                                        **self.workflow_data, bot=bot, dispatcher=self, router=router
                                    ),
                                    self.shutdown_timeout,
                                )
                            except BaseException as error:
                                cleanup_errors.append(error)
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                cleanup_errors.append(error)
        finally:
            self._running = False
            self._queue = None
            try:
                await self.close()
            except BaseException as error:
                cleanup_errors.append(error)
        if primary is not None:
            for cleanup_failure in cleanup_errors:
                primary.add_note(f"Additional cleanup failure: {type(cleanup_failure).__name__}")
            raise primary
        if cleanup_errors:
            raise BaseExceptionGroup("Dispatcher cleanup failed", cleanup_errors)
