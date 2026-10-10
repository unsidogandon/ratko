"""Lazy language pack loading and the persistent parsed-pack cache."""

import asyncio
import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace

import heroku.translations as translations
from heroku.translations import (
    BaseTranslator,
    Translator,
    get_language_pack_path,
)


def make_translator(lang=False):
    db = SimpleNamespace(get=lambda *args, **kwargs: lang)
    return Translator(client=None, db=db)


class ParsedPackCacheTest(unittest.TestCase):
    def test_parse_pack_json_suffix(self):
        parsed = BaseTranslator._parse_pack('{"a": {"b": "c"}}', ".json")
        self.assertEqual(parsed, {"a": {"b": "c"}})

    def test_parse_pack_rejects_non_mapping(self):
        with self.assertRaises(ValueError):
            BaseTranslator._parse_pack("- a\n- b\n", ".yml")

    def test_flatten_pack_prefixing(self):
        parsed = {"heroku.mod": {"name": "ignored", "key": "value"}}
        self.assertEqual(
            BaseTranslator._flatten_pack(parsed, "heroku."),
            {"heroku.heroku.mod.key": "value"},
        )

    def test_flatten_pack_nested_language_form(self):
        parsed = {"ru": {"$mod": {"key": "значение"}}}
        self.assertEqual(
            BaseTranslator._flatten_pack(parsed, "heroku."),
            {"ru": {"mod.key": "значение"}},
        )

    def test_disk_cache_roundtrip(self):
        translator = make_translator()
        pack = get_language_pack_path("en")
        data = translator._get_pack_content(pack)
        self.assertIsInstance(data, dict)
        self.assertTrue(data)

        cache_file = (
            translations.CORE_LANGPACKS_CACHE_PATH
            / f"{hashlib.sha1(str(pack).encode('utf-8')).hexdigest()}.json"
        )
        self.assertTrue(cache_file.exists())

        # Drop the in-memory memo: the next call must be served from disk
        translations._parsed_pack_memo.clear()

        def fail_parse(content, suffix):
            raise AssertionError("pack must be served from the disk cache")

        saved = BaseTranslator._parse_pack
        BaseTranslator._parse_pack = staticmethod(fail_parse)
        try:
            data2 = translator._get_pack_content(pack)
        finally:
            BaseTranslator._parse_pack = saved

        self.assertEqual(data, data2)

    def test_invalidation_on_pack_change(self):
        translator = make_translator()
        pack = get_language_pack_path("en")
        first = translator._get_pack_content(pack)

        # Corrupt the memo with a wrong signature: the entry must be
        # ignored and the pack re-read
        translations._parsed_pack_memo[str(pack)] = ((0, 0), {"bogus": True})
        second = translator._get_pack_content(pack)
        self.assertEqual(first, second)
        self.assertNotEqual(
            translations._parsed_pack_memo[str(pack)][1], {"bogus": True}
        )


class TranslatorLazyLanguagesTest(unittest.TestCase):
    def test_init_loads_only_en_without_user_language(self):
        translator = make_translator()
        loaded = []
        saved = Translator._get_pack_content

        def spy(self, pack, prefix="heroku.modules."):
            loaded.append(Path(pack).name)
            return saved(self, pack, prefix)

        Translator._get_pack_content = spy
        try:
            asyncio.run(translator.init())
        finally:
            Translator._get_pack_content = saved

        self.assertEqual(loaded, ["en.yml"])
        self.assertEqual(list(translator.raw_data.keys()), ["en"])

    def test_unknown_language_yields_empty_mapping(self):
        translator = make_translator()
        asyncio.run(translator.init())
        self.assertEqual(translator.raw_data["klingon"], {})

    def test_lazy_language_load_and_memoization(self):
        translator = make_translator()
        asyncio.run(translator.init())
        self.assertNotIn("de", translator.raw_data)

        de = translator.raw_data["de"]
        self.assertIn("de", translator.raw_data)
        self.assertIsInstance(de, dict)
        self.assertTrue(de)
        self.assertIs(translator.raw_data["de"], de)

    def test_translations_still_resolve_after_lazy_load(self):
        translator = make_translator()
        asyncio.run(translator.init())
        en = translator.raw_data["en"]
        de = translator.raw_data["de"]
        # Same keys, different languages
        self.assertTrue(set(en).intersection(de))


if __name__ == "__main__":
    unittest.main()
