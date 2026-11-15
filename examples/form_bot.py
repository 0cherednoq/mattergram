"""Open and validate an already provisioned /request command's native form."""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from mattergram import Bot, Dispatcher
from mattergram.filters import Command
from mattergram.http import DialogManager, HttpAdapter, SlashBinding
from mattergram.http.dialog_store import DialogStore
from mattergram.types import DialogSubmission, SlashCommand
from mattergram.ui import UIProfile
from mattergram.ui.forms import Checkbox, Form, TextInput


async def main() -> None:
    bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"])
    dispatcher = Dispatcher()
    store: DialogStore | None = None
    try:
        if url := os.environ.get("MATTERGRAM_DIALOG_REDIS_URL"):
            from mattergram.http.redis_dialogs import RedisDialogStore

            store = RedisDialogStore.from_url(url)
        profile = UIProfile(
            server_version=os.environ.get("MM_SERVER_VERSION", "11.1.0"),
            client="application-declared-basic-form",
            client_features=frozenset({"forms.basic"}),
            client_evidence="Example configuration; verify against the deployed client",
        )
        dialogs = DialogManager(
            bot,
            profile=profile,
            callback_url=os.environ["MM_CALLBACK_BASE_URL"].rstrip("/") + "/mattergram/dialog",
            store=store,
        )

        @dispatcher.slash_command(Command("request"))
        async def request(event: SlashCommand) -> None:
            await event.open_dialog(
                Form(
                    callback_id="request",
                    title="Request",
                    notify_on_cancel=True,
                    elements=(
                        TextInput(name="title", label="Title", min_length=1),
                        Checkbox(name="urgent", label="Urgent", default=False),
                    ),
                )
            )

        @dispatcher.dialog_submit()
        async def submitted(event: DialogSubmission) -> None:
            if event.values["title"] == "invalid":
                await event.field_errors({"title": "Choose another title"})
                return
            # This example validates and closes. Persist real business work or
            # enqueue it durably before answer() when the application needs it.
            await event.answer()

        http = HttpAdapter(
            bot,
            dispatcher,
            trusted_peers=os.environ["MM_HTTP_TRUSTED_PEERS"].split(","),
            slash_bindings=[
                SlashBinding(os.environ["MM_TEAM_ID"], "request", SecretStr(os.environ["MM_SLASH_TOKEN"]))
            ],
            dialogs=dialogs,
            own_dialogs=True,
        )
        await dispatcher.start(bot, http=http, host="127.0.0.1", port=8080)
    finally:
        await dispatcher.close()
        await bot.close()
        if store is not None:
            await store.close()


if __name__ == "__main__":
    asyncio.run(main())
