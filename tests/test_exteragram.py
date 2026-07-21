import unittest

from heroku.inline.tl import TelethonBot
from heroku.inline.utils import Utils as InlineUtils
from heroku.tl_cache import CustomTelegramClient
from heroku.utils.messages import replace_tg_emoji_tags


class FakeDatabase:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def get(self, owner, key, default=None):
        if (owner, key) == ("HerokuSettingsMod", "exteragram_emoji"):
            return self.enabled
        return default


class FakeClient:
    def __init__(self, *, premium=False, enabled=True):
        self.heroku_me = type("Me", (), {"premium": premium})()
        self.loader = type("Loader", (), {"db": FakeDatabase(enabled)})()


class ExteraGramEmojiTest(unittest.TestCase):
    def test_non_premium_tags_become_tg_emoji_links(self):
        text = (
            '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji> '
            "<emoji document_id='4974508259839836856'>bullet</emoji>"
        )

        self.assertEqual(
            replace_tg_emoji_tags(text, FakeClient()),
            '<a href="tg://emoji?id=5471950641918121951">moon</a> '
            '<a href="tg://emoji?id=4974508259839836856">bullet</a>',
        )

    def test_premium_accounts_keep_native_tags(self):
        text = '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji>'
        self.assertEqual(
            replace_tg_emoji_tags(text, FakeClient(premium=True)),
            text,
        )

    def test_disabled_setting_keeps_native_tags(self):
        text = '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji>'
        self.assertEqual(
            replace_tg_emoji_tags(text, FakeClient(enabled=False)),
            text,
        )

    def test_inline_sanitizer_keeps_link_fallback_for_non_premium(self):
        inline = object.__new__(InlineUtils)
        inline._client = FakeClient()
        text = '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji>'
        self.assertEqual(
            inline.sanitise_text(text),
            '<a href="tg://emoji?id=5471950641918121951">moon</a>',
        )

    def test_inline_sanitizer_preserves_premium_pre_edit_tags(self):
        inline = object.__new__(InlineUtils)
        inline._client = FakeClient(premium=True)
        text = '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji>'
        self.assertEqual(inline.sanitise_text(text), text)

    def test_client_transform_recurses_through_message_sequences(self):
        fake = FakeClient()
        client = object.__new__(CustomTelegramClient)
        client.heroku_me = fake.heroku_me
        client.loader = fake.loader
        text = '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji>'
        link = '<a href="tg://emoji?id=5471950641918121951">moon</a>'
        self.assertEqual(
            client._exteragram_transform((text, [text])),
            (link, [link]),
        )

    def test_inline_bot_uses_owner_emoji_settings(self):
        owner = FakeClient()
        bot = TelethonBot(object(), owner)
        text = '<tg-emoji emoji-id="5471950641918121951">moon</tg-emoji>'
        self.assertEqual(
            bot._emoji_text(text),
            '<a href="tg://emoji?id=5471950641918121951">moon</a>',
        )


if __name__ == "__main__":
    unittest.main()
