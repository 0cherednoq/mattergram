import pytest

from mattergram.fsm.context import FSMContext
from mattergram.fsm.storage.base import StorageKey
from mattergram.fsm.storage.memory import MemoryStorage


@pytest.fixture()
def state():
    storage = MemoryStorage()
    key = StorageKey(
        user_id="user", channel_id="channel-negative", instance_id="https://instance.test", bot_id="bot"
    )
    ctx = storage.storage[key]
    ctx.state = "test"
    ctx.data = {"foo": "bar"}
    return FSMContext(storage=storage, key=key)


class TestFSMContext:
    async def test_address_mapping(self):
        storage = MemoryStorage()
        ctx = storage.storage[
            StorageKey(
                channel_id="channel-negative",
                user_id="user",
                instance_id="https://instance.test",
                bot_id="bot",
            )
        ]
        ctx.state = "test"
        ctx.data = {"foo": "bar"}
        state = FSMContext(
            storage=storage,
            key=StorageKey(
                channel_id="channel-negative",
                user_id="user",
                instance_id="https://instance.test",
                bot_id="bot",
            ),
        )
        state2 = FSMContext(
            storage=storage,
            key=StorageKey(
                channel_id="channel-positive",
                user_id="user",
                instance_id="https://instance.test",
                bot_id="bot",
            ),
        )
        state3 = FSMContext(
            storage=storage,
            key=StorageKey(
                channel_id="channel-other", user_id="other", instance_id="https://instance.test", bot_id="bot"
            ),
        )

        assert await state.get_state() == "test"
        assert await state2.get_state() is None
        assert await state3.get_state() is None

        assert await state.get_data() == {"foo": "bar"}
        assert await state2.get_data() == {}
        assert await state3.get_data() == {}

        assert await state.get_value("foo") == "bar"
        assert await state2.get_value("foo") is None
        assert await state3.get_value("foo", "baz") == "baz"

        await state2.set_state("experiments")
        assert await state.get_state() == "test"
        assert await state3.get_state() is None

        await state3.set_data({"key": "value"})
        assert await state2.get_data() == {}

        await state.update_data({"key": "value"})
        assert await state.get_data() == {"foo": "bar", "key": "value"}

        await state.clear()
        assert await state.get_state() is None
        assert await state.get_data() == {}

        assert await state2.get_state() == "experiments"
