import logging
import unittest
from pathlib import Path

from heroku.log import RatkoFormatter


class BrandingTest(unittest.TestCase):
    def test_content_channel_uses_ratko_title_and_migrates_legacy_cache(self):
        source = Path("heroku/database.py").read_text()
        self.assertIn('CONTENT_CHANNEL_TITLE = "ratko-userbot"', source)
        self.assertIn("forums_cache.pop(LEGACY_CONTENT_CHANNEL_TITLE)", source)

    def test_private_bot_messages_have_no_repository_links(self):
        for filename in ("inline_stuff.py", "quickstart.py"):
            source = Path("heroku/modules", filename).read_text()
            self.assertNotIn("github.com/unsidogandon/ratko", source)

    def test_default_module_catalog_is_owned_by_ratko(self):
        loader_source = Path("heroku/modules/loader.py").read_text()
        presets_source = Path("heroku/modules/presets.py").read_text()

        self.assertIn(
            'DEFAULT_MODULES_REPO = "https://raw.githubusercontent.com/'
            'unsidogandon/ratko/main"',
            loader_source,
        )
        self.assertNotIn("coddrago/modules", presets_source)
        self.assertFalse(Path("full.txt").exists())
        self.assertIn("if repo == DEFAULT_MODULES_REPO:", loader_source)

    def test_updater_does_not_depend_on_dynamic_module_file(self):
        source = Path("heroku/modules/updater.py").read_text()
        self.assertNotIn("Path(__file__)", source)
        self.assertIn("Path(version.__file__).resolve().parent.parent", source)

    def test_log_formatter_rebrands_internal_package(self):
        formatter = RatkoFormatter("%(name)s: %(message)s")
        record = logging.LogRecord(
            "heroku.inline.token_obtainment",
            logging.INFO,
            __file__,
            1,
            "Bot token not found",
            (),
            None,
        )

        self.assertEqual(
            formatter.format(record),
            "ratko.inline.token_obtainment: Bot token not found",
        )

    def test_log_formatter_keeps_herokutl_name(self):
        formatter = RatkoFormatter("%(name)s")
        record = logging.LogRecord(
            "herokutl.network",
            logging.INFO,
            __file__,
            1,
            "Connected",
            (),
            None,
        )

        self.assertEqual(formatter.format(record), "herokutl.network")

    def test_log_formatter_redacts_bot_tokens(self):
        formatter = RatkoFormatter("%(message)s")
        token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd"
        record = logging.LogRecord(
            "heroku.inline.token_obtainment",
            logging.DEBUG,
            __file__,
            1,
            "Token: %s",
            (token,),
            None,
        )

        formatted = formatter.format(record)
        self.assertNotIn(token, formatted)
        self.assertEqual(formatted, "Token: <redacted bot token>")


if __name__ == "__main__":
    unittest.main()
