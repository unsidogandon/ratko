"""Cleanup/lifecycle regressions using real definitions without application startup."""

import ast
import asyncio
import contextlib
import copy
import functools
import html
import inspect
import io
import json
import logging
import shutil
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

from test_runtime_performance import ROOT, is_serializable, load_definition


class DatabaseAutofixTest(unittest.TestCase):
    def setUp(self):
        self.fix = load_definition(
            "heroku/database.py",
            "Database.process_db_autofix",
            {"utils": SimpleNamespace(is_serializable=is_serializable), "logger": Mock()},
        )

    def test_valid_nested_module_data_is_not_changed(self):
        data = {"TestMod": {"nested": {"items": [1, None, {"test": True}]}}, 1: {2: "ok"}}
        original = copy.deepcopy(data)
        self.assertTrue(self.fix(object(), data))
        self.assertEqual(data, original)

    def test_invalid_root_keys_and_non_mapping_sections_are_removed(self):
        data = {0.5: {"test": 1}, "valid": {"test": 2}, "invalid": []}
        self.assertTrue(self.fix(object(), data))
        self.assertEqual(data, {"valid": {"test": 2}})

    def test_multiple_invalid_subkeys_are_removed_without_iteration_errors(self):
        data = {"valid": {0.5: "bad", None: "bad", "test": 2}}
        self.assertTrue(self.fix(object(), data))
        self.assertEqual(data, {"valid": {"test": 2}})

    def test_non_mapping_input_is_rejected(self):
        for data in (None, [], "test"):
            with self.subTest(data=data):
                self.assertFalse(self.fix(object(), data))

    def test_unserializable_content_is_not_silently_removed(self):
        value = object()
        data = {"valid": {"test": value}}
        self.assertFalse(self.fix(object(), data))
        self.assertIs(data["valid"]["test"], value)


class TranslationPackTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.namespace = {
            "json": json,
            "yaml": SimpleNamespace(load=json.loads),
            "Path": Path,
            "urlsplit": urlsplit,
            "logger": Mock(),
        }
        base = load_definition("heroku/translations.py", "BaseTranslator", self.namespace)
        self.translator = base()

    def parse(self, data, prefix="heroku.modules."):
        return self.translator._get_pack_raw(json.dumps(data), ".yml", prefix)

    def test_single_language_pack_and_module_names_are_preserved(self):
        self.assertEqual(
            self.parse({"ab": {"name": "ignored", "test": "value"}}),
            {"heroku.modules.ab.test": "value"},
        )

    def test_multilingual_pack_is_flattened_for_each_language(self):
        self.assertEqual(
            self.parse({"en": {"test": {"key": "value"}}, "ru": {"test": {"key": "тест"}}}),
            {
                "en": {"heroku.modules.test.key": "value"},
                "ru": {"heroku.modules.test.key": "тест"},
            },
        )

    def test_named_languages_and_absolute_module_names_are_supported(self):
        self.assertEqual(
            self.parse({"unsido": {"$custom.mod": {"key": "value"}}}),
            {"unsido": {"custom.mod.key": "value"}},
        )

    def test_custom_prefix_and_empty_pack_are_supported(self):
        self.assertEqual(self.parse({"test": {"key": "value"}}, ""), {"test.key": "value"})
        self.assertEqual(self.parse({}), {})

    def test_flat_json_format_is_unchanged(self):
        data = {"heroku.modules.test.key": "value"}
        self.assertEqual(self.translator._get_pack_raw(json.dumps(data), ".json"), data)

    def test_invalid_yaml_root_is_rejected(self):
        for data in (None, [], "test"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.parse(data)

    async def test_remote_multilingual_pack_selects_configured_language(self):
        self.namespace.update(
            {
                "PACKS": Path("unused"),
                "SUPPORTED_LANGUAGES": {},
                "normalize_language_token": lambda value: value,
                "iter_language_codes": lambda value: (value,),
                "get_language_pack_path": lambda value: None,
                "fetch_text": object(),
                "utils": SimpleNamespace(
                    check_url=lambda value: value.startswith("https://"),
                    run_sync=AsyncMock(
                        return_value=json.dumps(
                            {
                                "en": {"test": {"key": "value"}},
                                "ru": {"test": {"key": "тест"}},
                            }
                        )
                    ),
                ),
            }
        )
        init = load_definition("heroku/translations.py", "Translator.init", self.namespace)
        self.translator.db = SimpleNamespace(
            get=lambda *args: "https://example.invalid/pack.yml ru"
        )
        self.translator.raw_data = {}
        self.translator._get_pack_content = lambda path: {"default.key": "default"}
        self.assertTrue(await init(self.translator))
        self.assertEqual(self.translator._data["heroku.modules.test.key"], "тест")

    async def test_remote_json_with_query_string_uses_json_parser(self):
        self.namespace.update(
            {
                "PACKS": Path("unused"),
                "SUPPORTED_LANGUAGES": {},
                "normalize_language_token": lambda value: value,
                "get_language_pack_path": lambda value: None,
                "fetch_text": object(),
                "utils": SimpleNamespace(
                    check_url=lambda value: value.startswith("https://"),
                    run_sync=AsyncMock(
                        return_value=json.dumps({"custom.key": "value"})
                    ),
                ),
            }
        )
        init = load_definition("heroku/translations.py", "Translator.init", self.namespace)
        self.translator.db = SimpleNamespace(
            get=lambda *args: "https://example.invalid/pack.json?v=1"
        )
        self.translator.raw_data = {}
        self.translator._get_pack_content = lambda path: {}
        self.assertTrue(await init(self.translator))
        self.assertEqual(self.translator._data, {"custom.key": "value"})


class StringsFallbackTest(unittest.TestCase):
    def setUp(self):
        self.rand = Mock(
            side_effect=AssertionError("utils.rand must not run per string lookup")
        )
        self.namespace = {
            "__name__": "heroku.translations",
            "logger": Mock(),
            "utils": SimpleNamespace(rand=self.rand),
            "iter_language_codes": lambda value: (value,),
            "_MISSING_STRINGS_ATTR": "_ratko_missing_strings_",
        }
        self.strings_cls = load_definition(
            "heroku/translations.py", "Strings", self.namespace
        )
        self.mod = SimpleNamespace(
            __module__="heroku.modules.fakemod",
            strings={"name": "Base", "shared": "Base shared"},
            strings_en={"name": "English"},
        )
        self.translator = SimpleNamespace(
            db=SimpleNamespace(get=lambda *args, **kwargs: "en"),
            getkey=lambda key: None,
            raw_data={},
        )

    def test_translated_keys_are_taken_from_language_strings(self):
        strings = self.strings_cls(self.mod, self.translator)
        self.assertEqual(strings["name"], "English")
        self.rand.assert_not_called()

    def test_missing_keys_fall_back_to_base_strings_without_randomness(self):
        strings = self.strings_cls(self.mod, self.translator)
        self.assertEqual(strings["shared"], "Base shared")
        self.assertEqual(strings["missing"], "Unknown strings: missing")
        self.rand.assert_not_called()

    def test_modules_without_language_dicts_use_base_strings(self):
        mod = SimpleNamespace(
            __module__="heroku.modules.fakemod",
            strings={"name": "Base"},
        )
        strings = self.strings_cls(mod, self.translator)
        self.assertEqual(strings["name"], "Base")
        self.rand.assert_not_called()


class RegistryLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {"disabled_modules": [], "disabled_commands": {}}
        self.namespace = {
            "main": SimpleNamespace(__name__="heroku.main"),
            "logger": Mock(),
            "copy": copy,
            "contextlib": contextlib,
            "utils": SimpleNamespace(iter_attrs=lambda obj: obj.attributes),
        }
        registry_type = type("Registry", (), {})
        for name in (
            "_is_module_disabled",
            "register_commands",
            "register_inline_stuff",
            "register_watchers",
            "register_raw_handlers",
            "register_bot_update_handlers",
        ):
            setattr(
                registry_type,
                name,
                load_definition("heroku/loader.py", f"Modules.{name}", self.namespace),
            )
        self.registry = registry_type()
        self.registry._db = SimpleNamespace(
            get=lambda owner, key, default=None: self.settings.get(key, default)
        )
        self.registry.client = SimpleNamespace(
            tg_id=1, dispatcher=SimpleNamespace(raw_handlers=[])
        )
        self.registry.inline = SimpleNamespace(register_bot_update_handler=Mock())
        self.registry.modules = []
        self.registry.commands = {}
        self.registry._command_handlers = {}
        self.registry.inline_handlers = {}
        self.registry.callback_handlers = {}
        self.registry.watchers = []
        self.registry.aliases = {}
        self.registry._core_commands = []
        self.registry._remove_core_protection = False

    def module(self, name):
        module = type(name, (), {})()
        module.__origin__ = "<file>"
        handler = Mock()
        module.heroku_commands = {"Test": handler, "Other": Mock()}
        module.heroku_inline_handlers = {"inline": handler}
        module.heroku_callback_handlers = {"callback": handler}
        module.heroku_watchers = {"watcher": handler}
        module.attributes = [
            ("raw", SimpleNamespace(is_raw_handler=True, id="raw")),
            (
                "bot",
                SimpleNamespace(
                    is_bot_update_handler=True, bot_update_types=["message"], id="bot"
                ),
            ),
        ]
        return module

    async def collect_once(self):
        self.namespace["asyncio"] = SimpleNamespace(
            sleep=AsyncMock(side_effect=[None, asyncio.CancelledError()])
        )
        collector = load_definition("heroku/loader.py", "Modules._junk_collector", self.namespace)
        with self.assertRaises(asyncio.CancelledError):
            await collector(self.registry)

    async def test_collector_does_not_restore_disabled_or_unloading_handlers(self):
        disabled = self.module("DisabledMod")
        unloading = self.module("UnloadingMod")
        unloading._unloading = True
        enabled = self.module("EnabledMod")
        self.registry.modules = [enabled, disabled, unloading]
        self.settings["disabled_modules"] = ["DisabledMod"]
        await self.collect_once()
        self.assertEqual(
            self.registry.commands,
            {
                "test": enabled.heroku_commands["Test"],
                "other": enabled.heroku_commands["Other"],
            },
        )
        self.assertEqual(self.registry.inline_handlers, enabled.heroku_inline_handlers)
        self.assertEqual(self.registry.callback_handlers, enabled.heroku_callback_handlers)
        self.assertEqual(self.registry.watchers, list(enabled.heroku_watchers.values()))

    async def test_collector_filters_legacy_command_disables_case_insensitively(self):
        module = self.module("TestMod")
        self.registry.modules = [module]
        self.settings["disabled_commands"] = {"TestMod": ["TEST"]}
        await self.collect_once()
        self.assertEqual(list(self.registry.commands), ["other"])
        self.assertNotIn("test", self.registry._command_handlers)

    async def test_collector_preserves_command_conflicts_between_enabled_modules(self):
        modules = [self.module("FirstMod"), self.module("SecondMod")]
        self.registry.modules = modules
        await self.collect_once()
        self.assertEqual(
            self.registry._command_handlers["test"],
            [module.heroku_commands["Test"] for module in modules],
        )

    def test_direct_registration_respects_disabled_modules(self):
        module = self.module("TestMod")
        self.settings["disabled_modules"] = ["TestMod"]
        for register in (
            self.registry.register_commands,
            self.registry.register_inline_stuff,
            self.registry.register_watchers,
            self.registry.register_raw_handlers,
            self.registry.register_bot_update_handlers,
        ):
            register(module)
        self.assertEqual(self.registry.commands, {})
        self.assertEqual(self.registry.inline_handlers, {})
        self.assertEqual(self.registry.callback_handlers, {})
        self.assertEqual(self.registry.watchers, [])
        self.assertEqual(self.registry.client.dispatcher.raw_handlers, [])
        self.registry.inline.register_bot_update_handler.assert_not_called()

    def test_enabled_module_and_unblocked_commands_are_registered(self):
        module = self.module("TestMod")
        self.settings["disabled_commands"] = {"TestMod": ["test"]}
        self.registry.register_commands(module)
        self.registry.register_watchers(module)
        self.registry.register_raw_handlers(module)
        self.registry.register_bot_update_handlers(module)
        self.assertEqual(list(self.registry.commands), ["other"])
        self.assertEqual(self.registry.watchers, list(module.heroku_watchers.values()))
        self.assertEqual(len(self.registry.client.dispatcher.raw_handlers), 1)
        self.registry.inline.register_bot_update_handler.assert_called_once()


class LogBatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.namespace = {
            "asyncio": asyncio,
            "logging": logging,
            "utils": SimpleNamespace(
                is_private_asset_channel=AsyncMock(return_value=True),
                chunks=lambda text, size: [text] if text else [],
                escape_html=html.escape,
            ),
            "HerokuException": type("HerokuException", (), {}),
        }
        self.sender = load_definition(
            "heroku/log.py", "TelegramLogsHandler.sender", self.namespace
        )
        self.bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        self.handler = SimpleNamespace(
            _send_lock=asyncio.Lock(),
            tg_buff=[("old record", 1)],
            _mods={
                1: SimpleNamespace(
                    client=object(), logchat=1, inline=SimpleNamespace(bot=self.bot)
                )
            },
            _unsafe_destinations_warned=set(),
            force_send_all=False,
            _exc_sender=AsyncMock(),
            get_logs_topic_id_by_client=AsyncMock(return_value=1),
        )

    async def test_records_created_during_preparation_remain_for_next_batch(self):
        async def topic(client):
            self.handler.tg_buff.append(("new record", 1))
            return 1

        self.handler.get_logs_topic_id_by_client = topic
        await self.sender(self.handler)
        self.assertEqual(self.bot.send_message.call_args.args[1], "<code>old record</code>")
        self.assertTrue(self.handler.tg_buff)
        self.assertTrue(all(item[0] == "new record" for item in self.handler.tg_buff))

    async def test_records_created_during_delivery_remain_for_next_batch(self):
        async def send(*args, **kwargs):
            self.handler.tg_buff.append(("new record", 1))

        self.bot.send_message.side_effect = send
        await self.sender(self.handler)
        self.assertEqual(self.handler.tg_buff, [("new record", 1)])

    async def test_account_routing_and_html_escaping_are_preserved(self):
        self.handler.tg_buff.extend([("other account", 2), ("<shared>", None)])
        await self.sender(self.handler)
        self.assertEqual(
            self.bot.send_message.call_args.args[1],
            "<code>old record&lt;shared&gt;</code>",
        )

    async def test_replaced_buffer_is_not_cleared_by_an_older_batch(self):
        async def topic(client):
            self.handler.tg_buff = [("after clearlogs", 1)]
            return 1

        self.handler.get_logs_topic_id_by_client = topic
        await self.sender(self.handler)
        self.assertEqual(self.handler.tg_buff, [("after clearlogs", 1)])

    async def test_clearlogs_keeps_a_list_for_future_records(self):
        self.namespace.update(
            {
                "logging": SimpleNamespace(
                    getLogger=lambda: SimpleNamespace(handlers=[self.handler])
                ),
                "utils": SimpleNamespace(answer=AsyncMock()),
            }
        )
        clear = load_definition("heroku/modules/test.py", "TestMod.clearlogs", self.namespace)
        await clear(SimpleNamespace(strings={"logs_cleared": "cleared"}), object())
        self.handler.tg_buff += [("new record", 1)]
        self.assertEqual(self.handler.tg_buff, [("new record", 1)])

    async def test_no_records_means_no_network_calls(self):
        self.handler.tg_buff.clear()
        await self.sender(self.handler)
        self.namespace["utils"].is_private_asset_channel.assert_not_called()


class InlineLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = SimpleNamespace(time=Mock(return_value=100))
        self.namespace = {
            "time": self.clock,
            "inspect": inspect,
            "asyncio": asyncio,
            "functools": functools,
            "logger": Mock(),
            "utils": SimpleNamespace(
                rand=Mock(side_effect=["first", "second", "third"])
            ),
            "make_button": lambda **kwargs: kwargs,
        }
        manager_type = type("InlineManager", (), {})
        for name in ("_normalize_markup", "_generate_markup", "_unload_unit"):
            setattr(
                manager_type,
                name,
                load_definition("heroku/inline/utils.py", f"Utils.{name}", self.namespace),
            )
        self.manager = manager_type()
        self.manager._units = {}
        self.manager._custom_map = {}
        self.manager._markup_ttl = 10
        self.manager._get_button_style = lambda button: None
        self.manager._get_button_emoji_id = lambda button: None

    async def clean_once(self):
        self.namespace["asyncio"] = SimpleNamespace(
            sleep=AsyncMock(side_effect=asyncio.CancelledError())
        )
        cleaner = load_definition(
            "heroku/inline/core.py", "InlineManager._cleaner", self.namespace
        )
        with self.assertRaises(asyncio.CancelledError):
            await cleaner(self.manager)

    async def test_default_deadline_is_stored_once_not_extended_by_cleaner(self):
        self.manager._units["test"] = {}
        await self.clean_once()
        self.assertEqual(self.manager._units["test"]["ttl"], 110)
        self.clock.time.return_value = 111
        await self.clean_once()
        self.assertNotIn("test", self.manager._units)

    async def test_explicit_deadline_is_respected(self):
        self.manager._units = {"expired": {"ttl": 99}, "alive": {"ttl": 101}}
        await self.clean_once()
        self.assertEqual(list(self.manager._units), ["alive"])

    async def test_standalone_callbacks_expire(self):
        self.manager._generate_markup({"text": "test", "callback": Mock()})
        self.clock.time.return_value = 111
        await self.clean_once()
        self.assertEqual(self.manager._custom_map, {})

    async def test_callback_deadline_does_not_move_on_repeated_generation(self):
        button = {"text": "test", "callback": Mock()}
        self.manager._generate_markup(button)
        self.clock.time.return_value = 105
        self.manager._generate_markup(button)
        self.assertEqual(self.manager._custom_map["first"]["ttl"], 110)

    async def test_unload_removes_callbacks_from_original_and_edited_keyboards(self):
        self.manager._units = {
            "test": {"ttl": 110, "buttons": [[{"text": "test", "callback": Mock()}]]},
            "other": {"ttl": 110},
        }
        self.manager._generate_markup("test")
        self.manager._generate_markup({"text": "edited", "callback": Mock()}, unit_id="test")
        self.manager._generate_markup({"text": "other", "callback": Mock()}, unit_id="other")
        self.assertTrue(await self.manager._unload_unit("test"))
        self.assertEqual(list(self.manager._custom_map), ["third"])

    async def test_unload_error_does_not_prevent_cleanup(self):
        callback = Mock(side_effect=ValueError("synthetic unload failure"))
        self.manager._units["test"] = {"on_unload": callback}
        self.assertTrue(await self.manager._unload_unit("test"))
        self.assertNotIn("test", self.manager._units)
        self.namespace["logger"].exception.assert_called_once()

    async def test_async_unload_callback_is_awaited_once(self):
        callback = AsyncMock()
        self.manager._units["test"] = {"on_unload": callback}
        self.assertTrue(await self.manager._unload_unit("test"))
        self.assertFalse(await self.manager._unload_unit("test"))
        callback.assert_awaited_once()

    async def test_reentrant_unload_does_not_call_callback_twice(self):
        results = []

        async def callback():
            results.append(await self.manager._unload_unit("test"))

        self.manager._units["test"] = {"on_unload": callback}
        self.assertTrue(await self.manager._unload_unit("test"))
        self.assertEqual(results, [False])

    async def test_missing_unit_does_not_remove_other_callbacks(self):
        self.manager._custom_map["other"] = {"handler": Mock()}
        self.assertFalse(await self.manager._unload_unit("missing"))
        self.assertIn("other", self.manager._custom_map)

    async def test_cleaner_removes_orphaned_owned_callbacks(self):
        self.manager._custom_map["orphan"] = {"unit_id": "missing"}
        await self.clean_once()
        self.assertEqual(self.manager._custom_map, {})

    async def test_owner_deadline_controls_callback_lifetime(self):
        self.manager._units["test"] = {"ttl": 120}
        self.manager._custom_map["owned"] = {"unit_id": "test", "ttl": 99}
        await self.clean_once()
        self.assertIn("owned", self.manager._custom_map)

    async def test_callback_can_access_the_unit_during_unload(self):
        callback = Mock(side_effect=lambda: self.assertIn("test", self.manager._units))
        self.manager._units["test"] = {"on_unload": callback}
        self.assertTrue(await self.manager._unload_unit("test"))
        callback.assert_called_once()

    async def test_cancellation_during_unload_still_releases_callbacks(self):
        self.manager._units["test"] = {
            "on_unload": AsyncMock(side_effect=asyncio.CancelledError())
        }
        self.manager._generate_markup({"text": "test", "callback": Mock()}, unit_id="test")
        with self.assertRaises(asyncio.CancelledError):
            await self.manager._unload_unit("test")
        self.assertEqual(self.manager._units, {})
        self.assertEqual(self.manager._custom_map, {})

    async def test_callback_security_metadata_is_preserved(self):
        button = {
            "text": "test", "callback": Mock(), "force_me": True,
            "always_allow": [1], "disable_security": False,
        }
        self.manager._generate_markup(button)
        entry = self.manager._custom_map["first"]
        self.assertTrue(entry["force_me"])
        self.assertEqual(entry["always_allow"], [1])
        self.assertFalse(entry["disable_security"])

    def test_unit_constructors_store_creation_deadlines(self):
        for path, name in (
            ("heroku/inline/form.py", "form"),
            ("heroku/inline/list.py", "list"),
            ("heroku/inline/gallery.py", "gallery"),
        ):
            method = next(
                node for node in ast.walk(ast.parse((ROOT / path).read_text()))
                if isinstance(node, ast.AsyncFunctionDef) and node.name == name
            )
            assignment = next(
                node for node in ast.walk(method)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "_units"
                    for target in node.targets
                )
            )
            deadline = next(
                value for key, value in zip(assignment.value.keys, assignment.value.values)
                if isinstance(key, ast.Constant) and key.value == "ttl"
            )
            for ttl, expected in ((None, 110), (False, 110), (5, 105)):
                with self.subTest(path=path, ttl=ttl):
                    result = eval(
                        compile(ast.Expression(deadline), path, "eval"),
                        {"self": self.manager, "time": self.clock, "ttl": ttl},
                    )
                    self.assertEqual(result, expected)


