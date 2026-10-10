"""Inline bot client prewarm: adoption by register_manager,
token-mismatch handling and fallback semantics."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

# inline.core participates in a pre-existing import cycle that is only
# resolved when heroku.main is imported first (as the entry point does)
import heroku.main  # noqa: F401
from heroku.inline.core import InlineManager


class FakeClient:
    tg_id = 6816400085
    api_id = 1
    api_hash = "hash"

    def __init__(self):
        self.heroku_me = Mock()

    async def __call__(self, request):
        return Mock(filters=[])

    async def force_get_entity(self, username):
        return username


class FakeBotClient:
    def __init__(self):
        self.started = False
        self.disconnected = False
        self.bot_token = None
        self.me = Mock(id=42, username="mybot")

    async def start(self, bot_token=None):
        self.started = True
        self.bot_token = bot_token
        return self

    async def get_me(self):
        return self.me

    async def disconnect(self):
        self.disconnected = True


class FakeDB:
    def get(self, owner, key, default=None):
        return default

    def set(self, owner, key, value):
        pass


def make_manager(token="12345:abc"):
    manager = object.__new__(InlineManager)
    manager._token = token
    manager._bot_prewarm = None
    manager._bot_client = None
    manager._me = None
    manager._client = FakeClient()
    return manager


def patched_client_stack(record=None):
    """Patch TelegramClient/SQLiteSession/SESSIONS_DIR in core."""

    def factory(*args, **kwargs):
        client = FakeBotClient()
        if record is not None:
            record.append(client)
        return client

    return (
        patch("heroku.inline.core.TelegramClient", side_effect=factory),
        patch("heroku.inline.core.SQLiteSession", return_value=object()),
        patch("heroku.main.SESSIONS_DIR", "/tmp/opencode-prewarm-test"),
    )


class PrewarmUnitTest(unittest.IsolatedAsyncioTestCase):
    async def test_prewarm_skipped_without_token(self):
        manager = make_manager(token=False)
        manager.prewarm_bot_client()
        self.assertIsNone(manager._bot_prewarm)

    async def test_prewarm_skipped_when_client_exists(self):
        manager = make_manager()
        manager._bot_client = object()
        manager.prewarm_bot_client()
        self.assertIsNone(manager._bot_prewarm)

    async def test_prewarm_not_started_twice(self):
        manager = make_manager()
        manager._cleanup_stale_bot_sessions = Mock()
        record = []
        stack = patched_client_stack(record)
        with stack[0], stack[1], stack[2]:
            manager.prewarm_bot_client()
            first = manager._bot_prewarm
            manager.prewarm_bot_client()
            self.assertIs(manager._bot_prewarm, first)
            await first

    async def test_prewarm_connects_client(self):
        manager = make_manager()
        manager._cleanup_stale_bot_sessions = Mock()
        record = []
        stack = patched_client_stack(record)
        with stack[0], stack[1], stack[2]:
            manager.prewarm_bot_client()
            result = await manager._bot_prewarm

        client, token = result
        self.assertEqual(token, "12345:abc")
        self.assertTrue(client.started)
        self.assertEqual(client.bot_token, "12345:abc")
        self.assertEqual(record, [client])
        self.assertEqual(manager._me, FakeClient.tg_id)

    async def test_take_adopts_started_client(self):
        manager = make_manager()
        client = FakeBotClient()
        manager._bot_prewarm = asyncio.ensure_future(
            _resolve((client, "12345:abc"))
        )
        taken = await manager._take_prewarmed_client()
        self.assertIs(taken, client)
        self.assertIsNone(manager._bot_prewarm)
        self.assertFalse(client.disconnected)

    async def test_take_returns_none_without_prewarm(self):
        manager = make_manager()
        self.assertIsNone(await manager._take_prewarmed_client())

    async def test_take_drops_token_mismatch(self):
        manager = make_manager()
        client = FakeBotClient()
        manager._bot_prewarm = asyncio.ensure_future(
            _resolve((client, "12345:abc"))
        )
        manager._token = "99999:other"
        taken = await manager._take_prewarmed_client()
        self.assertIsNone(taken)
        self.assertTrue(client.disconnected)

    async def test_take_drops_when_client_already_set(self):
        manager = make_manager()
        client = FakeBotClient()
        manager._bot_prewarm = asyncio.ensure_future(
            _resolve((client, "12345:abc"))
        )
        manager._bot_client = object()
        taken = await manager._take_prewarmed_client()
        self.assertIsNone(taken)
        self.assertTrue(client.disconnected)

    async def test_take_swallows_failures(self):
        manager = make_manager()

        async def boom():
            raise RuntimeError("network hiccup")

        manager._bot_prewarm = asyncio.ensure_future(boom())
        self.assertIsNone(await manager._take_prewarmed_client())

    async def test_take_handles_failed_prewarm(self):
        manager = make_manager()
        manager._bot_prewarm = asyncio.ensure_future(_resolve(None))
        self.assertIsNone(await manager._take_prewarmed_client())

    async def test_failed_start_disconnects_and_returns_none(self):
        manager = make_manager()
        manager._cleanup_stale_bot_sessions = Mock()
        created = []

        def factory(*args, **kwargs):
            client = FakeBotClient()

            async def failing_start(bot_token=None):
                raise ConnectionError("no route to DC")

            client.start = failing_start
            created.append(client)
            return client

        with patch(
            "heroku.inline.core.TelegramClient", side_effect=factory
        ), patch(
            "heroku.inline.core.SQLiteSession", return_value=object()
        ), patch(
            "heroku.main.SESSIONS_DIR", "/tmp/opencode-prewarm-test"
        ):
            manager.prewarm_bot_client()
            result = await manager._bot_prewarm

        self.assertIsNone(result)
        self.assertTrue(created[0].disconnected)

    async def test_stop_cancels_pending_prewarm(self):
        manager = make_manager()
        manager._task = None
        manager._cleaner_task = None
        manager._bot_client = None
        manager._units = {}
        manager._custom_map = {}
        manager.init_complete = True

        async def hanging():
            await asyncio.Event().wait()
            return object(), "12345:abc"

        task = asyncio.ensure_future(hanging())
        manager._bot_prewarm = task

        await manager._stop()

        self.assertIsNone(manager._bot_prewarm)
        self.assertTrue(task.cancelled())
        self.assertIsNone(manager._bot_client)

    async def test_stop_disconnects_completed_unadopted_prewarm(self):
        manager = make_manager()
        manager._task = None
        manager._cleaner_task = None
        manager._bot_client = None
        manager._units = {}
        manager._custom_map = {}
        manager.init_complete = True

        client = FakeBotClient()
        task = asyncio.ensure_future(_resolve((client, "12345:abc")))
        manager._bot_prewarm = task
        await task  # completed, but never adopted

        await manager._stop()

        self.assertTrue(client.disconnected)
        self.assertIsNone(manager._bot_prewarm)
        self.assertIsNone(manager._bot_client)


async def _resolve(value):
    return value


class RegisterManagerPrewarmTest(unittest.IsolatedAsyncioTestCase):
    async def test_register_manager_adopts_prewarmed_client(self):
        manager = make_manager()
        manager._name = None
        manager._task = None
        manager._cleaner_task = None
        manager._db = FakeDB()
        manager._units = {}
        manager._custom_map = {}
        manager._assert_token = AsyncMock(return_value=True)
        manager._cleanup_stale_bot_sessions = Mock()
        manager._register_builtin_handlers = Mock()
        manager._ping_bot = AsyncMock(return_value=True)
        manager._cleaner = AsyncMock()

        bot = FakeBotClient()
        manager._bot_prewarm = asyncio.ensure_future(
            _resolve((bot, "12345:abc"))
        )

        created = []

        def factory(*args, **kwargs):
            client = FakeBotClient()
            created.append(client)
            return client

        with patch(
            "heroku.inline.core.TelegramClient", side_effect=factory
        ), patch(
            "heroku.inline.core.SQLiteSession", return_value=object()
        ), patch(
            "heroku.inline.core.TelethonBot"
        ), patch(
            "heroku.inline.core.get_display_name", return_value="Name"
        ):
            await manager.register_manager()

        self.assertTrue(manager.init_complete)
        self.assertIs(manager._bot_client, bot)
        self.assertEqual(created, [])
        self.assertTrue(bot.started)
        self.assertEqual(manager.bot_username, "mybot")

    async def test_register_manager_falls_back_without_prewarm(self):
        manager = make_manager()
        manager._name = None
        manager._task = None
        manager._cleaner_task = None
        manager._db = FakeDB()
        manager._units = {}
        manager._custom_map = {}
        manager._assert_token = AsyncMock(return_value=True)
        manager._cleanup_stale_bot_sessions = Mock()
        manager._register_builtin_handlers = Mock()
        manager._ping_bot = AsyncMock(return_value=True)
        manager._cleaner = AsyncMock()

        created = []

        def factory(*args, **kwargs):
            client = FakeBotClient()
            created.append(client)
            return client

        with patch(
            "heroku.inline.core.TelegramClient", side_effect=factory
        ), patch(
            "heroku.inline.core.SQLiteSession", return_value=object()
        ), patch(
            "heroku.inline.core.TelethonBot"
        ), patch(
            "heroku.inline.core.get_display_name", return_value="Name"
        ):
            await manager.register_manager()

        self.assertTrue(manager.init_complete)
        self.assertEqual(len(created), 1)
        self.assertIs(manager._bot_client, created[0])
        self.assertTrue(created[0].started)


if __name__ == "__main__":
    unittest.main()
