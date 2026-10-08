"""Runtime regression tests without importing the application's startup code."""

import ast
import asyncio
import collections
import copy
import functools
import html
import json
import logging
import re
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parent.parent


def load_definition(path, name, namespace):
    """Execute actual source definitions, replacing only external dependencies."""
    node = ast.parse((ROOT / path).read_text())
    for part in name.split("."):
        node = next(item for item in node.body if getattr(item, "name", None) == part)
    for item in ast.walk(node):
        if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            item.decorator_list = [
                decorator
                for decorator in item.decorator_list
                if isinstance(decorator, ast.Name)
                and decorator.id in {"staticmethod", "classmethod", "property"}
                and item is not node
            ]
    tree = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            node,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(tree), str(ROOT / path), "exec"), namespace)
    return namespace[node.name]


def is_serializable(value):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


class DatabasePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.writer = Mock()
        self.namespace = {
            "asyncio": asyncio,
            "collections": collections,
            "copy": copy,
            "json": json,
            "time": time,
            "logger": Mock(),
            "main": SimpleNamespace(_atomic_write_text=self.writer),
            "utils": SimpleNamespace(is_serializable=is_serializable),
            "register_exit_flusher": Mock(),
            "register_secrets": Mock(),
            "register_secret_if_named": Mock(),
        }
        for name in ("PointerList", "PointerDict"):
            load_definition("heroku/pointers.py", name, self.namespace)
        database = load_definition("heroku/database.py", "Database", self.namespace)
        self.db = database(SimpleNamespace(tg_id=1))
        self.db._db_file = Path("unused-test-config.json")
        self.db.update({"TestMod": {"value": 1, "nested": {"items": [1]}}})

    def test_first_save_writes_the_current_snapshot(self):
        self.assertTrue(self.db.save())
        self.assertEqual(json.loads(self.writer.call_args.args[1]), dict(self.db))
        self.assertEqual(len(self.db._revisions), 1)

    def test_unchanged_periodic_saves_do_not_write_or_duplicate_revisions(self):
        self.db.save()
        self.db._next_revision_call = 0
        for _ in range(3):
            self.assertTrue(self.db.save())
        self.writer.assert_called_once()
        self.assertEqual(len(self.db._revisions), 1)

    def test_direct_nested_changes_are_persisted(self):
        self.db.save()
        self.db["TestMod"]["nested"]["items"].append(2)
        self.assertTrue(self.db.save())
        self.assertEqual(self.writer.call_count, 2)
        self.assertEqual(
            json.loads(self.writer.call_args.args[1])["TestMod"]["nested"]["items"],
            [1, 2],
        )

    def test_failed_write_is_retried_even_when_content_is_unchanged(self):
        self.writer.side_effect = OSError("test write failure")
        self.assertFalse(self.db.save())
        self.assertIsNone(self.db._last_saved_data)
        self.writer.side_effect = None
        self.assertTrue(self.db.save())
        self.assertEqual(self.writer.call_count, 2)

    def test_failed_changed_write_keeps_the_last_successful_snapshot(self):
        self.db.save()
        original = self.db._last_saved_data
        self.db["TestMod"]["value"] = 2
        self.writer.side_effect = OSError("test write failure")
        self.assertFalse(self.db.save())
        self.assertEqual(self.db._last_saved_data, original)
        self.writer.side_effect = None
        self.assertTrue(self.db.save())
        self.assertEqual(self.writer.call_count, 3)

    def test_read_invalidates_the_saved_snapshot_cache(self):
        self.db.save()
        self.db._update_from_read(dict(self.db))
        self.assertTrue(self.db.save())
        self.assertEqual(self.writer.call_count, 2)

    def test_exit_flush_includes_unscheduled_nested_changes(self):
        self.db.save()
        self.db["TestMod"]["nested"]["items"].append(2)
        self.assertFalse(self.db._save_scheduled)
        self.db._flush_pending()
        self.assertEqual(self.writer.call_count, 2)

    def test_exit_flush_skips_an_unchanged_snapshot(self):
        self.db.save()
        self.db._flush_pending()
        self.writer.assert_called_once()

    def test_exit_flush_skips_an_uninitialized_database(self):
        del self.db._db_file
        self.db._flush_pending()
        self.writer.assert_not_called()

    def test_debounce_coalesces_changes_and_exit_flush_avoids_a_second_write(self):
        loop = Mock()
        self.namespace["asyncio"] = SimpleNamespace(get_running_loop=lambda: loop)
        self.db.set("TestMod", "first", 1)
        self.db.set("TestMod", "second", 2)
        loop.call_later.assert_called_once_with(0.5, self.db._flush)
        self.db._flush_pending()
        loop.call_later.call_args.args[1]()
        self.writer.assert_called_once()
        saved = json.loads(self.writer.call_args.args[1])["TestMod"]
        self.assertEqual((saved["first"], saved["second"]), (1, 2))

    def test_pointer_updates_are_saved(self):
        loop = Mock()
        self.namespace["asyncio"] = SimpleNamespace(get_running_loop=lambda: loop)
        self.db.pointer("TestMod", "nested", {}).update({"extra": True})
        self.db._flush_pending()
        self.assertTrue(json.loads(self.writer.call_args.args[1])["TestMod"]["nested"]["extra"])

    def test_redis_is_not_short_circuited_by_the_local_snapshot_cache(self):
        self.db._redis = object()
        self.db._saving_task = object()
        self.db._last_saved_data = json.dumps(self.db)
        self.assertTrue(self.db.save())
        self.writer.assert_not_called()


class ConfigAutosaverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config_type = type("ModuleConfig", (), {})
        self.library_type = type("Library", (), {})
        self.autosave = load_definition(
            "heroku/modules/loader.py",
            "LoaderMod._config_autosaver",
            {"loader": SimpleNamespace(ModuleConfig=self.config_type, Library=self.library_type)},
        )
        self.db = Mock()
        self.loader = SimpleNamespace(
            allmodules=SimpleNamespace(modules=[], libraries=[]), _db=self.db
        )

    def make_module(self, *, library=False, marked=True):
        module = self.library_type() if library else SimpleNamespace()
        module.config = self.config_type()
        module.config._config = {
            name: SimpleNamespace(value=value, **({"_save_marker": True} if marked else {}))
            for name, value in (("first", 1), ("second", 2))
        }
        module.pointer = Mock(return_value=Mock())
        module._lib_pointer = Mock(return_value=Mock())
        return module

    async def test_module_config_is_saved_once_per_batch(self):
        module = self.make_module()
        self.loader.allmodules.modules.append(module)
        await self.autosave(self.loader)
        module.pointer.assert_called_once_with("__config__", {})
        module.pointer.return_value.update.assert_called_once_with({"first": 1, "second": 2})
        self.assertFalse(hasattr(module.config._config["first"], "_save_marker"))
        self.db.save.assert_called_once()

    async def test_library_config_uses_its_own_pointer(self):
        module = self.make_module(library=True)
        self.loader.allmodules.libraries.append(module)
        await self.autosave(self.loader)
        module._lib_pointer.assert_called_once_with("__config__", {})
        module.pointer.assert_not_called()

    async def test_clean_config_keeps_the_legacy_database_check(self):
        module = self.make_module(marked=False)
        self.loader.allmodules.modules.append(module)
        await self.autosave(self.loader)
        module.pointer.assert_not_called()
        self.db.save.assert_called_once()

    async def test_failed_pointer_write_does_not_discard_save_markers(self):
        module = self.make_module()
        module.pointer.return_value.update.side_effect = ValueError("invalid config")
        self.loader.allmodules.modules.append(module)
        with self.assertRaises(ValueError):
            await self.autosave(self.loader)
        self.assertTrue(module.config._config["first"]._save_marker)
        self.assertTrue(module.config._config["second"]._save_marker)


class LoggingCallerTest(unittest.TestCase):
    def setUp(self):
        self.namespace = {"sys": sys, "logging": logging, "asyncio": asyncio}
        self.find_caller = load_definition(
            "heroku/log.py", "_get_logging_caller", self.namespace
        )

    def test_nested_logging_preserves_the_account_tag(self):
        _heroku_client_id_logging_tag = 12345

        def nested():
            return self.find_caller()

        self.assertEqual(nested(), _heroku_client_id_logging_tag)

    def test_nearest_account_tag_wins(self):
        _heroku_client_id_logging_tag = 12345

        def nested():
            _heroku_client_id_logging_tag = 67890
            return self.find_caller(), _heroku_client_id_logging_tag

        self.assertEqual(nested(), (67890, 67890))
        self.assertEqual(_heroku_client_id_logging_tag, 12345)

    def test_non_integer_tags_are_ignored(self):
        _heroku_client_id_logging_tag = "not an account"
        self.assertIsNone(self.find_caller())
        self.assertIsInstance(_heroku_client_id_logging_tag, str)

    def test_missing_frame_support_does_not_break_logging(self):
        self.namespace["sys"] = SimpleNamespace(_getframe=Mock(side_effect=AttributeError))
        self.assertIsNone(self.find_caller())

    def test_emit_routes_logs_without_inspect_stack(self):
        stack = Mock(side_effect=AssertionError("inspect.stack must not be used"))
        self.namespace["inspect"] = SimpleNamespace(stack=stack)
        handler_type = load_definition("heroku/log.py", "TelegramLogsHandler", self.namespace)
        handler = handler_type([logging.NullHandler()], 10)
        handler.tg_level = logging.CRITICAL + 1
        handler.setLevel(-1)
        _heroku_client_id_logging_tag = 12345
        record = logging.LogRecord("test", logging.DEBUG, __file__, 1, "message", (), None)
        try:
            handler.emit(record)
            self.assertEqual(record.heroku_caller, _heroku_client_id_logging_tag)
            self.assertEqual(handler.dumps(client_id=12345), ["message"])
            self.assertEqual(handler.dumps(client_id=67890), [])
            stack.assert_not_called()
        finally:
            handler.close()


class GitPollerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        run_sync = load_definition(
            "heroku/utils/other.py", "run_sync", {"asyncio": asyncio, "functools": functools}
        )
        self.namespace = {
            "NO_GIT": False,
            "utils": SimpleNamespace(run_sync=run_sync),
        }
        self.poller = load_definition(
            "heroku/modules/updater.py", "UpdaterMod.poller", self.namespace
        )
        self.module = SimpleNamespace(
            config={"GIT_BRANCH": "test", "disable_notifications": True, "autoupdate": False},
            _pending=None,
            _log_git_poll_error=Mock(),
        )

    async def test_git_poll_runs_outside_the_event_loop(self):
        thread_ids = []

        def state(branch):
            thread_ids.append(threading.get_ident())
            self.assertEqual(branch, "test")
            return "current", "pending", False

        self.module._get_update_state = state
        await self.poller(self.module)
        self.assertNotEqual(thread_ids[0], threading.get_ident())
        self.assertEqual(self.module._pending, "pending")

    async def test_branch_changes_discard_stale_poll_results(self):
        started = threading.Event()
        release = threading.Event()

        def state(branch):
            started.set()
            if not release.wait(3):
                raise TimeoutError("test worker timed out")
            return "current", "old-branch-pending", False

        self.module._get_update_state = state
        task = asyncio.create_task(self.poller(self.module))
        try:
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3))
            self.module.config["GIT_BRANCH"] = "main"
        finally:
            release.set()
            await task
        self.assertIsNone(self.module._pending)

    async def test_poll_errors_keep_existing_error_handling(self):
        error = OSError("test Git error")
        self.module._get_update_state = Mock(side_effect=error)
        await self.poller(self.module)
        self.module._log_git_poll_error.assert_called_once_with(error)

    async def test_no_git_skips_the_worker(self):
        self.namespace["NO_GIT"] = True
        self.module._get_update_state = Mock()
        await self.poller(self.module)
        self.module._get_update_state.assert_not_called()


class RuntimeInfoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.thread_ids = []

        def metric(value):
            def collect():
                self.thread_ids.append(threading.get_ident())
                return value

            return collect

        async def get_placeholders(data, template):
            return data

        run_sync = load_definition(
            "heroku/utils/other.py", "run_sync", {"asyncio": asyncio, "functools": functools}
        )
        self.utils = SimpleNamespace(
            run_sync=run_sync,
            is_up_to_date=metric(True),
            get_commit_url=metric("build"),
            get_cpu_usage=metric("0.00"),
            get_git_status=metric("Clean"),
            escape_html=lambda value: html.escape(str(value)),
            get_named_platform=lambda: "VDS",
            get_named_platform_emoji=lambda: "",
            get_swap_usage=lambda: {"error": "No swap"},
            formatted_uptime=lambda: "1:00",
            get_ram_usage=lambda: 1,
            get_placeholders=get_placeholders,
        )
        self.namespace = {
            "utils": self.utils,
            "time": time,
            "re": re,
            "logger": Mock(),
            "get_display_name": lambda user: "Tester",
            "getpass": SimpleNamespace(getuser=lambda: "test"),
            "lib_platform": SimpleNamespace(
                python_version=lambda: "3.10", node=lambda: "test", release=lambda: "test"
            ),
            "version": SimpleNamespace(__version__=(6, 6, 6), branch="test"),
            "herokutl": SimpleNamespace(__version__="2.2.0.dev4"),
            "psutil": SimpleNamespace(cpu_count=lambda **kwargs: 1, cpu_percent=lambda: 0),
            "DEFAULT_INFO_MESSAGE": "fallback {build}",
        }
        collect = load_definition(
            "heroku/modules/heroku_info.py", "HerokuInfoMod._get_runtime_info", self.namespace
        )
        self.render = load_definition(
            "heroku/modules/heroku_info.py", "HerokuInfoMod._render_info", self.namespace
        )
        self.module = SimpleNamespace(
            _client=SimpleNamespace(heroku_me=SimpleNamespace(id=1)),
            strings={"up-to-date": "current", "update_required": "update {prefix}"},
            get_prefix=lambda: ".",
            _get_os_name=lambda: "Linux",
            _get_runtime_info=collect,
            _get_effective_banner=lambda: (None, False),
            _get_effective_info_template=lambda key: "{build}|{cpu_usage}|{git_status}|{upd}",
        )

    async def test_metrics_run_in_the_worker_and_rendering_is_preserved(self):
        text = await self.render(self.module, time.perf_counter_ns())
        self.assertEqual(text, "build|0.00|Clean|current")
        self.assertEqual(len(self.thread_ids), 4)
        self.assertNotIn(threading.get_ident(), self.thread_ids)

    async def test_rich_render_without_a_banner_keeps_the_empty_figure_guard(self):
        self.module._get_effective_info_template = (
            lambda key: '<figure><img src="{banner_url}"/></figure><p>{build}</p>'
        )
        text = await self.render(self.module, time.perf_counter_ns(), "rich_info_message")
        self.assertEqual(text, "<p>build</p>")

    async def test_unknown_placeholder_keeps_the_plain_fallback(self):
        self.module._get_effective_info_template = lambda key: "{missing}"
        text = await self.render(self.module, time.perf_counter_ns())
        self.assertEqual(text, "fallback build")
        self.namespace["logger"].exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
