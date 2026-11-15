from __future__ import annotations

import math
import unittest
from datetime import timedelta

from redis.asyncio import Redis

from mattergram.fsm.storage.redis import RedisEventIsolation, RedisStorage


class RedisConfigurationTests(unittest.TestCase):
    def test_rejects_invalid_ttl_and_unbounded_or_inconsistent_locks(self):
        client = Redis()
        for value in (0, -1, True, 0.5, 1.5, math.inf, math.nan, timedelta(milliseconds=100)):
            with self.subTest(ttl=value), self.assertRaises(ValueError):
                RedisStorage(client, state_ttl=value)
        for settings in (
            {"timeout": None},
            {"timeout": math.inf},
            {"timeout": True},
            {"blocking_timeout": None},
            {"blocking_timeout": 0},
            {"sleep": -1},
            {"blocking": False},
            {"raise_on_release_error": False},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                RedisEventIsolation(client, lock_kwargs=settings)
        for hold in (0, -1, True, math.inf, 1, 2):
            with self.subTest(hold=hold), self.assertRaises(ValueError):
                RedisEventIsolation(client, lock_kwargs={"timeout": 1}, hold_timeout=hold)
        with self.assertRaises(ValueError):
            RedisStorage(client, owns_client="false")
        with self.assertRaises(ValueError):
            RedisEventIsolation(client, owns_client="false")


if __name__ == "__main__":
    unittest.main()
