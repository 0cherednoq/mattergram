"""Run offline to inspect registration intent; this does not provision a server."""

from mattergram import Dispatcher, Router
from mattergram.filters import Command
from mattergram.provisioning import collect_commands
from mattergram.types import SlashCommand

router = Router(name="requests")


@router.slash_command(Command("request", "ticket"), description="Create a request", hint="[topic]")
async def request(event: SlashCommand) -> None:
    await event.answer("Request received")


def main() -> None:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    manifest = collect_commands(dispatcher)
    assert [c.trigger for c in manifest.commands] == ["request", "ticket"]
    for spec in manifest.commands:
        print(spec.trigger, spec.description, spec.hint)
    print("Manifest fingerprint:", manifest.fingerprint)


if __name__ == "__main__":
    main()
