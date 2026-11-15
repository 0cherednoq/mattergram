"""Send a native signed menu when a user writes 'menu'; no setup/admin token."""

from __future__ import annotations

import asyncio
import os

from mattergram import Bot, Dispatcher, F
from mattergram.filters import CallbackData
from mattergram.http import CallbackSigner, HttpAdapter
from mattergram.types import CallbackQuery, Message
from mattergram.ui.actions import ActionContext, Actions, Button, External


class MenuAction(CallbackData, prefix="menu"):
    verb: str


async def main() -> None:
    bot = Bot(token=os.environ["MM_BOT_TOKEN"], base_url=os.environ["MM_URL"])
    dispatcher = Dispatcher()
    try:
        identity = await bot.get_me()
        signer = CallbackSigner(
            instance_id=bot.instance_id,
            bot_id=identity.id,
            keys={"current": bytes.fromhex(os.environ["MM_ACTION_SIGNING_KEY_HEX"])},
            active_key_id="current",
        )

        @dispatcher.message(F.text == "menu")
        async def menu(message: Message) -> None:
            await bot.send_message(
                message.channel_id,
                "Choose an action",
                actions=Actions(
                    items=(
                        Button(
                            name="Confirm",
                            style="primary",
                            action=External(
                                url=os.environ["MM_CALLBACK_BASE_URL"].rstrip("/") + "/mattergram/callback",
                                data=MenuAction(verb="confirm").pack(),
                            ),
                        ),
                    )
                ),
                action_context=ActionContext(signer, message.channel_id, user_id=message.user_id),
            )

        @dispatcher.callback_query(MenuAction.filter())
        async def clicked(query: CallbackQuery, callback_data: MenuAction) -> None:
            await query.answer("Selected: " + callback_data.verb)

        http = HttpAdapter(
            bot,
            dispatcher,
            trusted_peers=os.environ["MM_HTTP_TRUSTED_PEERS"].split(","),
            callback_signer=signer,
        )
        await dispatcher.start(bot, http=http, host="127.0.0.1", port=8080)
    finally:
        await dispatcher.close()
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
