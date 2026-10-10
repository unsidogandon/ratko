"""StringLoader bytecode cache: warm loads bypass compilation, the
cache is invalidated by source changes and survives corruption."""

import hashlib
import importlib.machinery
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import heroku.types as types_module
from heroku.types import _PYC_TAG, _cached_compile, StringLoader

SOURCE = b"""
VALUE = 42

def get_value():
    return VALUE

class Calc:
    def double(self, x):
        return x * 2
"""

_counter = iter(range(100000))


def _exec_module(source, origin):
    name = f"heroku.tests.bytecode_{next(_counter)}"
    spec = importlib.machinery.ModuleSpec(
        name, StringLoader(source, origin), origin=origin
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class CachedCompileTest(unittest.TestCase):
    def setUp(self):
        # Isolate the cache directory per test
        self._saved_dir = types_module._STRINGLOADER_CACHE_DIR
        self.cache_dir = Path(tempfile.mkdtemp(prefix="pycache-test."))
        types_module._STRINGLOADER_CACHE_DIR = self.cache_dir
        self.addCleanup(self._restore)

    def _restore(self):
        types_module._STRINGLOADER_CACHE_DIR = self._saved_dir
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    def _cache_path(self, filename):
        key = hashlib.sha256(
            f"{_PYC_TAG}:{filename}".encode("utf-8")
        ).hexdigest()
        return self.cache_dir / f"{key}.pyc"

    def test_cold_and_warm_loads_behave_identically(self):
        first = _exec_module(SOURCE, "<test module one>")
        second = _exec_module(SOURCE, "<test module one>")
        self.assertEqual(first.get_value(), 42)
        self.assertEqual(second.get_value(), 42)
        self.assertEqual(first.Calc().double(2), 4)
        self.assertEqual(second.Calc().double(2), 4)
        # Warm load must produce an equal module, not the same objects
        self.assertEqual(
            first.get_value.__code__.co_code,
            second.get_value.__code__.co_code,
        )
        self.assertEqual(first.Calc.__name__, "Calc")
        self.assertIsNot(first.Calc, second.Calc)

    def test_warm_load_does_not_compile(self):
        code = _cached_compile(SOURCE, "<no compile>")
        self.assertTrue(self._cache_path("<no compile>").exists())
        with patch("builtins.compile", side_effect=AssertionError):
            warm = _cached_compile(SOURCE, "<no compile>")
        self.assertEqual(warm.co_code, code.co_code)
        self.assertEqual(warm.co_consts, code.co_consts)

    def test_source_change_invalidates(self):
        first = _cached_compile(b"one = 1\n", "<invalidate>")
        with patch("builtins.compile", wraps=compile) as compile_spy:
            second = _cached_compile(b"two = 2\n", "<invalidate>")
            self.assertEqual(compile_spy.call_count, 1)
        self.assertEqual(first.co_consts, (1, None))
        self.assertEqual(second.co_consts, (2, None))
        namespace = {}
        exec(second, namespace)
        self.assertEqual(namespace["two"], 2)
        # And the new source is now cached
        with patch("builtins.compile", side_effect=AssertionError):
            warm = _cached_compile(b"two = 2\n", "<invalidate>")
        self.assertEqual(warm.co_code, second.co_code)

    def test_corrupt_cache_falls_back_to_compile(self):
        path = self._cache_path("<corrupt>")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"garbage that is not marshal")
        code = _cached_compile(b"value = 7\n", "<corrupt>")
        namespace = {}
        exec(code, namespace)
        self.assertEqual(namespace["value"], 7)
        # The corrupted entry is replaced with a valid one
        with patch("builtins.compile", side_effect=AssertionError):
            warm = _cached_compile(b"value = 7\n", "<corrupt>")
        self.assertEqual(warm.co_code, code.co_code)

    def test_filename_is_part_of_the_key(self):
        code_a = _cached_compile(SOURCE, "<origin a>")
        code_b = _cached_compile(SOURCE, "<origin b>")
        self.assertEqual(code_a.co_filename, "<origin a>")
        self.assertEqual(code_b.co_filename, "<origin b>")
        # Both stay cached independently
        with patch("builtins.compile", side_effect=AssertionError):
            warm_a = _cached_compile(SOURCE, "<origin a>")
            warm_b = _cached_compile(SOURCE, "<origin b>")
        self.assertEqual(warm_a.co_code, code_a.co_code)
        self.assertEqual(warm_b.co_code, code_b.co_code)

    def test_empty_source(self):
        code = _cached_compile(b"", "<empty>")
        namespace = {}
        exec(code, namespace)
        self.assertEqual(
            {key for key in namespace if key != "__builtins__"}, set()
        )

    def test_non_ascii_source(self):
        source = "значение = '🔥'\n".encode("utf-8")
        code = _cached_compile(source, "<unicode>")
        namespace = {}
        exec(code, namespace)
        self.assertEqual(namespace["значение"], "🔥")


if __name__ == "__main__":
    unittest.main()
