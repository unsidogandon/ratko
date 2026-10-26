"""InlineDownError: access to self.inline.bot raises a clear error when
the inline bot is not running, while getattr(..., None) still works."""

import unittest

import heroku.main  # noqa: F401  (import cycle)
from heroku.inline.core import InlineManager
from heroku.types import InlineDownError


class InlineDownErrorTest(unittest.TestCase):
    def test_is_attribute_error(self):
        # AttributeError subclass keeps getattr(inline, "bot", None) working
        self.assertTrue(issubclass(InlineDownError, AttributeError))

    def test_property_raises_when_bot_is_not_running(self):
        manager = object.__new__(InlineManager)
        manager._bot = None
        with self.assertRaises(InlineDownError):
            manager.bot

    def test_property_returns_running_bot(self):
        manager = object.__new__(InlineManager)
        sentinel = object()
        manager._bot = sentinel
        self.assertIs(manager.bot, sentinel)

    def test_getattr_with_default_still_works(self):
        manager = object.__new__(InlineManager)
        manager._bot = None
        self.assertIsNone(getattr(manager, "bot", None))


if __name__ == "__main__":
    unittest.main()
