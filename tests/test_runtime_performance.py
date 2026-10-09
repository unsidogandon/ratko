"""Runtime regression tests without importing the application's startup code."""

import ast
import asyncio
import base64
import collections
import copy
import functools
import html
import inspect
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import weakref

import orjson
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent


def _assign_target_id(item):
    """Return the target name of a module-level (annotated) assignment"""
    if isinstance(item, ast.Assign) and len(item.targets) == 1:
        target = item.targets[0]
    elif isinstance(item, ast.AnnAssign) and item.value is not None:
        target = item.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def load_definition(path, name, namespace):
    """Execute actual source definitions, replacing only external dependencies."""
    node = ast.parse((ROOT / path).read_text())
    export_name = name.split(".")[-1]
    for part in name.split("."):
        node = next(
            item
            for item in node.body
            if getattr(item, "name", None) == part
            or _assign_target_id(item) == part
        )
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
    return namespace[export_name]


def is_serializable(value):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.namespace = {"os": os, "tempfile": tempfile, "Path": Path}
        self.write = load_definition(
            "heroku/main.py", "_atomic_write_text", self.namespace
        )
        self.directory = Path(tempfile.mkdtemp(prefix="ratko-atomic-", dir="/tmp/opencode"))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_bytes_payloads_are_written_verbatim(self):
        target = self.directory / "db.json"
        self.write(target, b'{"a": 1}')
        self.assertEqual(target.read_bytes(), b'{"a": 1}')

    def test_text_payloads_keep_utf8_encoding(self):
        target = self.directory / "config.json"
        self.write(target, '{"тест": "ok"}')
        self.assertEqual(target.read_text(encoding="utf-8"), '{"тест": "ok"}')

    def test_replacement_leaves_no_temporary_files(self):
        target = self.directory / "db.json"
        self.write(target, b"first")
        self.write(target, b"second")
        self.assertEqual(target.read_bytes(), b"second")
        self.assertEqual(list(self.directory.iterdir()), [target])


class DatabasePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.writer = Mock()
        self.namespace = {
            "asyncio": asyncio,
            "collections": collections,
            "copy": copy,
            "json": json,
            "orjson": orjson,
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
        self.assertIsInstance(self.writer.call_args.args[1], bytes)
        self.assertEqual(json.loads(self.writer.call_args.args[1]), dict(self.db))
        self.assertEqual(len(self.db._revisions), 1)

    def test_snapshot_matches_stdlib_json_for_non_str_keys(self):
        self.db[12345] = {"inner": {678: "ok"}}
        self.assertTrue(self.db.save())
        written = json.loads(self.writer.call_args.args[1])
        self.assertEqual(written, json.loads(json.dumps(dict(self.db))))
        self.assertEqual(written["12345"]["inner"]["678"], "ok")

    def test_nan_values_are_saved_as_null(self):
        self.db["TestMod"]["value"] = float("nan")
        self.assertTrue(self.db.save())
        written = json.loads(self.writer.call_args.args[1])
        self.assertIsNone(written["TestMod"]["value"])

    def test_datetime_values_are_serialized_natively(self):
        import datetime

        self.db["TestMod"]["value"] = datetime.datetime(2026, 1, 1, 12, 0)
        self.assertTrue(self.db.save())
        written = json.loads(self.writer.call_args.args[1])
        self.assertEqual(written["TestMod"]["value"], "2026-01-01T12:00:00")

    def test_unserializable_values_trigger_revision_restore(self):
        self.assertTrue(self.db.save())
        self.db["TestMod"]["value"] = object()
        with self.assertRaises(RuntimeError):
            self.db.save()
        self.assertEqual(self.db["TestMod"]["value"], 1)

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

    def test_get_nocopy_returns_the_stored_object_without_copying(self):
        value = {"list": [1, 2]}
        self.db["TestMod"]["value"] = value
        self.assertIs(self.db.get_nocopy("TestMod", "value"), value)
        copied = self.db.get("TestMod", "value")
        self.assertIsNot(copied, value)
        self.assertEqual(copied, value)

    def test_get_nocopy_returns_the_default_for_missing_keys(self):
        self.assertIsNone(self.db.get_nocopy("TestMod", "missing"))
        self.assertEqual(
            self.db.get_nocopy("TestMod", "missing", "fallback"), "fallback"
        )


class UserPrefixesTest(unittest.TestCase):
    def setUp(self):
        self.namespace = {}
        self.normalize = load_definition(
            "heroku/utils/args.py", "normalize_prefixes", self.namespace
        )
        self.namespace["normalize_prefixes"] = self.normalize
        self.user_prefixes = load_definition(
            "heroku/utils/args.py", "user_prefixes", self.namespace
        )

    @staticmethod
    def make_db(**values):
        return SimpleNamespace(
            get_nocopy=lambda owner, key, default=None: values.get(key, default)
        )

    def test_empty_db_falls_back_to_the_dot_prefix(self):
        self.assertEqual(self.user_prefixes(self.make_db(), "heroku.main"), ["."])

    def test_primary_and_alias_prefixes_are_combined_and_deduplicated(self):
        db = self.make_db(command_prefix="!", command_prefix_aliases=[".", "!", ""])
        self.assertEqual(self.user_prefixes(db, "heroku.main"), ["!", "."])

    def test_personal_prefixes_win_for_other_users(self):
        db = self.make_db(command_prefix=".", command_prefixes={"42": ["/", "/"]})
        self.assertEqual(self.user_prefixes(db, "heroku.main", 42, 1), ["/"])
        self.assertEqual(self.user_prefixes(db, "heroku.main", 1, 1), ["."])

    def test_other_users_without_personal_prefixes_get_the_defaults(self):
        db = self.make_db(command_prefix="!", command_prefixes={})
        self.assertEqual(self.user_prefixes(db, "heroku.main", 42, 1), ["!"])


class RedactPipelineTest(unittest.TestCase):
    def setUp(self):
        self.namespace = {
            "base64": base64,
            "html": html,
            "quote": quote,
            "re": re,
        }
        for name in (
            "_secrets",
            "_secrets_sorted",
            "_secret_names",
            "_PRIVATE_KEY_RE",
            "_BOT_LIKE_TOKEN_RE",
            "_API_TOKEN_RE",
            "_AUTH_HEADER_RE",
            "_URL_CREDENTIALS_RE",
            "_KEY_VALUE_SECRET_RE",
            "register_secret",
            "redact",
        ):
            load_definition("heroku/_internal.py", name, self.namespace)

    def test_registered_secrets_are_redacted(self):
        register, redact = self.namespace["register_secret"], self.namespace["redact"]
        register("short-secret")
        register("a-much-longer-secret-value")
        self.assertEqual(
            redact("a-much-longer-secret-value and short-secret"),
            "[REDACTED] and [REDACTED]",
        )

    def test_secret_registration_updates_the_sorted_cache(self):
        self.namespace["register_secret"]("another-secret-value")
        self.assertIn("another-secret-value", self.namespace["_secrets_sorted"])
        self.assertEqual(
            self.namespace["_secrets_sorted"],
            sorted(self.namespace["_secrets"], key=len, reverse=True),
        )

    def test_pattern_based_redaction_still_works(self):
        redact = self.namespace["redact"]
        self.assertEqual(
            redact("pk = 123456:ABCDEF-ghijklmnopqrstuvwxyz123456"),
            "pk = [REDACTED]",
        )
        self.assertEqual(redact("key sk-abcdefghijklmnopqrst"), "key [REDACTED]")
        self.assertEqual(
            redact("Header: Bearer abc123xyz"), "Header: Bearer [REDACTED]"
        )
        self.assertEqual(
            redact("db at postgres://user:pass@host/db"),
            "db at postgres://[REDACTED]@host/db",
        )
        self.assertEqual(redact("password: hunter2"), "password: [REDACTED]")
        self.assertIn(
            "[REDACTED PRIVATE KEY]",
            redact("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"),
        )


class _CallerModuleBase:
    """namespace stand-in for heroku.types.Module"""


class _ProbeModule(_CallerModuleBase):
    """First Module subclass in this test module's globals"""

    def _probe_frame(self):
        ...


_FIND_CALLER_HOLDER: dict = {}


def _probe_frame():
    return _FIND_CALLER_HOLDER["find_caller"]()


class FindCallerTest(unittest.TestCase):
    def setUp(self):
        self.namespace = {
            "inspect": inspect,
            "sys": sys,
            "Module": _CallerModuleBase,
        }
        self.cache = load_definition(
            "heroku/utils/entity.py", "_module_classes_cache", self.namespace
        )
        self.namespace["_MODULE_CLASSES_CACHE_LIMIT"] = load_definition(
            "heroku/utils/entity.py", "_MODULE_CLASSES_CACHE_LIMIT", self.namespace
        )
        self.module_class_of_globals = load_definition(
            "heroku/utils/entity.py", "_module_class_of_globals", self.namespace
        )
        self.find_caller = load_definition(
            "heroku/utils/entity.py", "find_caller", self.namespace
        )

    @staticmethod
    def frame(f_globals, co_name="function", f_locals=None):
        return SimpleNamespace(
            f_globals=f_globals,
            f_locals=f_locals or {},
            f_code=SimpleNamespace(co_name=co_name),
        )

    def test_module_frame_resolves_to_the_same_named_method(self):
        class MyMod(_CallerModuleBase):
            def probe(self):
                ...

        frame = self.frame({"MyMod": MyMod}, co_name="probe")
        self.assertIs(self.find_caller(stack=[frame]), MyMod.probe)

    def test_first_module_frame_wins_over_later_ones(self):
        class First(_CallerModuleBase):
            def anything(self):
                ...

        class Second(_CallerModuleBase):
            def anything(self):
                ...

        frames = [
            self.frame({"Second": Second}, co_name="anything"),
            self.frame({"First": First}, co_name="anything"),
        ]
        self.assertIs(self.find_caller(stack=frames), Second.anything)

    def test_future_dispatcher_fallback_returns_the_dispatched_func(self):
        sentinel = object()
        frame = self.frame(
            {"CommandDispatcher": object},
            co_name="future_dispatcher",
            f_locals={"func": sentinel},
        )
        self.assertIs(self.find_caller(stack=[frame]), sentinel)

    def test_no_matches_return_none(self):
        self.assertIsNone(self.find_caller(stack=[self.frame({})]))

    def test_truthy_non_list_stack_matches_nothing(self):
        self.assertIsNone(self.find_caller(stack="not-a-stack"))

    def test_cache_is_keyed_per_globals_dict(self):
        class First(_CallerModuleBase):
            def probe(self):
                ...

        class Second(_CallerModuleBase):
            def probe(self):
                ...

        first_globals = {"First": First}
        second_globals = {"Second": Second}
        self.assertIs(
            self.find_caller(stack=[self.frame(first_globals, "probe")]), First.probe
        )
        self.assertIs(
            self.find_caller(stack=[self.frame(second_globals, "probe")]), Second.probe
        )
        self.assertIs(
            self.find_caller(stack=[self.frame(first_globals, "probe")]), First.probe
        )
        self.assertIs(self.module_class_of_globals(first_globals), First)
        self.assertIsNone(self.module_class_of_globals({}))

    def test_real_stack_capture_resolves_module_functions(self):
        _FIND_CALLER_HOLDER["find_caller"] = self.find_caller
        self.assertIs(_probe_frame(), _ProbeModule._probe_frame)


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
