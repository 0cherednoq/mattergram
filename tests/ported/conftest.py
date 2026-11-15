"""Native fixtures; never import the donor's Telegram conftest or MockedBot."""

import os
from uuid import uuid4

import pytest

from mattergram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from mattergram.fsm.storage.memory import DisabledEventIsolation, MemoryStorage, SimpleEventIsolation
from mattergram.fsm.storage.redis import RedisEventIsolation, RedisStorage


@pytest.fixture
def storage_key():
    return StorageKey("https://instance.test", "bot", "channel", "user")


@pytest.fixture
async def storage(request):
    if request.param == "memory_storage":
        result = MemoryStorage()
        try:
            yield result
        finally:
            await result.close()
        return
    assert request.param == "redis_storage"
    url = os.environ.get("MATTERGRAM_REDIS_URL")
    if not url:
        pytest.skip("Real Redis profile not requested")
    prefix = "mattergram-upstream-" + uuid4().hex
    remote = RedisStorage.from_url(url, key_builder=DefaultKeyBuilder(prefix=prefix))
    try:
        yield remote
    finally:
        try:
            keys = [key async for key in remote.redis.scan_iter(match=prefix + ":*")]
            if keys:
                await remote.redis.delete(*keys)
        finally:
            await remote.close()


@pytest.fixture
async def isolation(request):
    if request.param == "redis_isolation":
        url = os.environ.get("MATTERGRAM_REDIS_URL")
        if not url:
            pytest.skip("Real Redis profile not requested")
        remote = RedisEventIsolation.from_url(
            url, key_builder=DefaultKeyBuilder(prefix="mattergram-lock-" + uuid4().hex)
        )
        try:
            yield remote
        finally:
            await remote.close()
        return
    result = SimpleEventIsolation() if request.param == "lock_isolation" else DisabledEventIsolation()
    try:
        yield result
    finally:
        await result.close()
