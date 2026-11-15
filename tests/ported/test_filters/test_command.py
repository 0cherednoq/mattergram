import re

import pytest

from mattergram import Bot, F
from mattergram.filters import Command, CommandObject
from mattergram.types import Message, User


@pytest.fixture
async def bot():
    client = Bot(token="fixture-only", base_url="https://mattermost.test")
    client._me = User(id="fixture-bot", username="tbot")
    try:
        yield client
    finally:
        await client.close()


class TestCommandFilter:
    def test_commands_not_iterable(self):
        with pytest.raises(ValueError):
            Command(commands=1)

    def test_bad_type(self):
        with pytest.raises(ValueError):
            Command(1)

    def test_without_args(self):
        with pytest.raises(ValueError):
            Command()

    def test_convert_to_list(self):
        cmd = Command(commands="start")
        assert cmd.commands
        assert isinstance(cmd.commands, tuple)
        assert cmd.commands[0] == "start"
        # assert cmd == Command(commands=["start"])

    @pytest.mark.parametrize(
        "commands,checklist",
        [
            [("Test1", "tEst2", "teSt3"), ("test1", "test2", "test3")],
            [("12TeSt", "3t4Est", "5TE6sT"), ("12test", "3t4est", "5te6st")],
            [(re.compile(r"test(\d+)"), "TeSt"), (re.compile(r"test(\d+)"), "test")],
        ],
    )
    def test_init_casefold(self, commands, checklist):
        command = Command(*commands, ignore_case=True)
        assert command.commands == checklist

        command = Command(*commands, ignore_case=False)
        assert command.commands != checklist

    @pytest.mark.parametrize(
        "text,command,result",
        [
            ["/test@tbot", Command(commands=["test"], prefix="/"), True],
            ["/test@tbot", Command("test", prefix="/"), True],
            ["!test", Command(commands=["test"], prefix="/"), False],
            ["/test@mention", Command(commands=["test"], prefix="/"), False],
            ["/tests", Command(commands=["test"], prefix="/"), False],
            ["/", Command(commands=["test"], prefix="/"), False],
            ["/ test", Command(commands=["test"], prefix="/"), False],
            ["", Command(commands=["test"], prefix="/"), False],
            [" ", Command(commands=["test"], prefix="/"), False],
            ["test", Command(commands=["test"], prefix="/"), False],
            [" test", Command(commands=["test"], prefix="/"), False],
            ["a", Command(commands=["test"], prefix="/"), False],
            ["/test@tbot some args", Command(commands=["test"]), True],
            ["/test42@tbot some args", Command(commands=[re.compile(r"test(\d+)")]), True],
            [
                "/test42@tbot some args",
                Command(commands=[re.compile(r"test(\d+)")], magic=F.args == "some args"),
                True,
            ],
            [
                "/test42@tbot some args",
                Command(commands=[re.compile(r"test(\d+)")], magic=F.args == "test"),
                False,
            ],
            ["/TeSt", Command("test", ignore_case=True), True],
            ["/TeSt", Command("TeSt", ignore_case=True), True],
            ["/test", Command("TeSt", ignore_case=True), True],
            ["/TeSt", Command("test", ignore_case=False), False],
            ["/test", Command("TeSt", ignore_case=False), False],
            ["/TeSt", Command("TeSt", ignore_case=False), True],
        ],
    )
    async def test_parse_command(self, bot: Bot, text: str, result: bool, command: Command):
        # TODO: test ignore mention

        message = Message(
            id="fixture-post",
            channel_id="fixture-channel",
            user_id="fixture-user",
            message=text,
            create_at=1700000000000,
        )

        response = await command(message, bot)
        assert bool(response) is result

    @pytest.mark.parametrize(
        "message,result",
        [
            [
                Message(
                    id="fixture-post",
                    channel_id="fixture-channel",
                    user_id="fixture-user",
                    message="",
                    create_at=1700000000000,
                ),
                False,
            ],
            [
                Message(
                    id="fixture-post",
                    channel_id="fixture-channel",
                    user_id="fixture-user",
                    message="/test",
                    create_at=1700000000000,
                ),
                True,
            ],
            [None, False],
        ],
    )
    async def test_call(self, message: Message, result: bool, bot: Bot):
        command = Command(commands=["test"])
        assert bool(await command(message=message, bot=bot)) is result

    async def test_command_magic_result(self, bot: Bot):
        message = Message(
            id="fixture-post",
            channel_id="fixture-channel",
            user_id="fixture-user",
            message="/test 42",
            create_at=1700000000000,
        )
        command = Command(commands=["test"], magic=(F.args.as_("args")))
        result = await command(message=message, bot=bot)
        assert "args" in result
        assert result["args"] == "42"

    async def test_empty_mention_is_none(self, bot: Bot):
        # Fixed https://github.com/aiogram/aiogram/issues/1013:
        #   Empty mention should be None instead of empty string.

        message = Message(
            id="fixture-post",
            channel_id="fixture-channel",
            user_id="fixture-user",
            message="/test",
            create_at=1700000000000,
        )
        command = Command("test")
        result = await command(message=message, bot=bot)

        assert "command" in result
        command_obj: CommandObject = result["command"]
        assert command_obj.mention is None

    def test_str(self):
        cmd = Command(commands=["start"])
        assert str(cmd) == "Command('start', prefix='/', ignore_case=False, ignore_mention=False)"


class TestCommandObject:
    @pytest.mark.parametrize(
        "obj,result",
        [
            [CommandObject(prefix="/", command="command", mention="mention", args="args"), True],
            [CommandObject(prefix="/", command="command", args="args"), False],
        ],
    )
    def test_mentioned(self, obj: CommandObject, result: bool):
        assert isinstance(obj.mentioned, bool)
        assert obj.mentioned is result

    @pytest.mark.parametrize(
        "obj,result",
        [
            [
                CommandObject(prefix="/", command="command", mention="mention", args="args"),
                "/command@mention args",
            ],
            [
                CommandObject(prefix="/", command="command", mention="mention", args=None),
                "/command@mention",
            ],
            [
                CommandObject(prefix="/", command="command", mention=None, args="args"),
                "/command args",
            ],
            [CommandObject(prefix="/", command="command", mention=None, args=None), "/command"],
            [CommandObject(prefix="!", command="command", mention=None, args=None), "!command"],
        ],
    )
    def test_text(self, obj: CommandObject, result: str):
        assert obj.text == result

    def test_update_handler_flags(self):
        cmd = Command(commands=["start"])
        flags = {}
        cmd.update_handler_flags(flags)

        assert "commands" in flags
        assert isinstance(flags["commands"], list)
        assert len(flags["commands"]) == 1
        assert flags["commands"][0] is cmd

        cmd.update_handler_flags(flags)
        assert len(flags["commands"]) == 2
