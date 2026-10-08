"""Module, inline and terminal lifecycle tests without application startup."""

import ast
import asyncio
import codecs
import contextlib
import copy
import functools
import hashlib
import html
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import itertools
import os
import re
import shlex
import signal
import sys
import time
import traceback
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlparse

from test_runtime_performance import ROOT, load_definition


def bind_methods(cls, path, prefix, names, namespace, static=()):
    for name in names:
        method = load_definition(path, f"{prefix}.{name}", namespace)
        setattr(cls, name, staticmethod(method) if name in static else method)


class ModuleUidTest(unittest.TestCase):
    def setUp(self):
        self.uid = load_definition(
            "heroku/modules/loader.py", "LoaderMod._get_module_uid",
            {"ast": ast, "hashlib": hashlib},
        )

    def test_standard_loader_base_uses_class_name(self):
        self.assertEqual(self.uid("class ExampleMod(loader.Module): pass"), "ExampleMod")

    def test_direct_and_aliased_base_imports_are_supported(self):
        for source in (
            "class ExampleMod(Module): pass",
            "from package import Module as Base\nclass ExampleMod(Base): pass",
            "class ExampleMod(package.loader.Module): pass",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.uid(source), "ExampleMod")

    def test_unrelated_classes_do_not_hide_the_module(self):
        self.assertEqual(
            self.uid("class Helper(other.Base): pass\nclass ExampleMod(loader.Module): pass"),
            "ExampleMod",
        )

    def test_fallback_is_stable_for_indirect_bases(self):
        source = "class ExampleMod(SomeOtherBase): pass"
        self.assertEqual(self.uid(source), self.uid(source))
        self.assertTrue(self.uid(source).startswith("__extmod_"))
        self.assertNotEqual(self.uid(source), self.uid(source + "\nvalue = 1"))

    def test_syntax_errors_have_stable_names_for_failure_cleanup(self):
        self.assertEqual(self.uid("class broken("), self.uid("class broken("))


class RegistryNamespaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class Module:
            strings = {"name": "Example"}

            def internal_init(self):
                pass

            async def on_unload(self):
                pass

            async def on_dlmod(self):
                pass

            async def client_ready(self):
                pass

        self.namespace = {
            "asyncio": asyncio, "contextlib": contextlib, "copy": copy,
            "importlib": importlib, "inspect": inspect, "sys": sys, "os": os,
            "Path": Path, "Module": Module, "logger": Mock(),
            "utils": SimpleNamespace(iter_attrs=lambda obj: []),
            "InfiniteLoop": type("InfiniteLoop", (), {}),
            "VALID_PIP_PACKAGES": re.compile(r"#requires: (.+)"),
            "IMPORT_PIP_ALIASES": {}, "MODULES_LANGPACKS_PATH": Path("unused"),
        }
        for name in ("LoadError", "CoreOverwriteError", "SelfUnload", "SelfSuspend"):
            load_definition("heroku/types.py", name, self.namespace)
        Module.create_task = load_definition(
            "heroku/types.py", "Module.create_task", self.namespace,
        )
        stub = ModuleType("_ratko_lifecycle_stub")
        stub.Module = Module
        stub.ImportedMod = type("ImportedMod", (Module,), {})
        self.stub = stub
        self.module_patch = patch.dict(sys.modules, {stub.__name__: stub})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        registry_type = type("Registry", (), {})
        bind_methods(
            registry_type, "heroku/loader.py", "Modules",
            (
                "register_module", "complete_registration", "_forget_module_namespace",
                "_shutdown_module", "_finish_shutdown", "_finish_shutdown_handlers",
                "_wait_module_tasks", "_consume_shutdown_result", "send_ready_one",
                "_send_ready_one", "find_alias",
            ), self.namespace,
            static=("_forget_module_namespace", "_consume_shutdown_result"),
        )
        self.registry = registry_type()
        self.registry.modules = []
        self.registry.client = SimpleNamespace(tg_id=1)
        self.registry._db = {}
        self.registry._remove_core_protection = False
        self.registry.translator = SimpleNamespace(
            load_module_translations=AsyncMock(return_value={"hello": "translated"})
        )
        for name in (
            "unregister_raw_handlers", "unregister_bot_update_handlers",
            "unregister_commands", "unregister_watchers", "unregister_inline_stuff",
            "register_commands", "register_watchers", "register_raw_handlers",
            "register_bot_update_handlers",
        ):
            setattr(self.registry, name, Mock())
        self.StringLoader = load_definition(
            "heroku/types.py", "StringLoader", {"SourceLoader": importlib.abc.SourceLoader}
        )
        self.name = "_ratko_lifecycle_example"

    async def asyncTearDown(self):
        for module in self.registry.modules.copy():
            await self.registry._shutdown_module(module, "test teardown")

    async def register(self, source=None, *, origin="<string>"):
        source = source or (
            "from _ratko_lifecycle_stub import Module\n"
            "class ExampleMod(Module): pass\n"
        )
        spec = importlib.machinery.ModuleSpec(self.name, self.StringLoader(source, "<test>"))
        return await self.registry.register_module(spec, self.name, origin)

    async def test_imported_module_base_is_not_instantiated(self):
        module = await self.register()
        self.assertEqual(module.__class__.__name__, "ExampleMod")
        self.assertIs(module.__python_module__, sys.modules[self.name])

    async def test_local_subclass_is_preferred_to_an_imported_helper(self):
        module = await self.register(
            "from _ratko_lifecycle_stub import ImportedMod\n"
            "class ExampleMod(ImportedMod): pass\n"
        )
        self.assertEqual(module.__class__.__name__, "ExampleMod")

    async def test_legacy_reexported_modules_remain_supported(self):
        module = await self.register("from _ratko_lifecycle_stub import ImportedMod\n")
        self.assertIsInstance(module, self.stub.ImportedMod)

    async def test_legacy_register_factory_and_version_remain_supported(self):
        module = await self.register(
            "from _ratko_lifecycle_stub import Module\n"
            "__version__ = (1, 2, 3)\n"
            "def register(name): return Module()\n"
        )
        self.assertIsInstance(module, self.stub.Module)
        self.assertEqual(module.__version__, (1, 2, 3))

    async def test_import_failure_does_not_leave_a_namespace(self):
        with self.assertRaises(RuntimeError):
            await self.register("raise RuntimeError('synthetic import failure')")
        self.assertNotIn(self.name, sys.modules)
        self.assertEqual(self.registry.modules, [])

    async def test_constructor_failure_does_not_leave_a_namespace(self):
        with self.assertRaises(ValueError):
            await self.register(
                "from _ratko_lifecycle_stub import Module\n"
                "class ExampleMod(Module):\n"
                "    def __init__(self): raise ValueError('synthetic constructor failure')"
            )
        self.assertNotIn(self.name, sys.modules)

    async def test_source_decode_failure_is_also_cleaned_up(self):
        spec = importlib.machinery.ModuleSpec(self.name, self.StringLoader(b"\xff", "<test>"))
        with self.assertRaises(UnicodeDecodeError):
            await self.registry.register_module(spec, self.name)
        self.assertNotIn(self.name, sys.modules)

    async def test_failed_replacement_restores_the_live_namespace(self):
        module = await self.register()
        previous = sys.modules[self.name]
        with self.assertRaises(RuntimeError):
            await self.register("raise RuntimeError('synthetic replacement failure')")
        self.assertIs(sys.modules[self.name], previous)
        self.assertEqual(self.registry.modules, [module])

    async def test_core_rejection_does_not_unload_the_live_core_module(self):
        module = await self.register(origin="<core>")
        previous = sys.modules[self.name]
        with self.assertRaises(self.namespace["CoreOverwriteError"]):
            await self.register()
        self.assertIs(sys.modules[self.name], previous)
        self.assertFalse(getattr(module, "_unloading", False))

    async def test_failure_after_update_does_not_resurrect_the_unloaded_namespace(self):
        old = await self.register()
        complete = self.registry.complete_registration

        async def fail(instance):
            await complete(instance)
            raise ValueError("synthetic failure after registration")

        self.registry.complete_registration = fail
        with self.assertRaises(ValueError):
            await self.register()
        self.assertEqual(self.registry.modules, [])
        self.assertNotIn(self.name, sys.modules)
        self.assertTrue(old._unloading)

    async def test_repeated_updates_keep_one_namespace_and_instance(self):
        for _ in range(40):
            module = await self.register()
        self.assertEqual(self.registry.modules, [module])
        self.assertEqual([name for name in sys.modules if name == self.name], [self.name])
        await self.registry._shutdown_module(module, "test unload")
        self.assertNotIn(self.name, sys.modules)

    async def test_unloading_an_old_owner_does_not_remove_its_replacement(self):
        old = await self.register()
        old_namespace = old.__python_module__
        new_namespace = ModuleType(self.name)
        sys.modules[self.name] = new_namespace
        self.registry._forget_module_namespace(old)
        self.assertIs(sys.modules[self.name], new_namespace)
        self.assertIsNot(old_namespace, new_namespace)
        self.assertFalse(hasattr(old, "__python_module__"))

    async def test_namespace_is_released_even_if_shutdown_itself_fails(self):
        module = await self.register()
        self.registry._finish_shutdown_handlers = AsyncMock(side_effect=ValueError("failure"))
        with self.assertRaises(ValueError):
            await self.registry._finish_shutdown(module, "test", asyncio.current_task())
        self.assertNotIn(self.name, sys.modules)
        self.registry.modules.clear()

    async def test_ready_cancellation_cleans_the_instance_and_namespace(self):
        module = await self.register()
        entered = asyncio.Event()

        async def ready():
            entered.set()
            await asyncio.Event().wait()

        module.client_ready = ready
        task = asyncio.create_task(self.registry.send_ready_one(module))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn(self.name, sys.modules)
        self.assertEqual(self.registry.modules, [])

    async def test_failure_after_client_ready_is_cleaned_up(self):
        module = await self.register()
        self.registry.register_commands.side_effect = ValueError("synthetic command failure")
        with self.assertRaises(ValueError):
            await self.registry.send_ready_one(module)
        self.assertNotIn(self.name, sys.modules)
        self.assertEqual(self.registry.modules, [])

    async def test_suspended_modules_keep_their_namespace(self):
        module = await self.register()

        async def ready():
            raise self.namespace["SelfSuspend"]()

        module.client_ready = ready
        with self.assertRaises(self.namespace["SelfSuspend"]):
            await self.registry.send_ready_one(module, no_self_unload=True)
        self.assertIs(sys.modules[self.name], module.__python_module__)
        self.assertEqual(self.registry.modules, [module])

    async def test_manual_self_unload_is_left_to_the_installer(self):
        module = await self.register()

        async def ready():
            raise self.namespace["SelfUnload"]()

        module.client_ready = ready
        with self.assertRaises(self.namespace["SelfUnload"]):
            await self.registry.send_ready_one(module, no_self_unload=True)
        self.assertEqual(self.registry.modules, [module])
        self.assertIn(self.name, sys.modules)

    async def test_startup_self_unload_releases_instance_and_namespace(self):
        module = await self.register()

        async def ready():
            raise self.namespace["SelfUnload"]()

        module.client_ready = ready
        await self.registry.send_ready_one(module)
        self.assertEqual(self.registry.modules, [])
        self.assertNotIn(self.name, sys.modules)

    async def test_packurl_is_fetched_once_during_manual_loading(self):
        module = await self.register()
        module.__source__ += "\n#packurl: https://example.invalid/pack.yml"
        module.strings = SimpleNamespace()
        await self.registry.send_ready_one(module, from_dlmod=True)
        fetch = self.registry.translator.load_module_translations
        fetch.assert_awaited_once()
        self.assertFalse(fetch.call_args.kwargs["cache_only"])
        self.assertEqual(module.strings.external_strings, {"hello": "translated"})

    async def test_startup_uses_the_cache_and_one_managed_refresh(self):
        module = await self.register()
        module.__source__ += "\n#packurl: https://example.invalid/pack.yml"
        module.strings = SimpleNamespace()
        await self.registry.send_ready_one(module)
        await asyncio.gather(*module._managed_tasks)
        fetch = self.registry.translator.load_module_translations
        self.assertEqual(fetch.await_count, 2)
        self.assertTrue(fetch.call_args_list[0].kwargs["cache_only"])
        self.assertNotIn("cache_only", fetch.call_args_list[1].kwargs)

    def make_external_loader(self):
        self.namespace.update({
            "ast": ast, "hashlib": hashlib, "re": re,
            "ModuleSpec": importlib.machinery.ModuleSpec,
            "loader": SimpleNamespace(
                StringLoader=self.StringLoader, LoadError=self.namespace["LoadError"],
                SelfUnload=self.namespace["SelfUnload"],
                SelfSuspend=self.namespace["SelfSuspend"],
            ),
            "Message": type("Message", (), {}),
            "ScamDetectionError": type("ScamDetectionError", (Exception,), {}),
        })
        external_type = type("ExternalLoader", (), {})
        bind_methods(
            external_type, "heroku/modules/loader.py", "LoaderMod",
            ("_get_module_uid", "load_module"), self.namespace,
            static=("_get_module_uid",),
        )
        external = external_type()
        external.allmodules = self.registry
        external.config = {"MODULES_REPO": "https://example.invalid"}
        external.strings = {"load_failed": "failed"}
        return external

    async def test_config_failure_cleans_the_external_module(self):
        external = self.make_external_loader()
        self.registry.send_config_one = Mock(side_effect=ValueError("synthetic config failure"))
        result = await external.load_module(
            "from _ratko_lifecycle_stub import Module\nclass ExampleMod(Module): pass",
            None, name=self.name, save_fs=False, did_requires=True, did_packages=True,
        )
        self.assertFalse(result)
        self.assertNotIn(f"heroku.modules.{self.name}", sys.modules)
        self.assertEqual(self.registry.modules, [])

    async def test_cancelled_external_install_joins_the_approval_poller(self):
        external = self.make_external_loader()
        entered = asyncio.Event()

        async def ready():
            entered.set()
            await asyncio.Event().wait()

        def config(module):
            module.client_ready = ready

        self.registry.send_config_one = config
        before = asyncio.all_tasks()
        task = asyncio.create_task(external.load_module(
            "from _ratko_lifecycle_stub import Module\nclass ExampleMod(Module): pass",
            None, name=self.name, save_fs=False, did_requires=True, did_packages=True,
        ))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.registry.modules, [])
        self.assertNotIn(f"heroku.modules.{self.name}", sys.modules)
        self.assertEqual(asyncio.all_tasks(), before)

    def test_plural_only_command_aliases_work(self):
        self.registry.commands = {"example": SimpleNamespace(aliases=["first", "SECOND"])}
        self.registry._core_commands = set()
        self.registry.aliases = {}
        self.assertEqual(self.registry.find_alias("second"), "example")
        self.assertIsNone(self.registry.find_alias("missing"))

    def test_single_and_legacy_aliases_and_core_protection_are_preserved(self):
        self.registry.commands = {"example": SimpleNamespace(alias="single")}
        self.registry._core_commands = {"protected"}
        self.registry.aliases = {"legacy": "example arguments"}
        self.assertEqual(self.registry.find_alias("SINGLE"), "example")
        self.assertIsNone(self.registry.find_alias("legacy"))
        self.assertEqual(self.registry.find_alias("legacy", True), "example arguments")
        self.registry.commands["example"].aliases = ["protected"]
        self.assertIsNone(self.registry.find_alias("protected"))


class InlineWaitTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.namespace = {
            "asyncio": asyncio, "inspect": inspect, "logger": Mock(),
            "Message": type("Message", (), {}),
            "utils": SimpleNamespace(get_chat_id=lambda message: message.chat_id),
        }
        manager_type = type("Manager", (), {})
        bind_methods(
            manager_type, "heroku/inline/core.py", "InlineManager",
            ("_invoke_unit", "_wait_for_unit"), self.namespace,
        )
        bind_methods(
            manager_type, "heroku/inline/utils.py", "Utils", ("_unload_unit",),
            self.namespace,
        )
        self.manager = manager_type()
        self.manager._units = {"unit": {"future": asyncio.Event()}}
        self.manager._custom_map = {"button": {"unit_id": "unit"}}
        self.manager._error_events = {}
        self.manager.bot_username = "test_bot"
        self.click = AsyncMock(return_value="sent")
        self.manager._client = SimpleNamespace(
            inline_query=AsyncMock(return_value=[SimpleNamespace(click=self.click)])
        )

    def shorten_timeouts(self):
        async def wait_for(awaitable, timeout):
            return await asyncio.wait_for(awaitable, min(timeout, 0.02))

        self.namespace["asyncio"] = SimpleNamespace(
            Event=asyncio.Event, ensure_future=asyncio.ensure_future,
            wait=asyncio.wait, gather=asyncio.gather, wait_for=wait_for,
            FIRST_COMPLETED=asyncio.FIRST_COMPLETED,
            TimeoutError=asyncio.TimeoutError, CancelledError=asyncio.CancelledError,
        )

    async def test_chosen_result_releases_the_wait_event(self):
        unit = self.manager._units["unit"]
        unit["inline_message_id"] = "chosen"
        unit["future"].set()
        self.assertTrue(await self.manager._wait_for_unit("unit"))
        self.assertNotIn("future", unit)
        self.assertIn("button", self.manager._custom_map)

    async def test_timeout_releases_unit_callbacks_and_calls_on_unload(self):
        callback = AsyncMock()
        self.manager._units["unit"]["on_unload"] = callback
        self.assertFalse(await self.manager._wait_for_unit("unit", timeout=0.001))
        self.assertEqual(self.manager._units, {})
        self.assertEqual(self.manager._custom_map, {})
        callback.assert_awaited_once()

    async def test_cancellation_releases_the_unit_and_propagates(self):
        task = asyncio.create_task(self.manager._wait_for_unit("unit"))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.manager._units, {})
        self.assertEqual(self.manager._custom_map, {})

    async def test_unloading_a_pending_unit_wakes_its_waiter(self):
        task = asyncio.create_task(self.manager._wait_for_unit("unit"))
        await asyncio.sleep(0)
        await self.manager._unload_unit("unit")
        self.assertFalse(await asyncio.wait_for(task, timeout=0.5))

    async def test_missing_unit_or_chosen_message_returns_false(self):
        self.assertFalse(await self.manager._wait_for_unit("missing"))
        self.manager._units["unit"]["future"].set()
        self.assertFalse(await self.manager._wait_for_unit("unit"))
        self.assertEqual(self.manager._units, {})

    async def test_stalled_unload_callback_is_cancelled_and_cleanup_still_runs(self):
        self.shorten_timeouts()
        cancelled = asyncio.Event()

        async def callback():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.manager._units["unit"]["on_unload"] = callback
        self.assertTrue(await self.manager._unload_unit("unit"))
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.manager._units, {})
        self.assertEqual(self.manager._custom_map, {})

    async def test_invoke_preserves_chat_and_reply_arguments(self):
        message = self.namespace["Message"]()
        message.chat_id = 123
        message.reply_to_msg_id = 456
        self.assertEqual(await self.manager._invoke_unit("unit", message), "sent")
        self.click.assert_awaited_once_with(123, reply_to=456)
        self.assertEqual(self.manager._error_events, {})

    async def test_invoke_accepts_explicit_reply_and_integer_chat(self):
        await self.manager._invoke_unit("unit", 123, reply_to=789)
        self.click.assert_awaited_once_with(123, reply_to=789)

    async def test_failed_query_releases_error_state(self):
        self.manager._client.inline_query.side_effect = ValueError("synthetic query failure")
        with self.assertRaisesRegex(Exception, "No query results"):
            await self.manager._invoke_unit("unit", 123)
        self.assertEqual(self.manager._error_events, {})

    async def test_bot_error_is_preserved_and_query_task_is_joined(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def query(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.manager._client.inline_query = query
        task = asyncio.create_task(self.manager._invoke_unit("unit", 123))
        await entered.wait()
        event = self.manager._error_events["unit"]
        self.manager._error_events["unit"] = ValueError("synthetic bot error")
        event.set()
        with self.assertRaisesRegex(ValueError, "synthetic bot error"):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.manager._error_events, {})

    async def test_cancelled_invoke_joins_query_and_poller_tasks(self):
        before = asyncio.all_tasks()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def query(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.manager._client.inline_query = query
        task = asyncio.create_task(self.manager._invoke_unit("unit", 123))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.manager._error_events, {})
        self.assertEqual(asyncio.all_tasks(), before)

    async def test_query_and_click_have_timeouts(self):
        self.shorten_timeouts()
        self.manager._client.inline_query = AsyncMock(
            side_effect=lambda *args: None
        )

        async def never(*args, **kwargs):
            await asyncio.Event().wait()

        self.manager._client.inline_query.side_effect = never
        with self.assertRaisesRegex(Exception, "No query results"):
            await self.manager._invoke_unit("unit", 123)
        self.manager._client.inline_query.side_effect = None
        self.manager._client.inline_query.return_value = [SimpleNamespace(click=never)]
        with self.assertRaises(asyncio.TimeoutError):
            await self.manager._invoke_unit("unit", 123)
        self.assertEqual(self.manager._error_events, {})


class InlineConstructorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        InlineWaitTest.setUp(self)
        self.manager._units.clear()
        self.manager._custom_map.clear()
        ids = itertools.count()
        self.namespace.update({
            "contextlib": contextlib, "copy": copy, "functools": functools,
            "os": os, "time": time, "traceback": traceback, "urlparse": urlparse,
            "Event": asyncio.Event, "Placeholder": type("Placeholder", (), {}),
            "ChatSendInlineForbiddenError": type("InlineForbidden", (Exception,), {}),
            "main": SimpleNamespace(__name__="heroku.main"),
            "InlineMessage": lambda *args, **kwargs: SimpleNamespace(
                unit_id=kwargs["unit_id"] if kwargs else args[1]
            ),
            "utils": SimpleNamespace(
                rand=lambda length: f"id{next(ids)}",
                get_chat_id=lambda message: message.chat_id,
                get_topic=lambda message: None, escape_html=html.escape,
            ),
        })
        load_definition("heroku/inline/gallery.py", "ListGalleryHelper", self.namespace)
        for path, prefix, names in (
            ("heroku/inline/utils.py", "Utils", ("_normalize_markup", "_validate_markup")),
            ("heroku/inline/form.py", "Form", ("form",)),
            ("heroku/inline/list.py", "List", ("list",)),
            ("heroku/inline/gallery.py", "Gallery", ("gallery",)),
        ):
            bind_methods(type(self.manager), path, prefix, names, self.namespace)
        self.namespace.pop("list")  # Restore builtin list after extracting List.list.
        self.manager._markup_ttl = 86400
        self.manager._needs_premium_emoji_pre_edit = lambda text: False
        self.manager.sanitise_text = lambda text: text
        self.manager._find_caller_sec_map = lambda: 7
        self.manager._list_page = self.manager._gallery_page = AsyncMock()
        self.manager.translator = SimpleNamespace(getkey=lambda key: key)
        self.manager._db = SimpleNamespace(get=lambda *args: False)
        self.manager._client.send_message = AsyncMock()
        self.callback = AsyncMock()

    def construct(self, kind):
        kwargs = {"message": 123, "on_unload": self.callback, "force_me": True, "always_allow": [1]}
        if kind == "form":
            kwargs.update(text="test", reply_markup=[[{"text": "button", "callback": Mock()}]])
        elif kind == "list":
            kwargs["strings"] = ["first", "second"]
        else:
            kwargs["next_handler"] = ["https://example.invalid/image.jpg"]
        return getattr(self.manager, kind)(**kwargs)

    async def test_all_constructors_release_units_after_chosen_result_timeout(self):
        InlineWaitTest.shorten_timeouts(self)
        self.manager._invoke_unit = AsyncMock(return_value=SimpleNamespace(chat_id=1, id=2))
        for kind in ("form", "list", "gallery"):
            with self.subTest(kind=kind):
                self.assertFalse(await self.construct(kind))
                self.assertEqual(self.manager._units, {})
                self.assertEqual(self.manager._custom_map, {})
        self.assertEqual(self.callback.await_count, 3)

    async def test_all_constructors_release_units_after_invocation_cancellation(self):
        self.manager._invoke_unit = AsyncMock(side_effect=asyncio.CancelledError())
        for kind in ("form", "list", "gallery"):
            with self.subTest(kind=kind), self.assertRaises(asyncio.CancelledError):
                await self.construct(kind)
            self.assertEqual(self.manager._units, {})
            self.assertEqual(self.manager._custom_map, {})
        self.assertEqual(self.callback.await_count, 3)

    async def test_forbidden_inline_cleans_up_even_when_error_delivery_fails(self):
        self.manager._invoke_unit = AsyncMock(side_effect=self.namespace["ChatSendInlineForbiddenError"]())
        self.manager._client.send_message.side_effect = ValueError("synthetic response failure")
        for kind in ("form", "list", "gallery"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "response failure"):
                await self.construct(kind)
            self.assertEqual(self.manager._units, {})
            self.assertEqual(self.manager._custom_map, {})

    async def test_successful_constructors_keep_security_and_owned_callbacks(self):
        async def invoke(unit_id, message, **kwargs):
            unit = self.manager._units[unit_id]
            unit["inline_message_id"] = "chosen"
            unit["future"].set()
            return SimpleNamespace(chat_id=1, id=2)

        self.manager._invoke_unit = invoke
        for kind in ("form", "list", "gallery"):
            with self.subTest(kind=kind):
                result = await self.construct(kind)
                unit = self.manager._units[result.unit_id]
                self.assertEqual((unit["chat"], unit["message_id"]), (1, 2))
                self.assertEqual(unit["inline_message_id"], "chosen")
                self.assertEqual(unit["always_allow"], [1])
                self.assertTrue(unit["force_me"])
                self.assertEqual(unit["perms_map"], 7)
                self.assertNotIn("future", unit)
                for callback in self.manager._custom_map.values():
                    self.assertEqual(callback["unit_id"], result.unit_id)
                await self.manager._unload_unit(result.unit_id)
                self.assertEqual(self.manager._custom_map, {})


class TerminalLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = SimpleNamespace(monotonic=Mock(return_value=100), time=time.time)
        self.namespace = {
            "__name__": "_ratko_terminal_tests", "asyncio": asyncio, "codecs": codecs,
            "contextlib": contextlib, "functools": functools, "inspect": inspect,
            "os": os, "re": re, "shlex": shlex, "signal": signal,
            "time": self.clock, "logger": Mock(), "STREAM_BUFFER_LIMIT": 64 * 1024,
            "utils": SimpleNamespace(
                rand=Mock(return_value="password-token"), escape_html=html.escape,
                get_chat_id=lambda message: message.chat_id,
                ensure_child_watcher=Mock(), get_base_dir=lambda: str(ROOT),
                answer=AsyncMock(),
            ),
            "loader": SimpleNamespace(
                Module=type("Module", (), {}),
                ModuleConfig=lambda *args: {"FLOOD_WAIT_PROTECT": 0, "command_protect": True},
                ConfigValue=lambda *args, **kwargs: None,
                validators=SimpleNamespace(Integer=Mock(), Boolean=Mock()),
            ),
        }
        for name in (
            "hash_msg", "sudo_stdin_command", "read_stream", "MessageEditor",
            "SudoMessageEditor", "InlineMessageEditor", "TerminalMod",
        ):
            load_definition("heroku/modules/terminal.py", name, self.namespace)
        self.terminal = self.namespace["TerminalMod"]()
        manager_type = type("InlineManager", (), {})
        bind_methods(
            manager_type, "heroku/inline/utils.py", "Utils", ("_unload_unit",),
            self.namespace,
        )
        self.terminal.inline = manager_type()
        self.terminal.inline._units = {}
        self.terminal.inline._custom_map = {}
        self.terminal.inline._markup_ttl = 86400
        self.terminal.strings = {
            "running": "{}", "finished": "{}", "stdout": " stdout: ",
            "stderr": " stderr: ", "end": " end", "time_exec": "{}",
            "sudo_password_retry": "retry", "sudo_password_required": "required",
            "exec_error": "error: {}", "exec_running": "running",
        }

    def editor(self):
        return self.namespace["InlineMessageEditor"](
            SimpleNamespace(edit=AsyncMock()), "example", self.terminal.strings,
            self.terminal.config,
        )

    async def test_output_keeps_only_a_bounded_tail(self):
        stream = asyncio.StreamReader()
        stream.feed_data(b"x" * 200000 + b"end")
        stream.feed_eof()
        snapshots = []

        async def update(data, **kwargs):
            snapshots.append((data, kwargs))

        await self.namespace["read_stream"](update, stream, 0, report_offset=True)
        output, metadata = snapshots[-1]
        self.assertEqual(len(output), 64 * 1024)
        self.assertTrue(output.endswith("end"))
        self.assertEqual(metadata["offset"] + len(output), 200003)

    async def test_utf8_characters_split_between_reads_are_preserved(self):
        stream = SimpleNamespace(read=AsyncMock(side_effect=[b"a\xd0", b"\xb1\xf0\x9f", b"\x98\x80", b""]))
        update = AsyncMock()
        await self.namespace["read_stream"](update, stream, 0)
        update.assert_awaited_once_with("aб😀")

    async def test_truncated_utf8_at_eof_is_reported(self):
        stream = SimpleNamespace(read=AsyncMock(side_effect=[b"a\xd0", b""]))
        update = AsyncMock()
        await self.namespace["read_stream"](update, stream, 0)
        update.assert_awaited_once_with("a�")

    def test_pending_commands_have_a_ttl_and_count_limit(self):
        self.terminal.INLINE_PENDING_LIMIT = 2
        for uid in ("old", "second", "third"):
            self.terminal._remember_inline_command(uid, "example")
        self.assertEqual(list(self.terminal._inline_pending), ["second", "third"])
        self.assertNotIn("old", self.terminal._inline_pending_deadlines)
        self.clock.monotonic.return_value = 401
        self.terminal._prune_inline_pending()
        self.assertEqual(self.terminal._inline_pending, {})
        self.assertEqual(self.terminal._inline_pending_deadlines, {})

    async def test_unit_unload_releases_the_editor_and_output(self):
        editor = self.editor()
        editor.stdout = editor.stderr = "retained output"
        self.terminal._register_inline_session("unit", "inline-id")
        self.terminal._track_inline_session("unit", editor)
        self.assertIn("ttl", self.terminal.inline._units["unit"])
        await self.terminal.inline._unload_unit("unit")
        self.assertEqual(self.terminal._inline_sessions, {})
        self.assertIsNone(editor.form)
        self.assertEqual(editor.stdout + editor.stderr, "")

    async def test_idle_session_limit_preserves_active_and_recent_sessions(self):
        self.terminal.INLINE_IDLE_LIMIT = 1
        for uid, rc, age in (("old", 0, 1), ("recent", 0, 2), ("active", None, 0)):
            editor = self.editor()
            editor.rc, editor.last_activity = rc, age
            self.terminal._register_inline_session(uid, uid)
            self.terminal._track_inline_session(uid, editor)
        await self.terminal._cleanup_terminal_state()
        self.assertEqual(set(self.terminal._inline_sessions), {"recent", "active"})
        self.assertNotIn("old", self.terminal.inline._units)

    async def test_orphaned_sessions_are_released(self):
        editor = self.editor()
        self.terminal._track_inline_session("orphan", editor)
        await self.terminal._cleanup_terminal_state()
        self.assertEqual(self.terminal._inline_sessions, {})
        self.assertIsNone(editor.form)

    def test_password_prompt_position_uses_the_absolute_stream_offset(self):
        editor = self.editor()
        editor.process = SimpleNamespace(returncode=None)
        editor.stderr = "[heroku-sudo] password:"
        editor.observe_password_prompt(offset=100000)
        self.assertEqual(editor._auth_notice, "required")
        editor.waiting_password = False
        editor.observe_password_prompt(offset=100000)
        self.assertFalse(editor.waiting_password)
        editor.observe_password_prompt(offset=100100)
        self.assertTrue(editor.waiting_password)
        self.assertEqual(editor._auth_notice, "retry")

    async def test_running_session_cannot_be_started_twice(self):
        editor = self.editor()
        self.terminal._inline_sessions["unit"] = editor
        self.terminal.create_task = Mock()
        await self.terminal.inline__continue_input(None, "more", "unit")
        self.terminal.create_task.assert_not_called()
        editor.rc = 0
        editor.process = SimpleNamespace(returncode=0)
        await self.terminal.inline__continue_input(None, "more", "unit")
        self.terminal.create_task.assert_not_called()

    async def test_completed_session_can_still_be_continued(self):
        editor = self.editor()
        editor.rc = 0
        self.terminal._inline_sessions["unit"] = editor
        coroutines = []
        self.terminal.create_task = lambda coroutine: coroutines.append(coroutine)
        await self.terminal.inline__continue_input(None, "more", "unit")
        self.assertEqual(editor.command, "example more")
        self.assertIsNone(editor.rc)
        self.assertEqual(len(coroutines), 1)
        coroutines[0].close()

    async def test_failed_continuation_does_not_leave_a_stranded_session(self):
        editor = self.editor()
        editor.rc = 0
        editor.form.edit.side_effect = ValueError("synthetic edit failure")
        self.terminal._register_inline_session("unit", "inline-id")
        self.terminal._track_inline_session("unit", editor)
        with self.assertRaisesRegex(ValueError, "synthetic edit failure"):
            await self.terminal.inline__continue_input(None, "more", "unit")
        self.assertEqual(self.terminal._inline_sessions, {})
        self.assertEqual(self.terminal.inline._units, {})

    async def test_stop_process_escalates_from_term_to_kill(self):
        process = SimpleNamespace(pid=123, returncode=None, wait=AsyncMock())
        process.wait.side_effect = [asyncio.TimeoutError(), -9]
        with patch.object(os, "killpg") as kill:
            await self.terminal._stop_process(process)
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    async def test_stop_group_also_stops_children_after_the_shell_exits(self):
        process = SimpleNamespace(pid=123, returncode=0, wait=AsyncMock(return_value=0))
        with patch.object(os, "killpg") as kill:
            await self.terminal._stop_process(process, stop_group=True)
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    async def test_finished_process_is_not_signalled_during_normal_cleanup(self):
        process = SimpleNamespace(pid=123, returncode=0)
        with patch.object(os, "killpg") as kill:
            await self.terminal._stop_process(process)
        kill.assert_not_called()

    async def test_reader_failure_cancels_and_joins_the_other_stream(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def read(callback, stream, delay, **kwargs):
            if stream == "stdout":
                await entered.wait()
                raise ValueError("synthetic reader failure")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.namespace["read_stream"] = read
        with self.assertRaisesRegex(ValueError, "synthetic reader failure"):
            await self.terminal._read_process_output(
                SimpleNamespace(stdout="stdout", stderr="stderr"), self.editor()
            )
        self.assertTrue(cancelled.is_set())

    async def test_custom_editors_without_offset_arguments_still_work(self):
        stderr = []

        async def update_stderr(output):
            stderr.append(output)

        process = SimpleNamespace(stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader())
        process.stdout.feed_eof()
        process.stderr.feed_data(b"custom error output")
        process.stderr.feed_eof()
        editor = SimpleNamespace(update_stdout=AsyncMock(), update_stderr=update_stderr)
        await self.terminal._read_process_output(process, editor)
        self.assertEqual(stderr, ["custom error output"])

    async def test_cancelled_command_releases_active_process_and_editor(self):
        process = SimpleNamespace(pid=123, returncode=None)
        editor = self.editor()
        entered = asyncio.Event()

        async def read(*args):
            entered.set()
            await asyncio.Event().wait()

        self.terminal._read_process_output = read
        self.terminal._stop_process = AsyncMock()
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(
                self.terminal.run_command(SimpleNamespace(chat_id=1, id=2), "example", editor)
            )
            await entered.wait()
            self.assertEqual(self.terminal.activecmds, {"1/2": process})
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.terminal.activecmds, {})
        self.assertIsNone(editor.process)
        self.terminal._stop_process.assert_awaited_once_with(process, stop_group=True)

    async def test_cancelled_inline_command_releases_process(self):
        process = SimpleNamespace(pid=123, returncode=-15)
        editor = self.editor()
        self.terminal._read_process_output = AsyncMock(side_effect=asyncio.CancelledError())
        self.terminal._stop_process = AsyncMock()
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.CancelledError):
                await self.terminal._run_inline("example", editor)
        self.assertIsNone(editor.process)
        self.assertEqual(editor.rc, -15)
        self.terminal._stop_process.assert_awaited_once_with(process, stop_group=True)

    async def test_cleanup_error_still_releases_editor_process_reference(self):
        process = SimpleNamespace(pid=123, returncode=None)
        editor = self.editor()
        self.terminal._read_process_output = AsyncMock(side_effect=asyncio.CancelledError())
        self.terminal._stop_process = AsyncMock(side_effect=ValueError("synthetic stop failure"))
        with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaisesRegex(ValueError, "synthetic stop failure"):
                await self.terminal._run_inline("example", editor)
        self.assertIsNone(editor.process)
        self.assertEqual(editor.rc, 1)

    async def test_module_unload_stops_commands_and_releases_all_session_state(self):
        editor = self.editor()
        editor.process = Mock(returncode=None)
        self.terminal._register_inline_session("unit", "inline-id")
        self.terminal._track_inline_session("unit", editor)
        self.terminal._remember_inline_command("pending", "example")
        self.terminal.activecmds["command"] = editor.process
        self.terminal._stop_process = AsyncMock()
        await self.terminal.on_unload()
        self.terminal._stop_process.assert_awaited_once()
        self.assertEqual(self.terminal.activecmds, {})
        self.assertEqual(self.terminal._inline_pending, {})
        self.assertEqual(self.terminal._inline_pending_deadlines, {})
        self.assertEqual(self.terminal._inline_sessions, {})
        self.assertEqual(self.terminal.inline._units, {})

    async def test_real_subprocess_is_reaped_when_stopped(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)", start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await self.terminal._stop_process(process)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


if __name__ == "__main__":
    unittest.main()