class CleanupCompatibilityTest(unittest.TestCase):
    def test_removed_commands_and_private_helpers_are_absent(self):
        candidates = {
            "heroku/modules/settings.py": {
                "togglecmdcmd", "togglemod", "_inline__choose__installation"
            },
            "heroku/modules/loader.py": {"_inline__load", "_format_result"},
            "heroku/modules/terminal.py": {"RawMessageEditor"},
            "heroku/modules/heroku_settings.py": {"_get_all_IDM"},
            "heroku/inline/gallery.py": {"_gallery_back"},
            "heroku/_internal.py": {"RedactingFormatter"},
            "heroku/progresslive.py": {"_keyboard_loop"},
        }
        for path, removed in candidates.items():
            names = {
                node.name for node in ast.walk(ast.parse((ROOT / path).read_text()))
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            }
            with self.subTest(path=path):
                self.assertFalse(names & removed)

    def test_public_compatibility_methods_are_kept(self):
        for path, name in (
            ("heroku/_local_storage.py", "RemoteStorage.preload"),
            ("heroku/tl_cache.py", "CustomTelegramClient.get_perms_cached"),
            ("heroku/inline/core.py", "InlineManager._restart_polling"),
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(load_definition(path, name, {}))

    def test_startup_display_still_renders_stage_progress(self):
        output = io.StringIO()
        display = load_definition(
            "heroku/progresslive.py", "StartupLiveDisplay",
            {
                "shutil": shutil,
                "threading": threading,
                "sys": SimpleNamespace(stdout=output),
            },
        )()
        display._term_size = lambda: SimpleNamespace(columns=100, lines=24)
        display.stage("modules", advance=True, stage="ready")
        self.assertIn("load modules", output.getvalue())
        self.assertEqual(display.completed_steps, 1)
        display.finalize()
        self.assertFalse(display._rendered)


if __name__ == "__main__":
    unittest.main()
