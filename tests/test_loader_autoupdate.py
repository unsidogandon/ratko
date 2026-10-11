"""LoaderMod boot-time module auto-reinstall gate (auto_update_modules).

Re-installing modules from their source links on every boot executes
remote code without any owner action; the gate disables that by default.
"""

import unittest
from unittest.mock import AsyncMock, Mock

# inline.core participates in a pre-existing import cycle that is only
# resolved when heroku.main is imported first (as the entry point does)
import heroku.main  # noqa: F401
from heroku.modules import loader as loader_module


class FakeDB:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.set_calls = []

    def get(self, owner, key, default=None):
        return self.values.get((owner, key), default)

    def set(self, owner, key, value):
        self.set_calls.append((owner, key, value))
        self.values[(owner, key)] = value


class FakeSettings:
    def __init__(self):
        self.aliases = {}

    def get(self, key, default=None):
        return self.aliases if key == "aliases" else default

    def set(self, key, value):
        if key == "aliases":
            self.aliases = value


def make_loader_mod(todo, auto_update, secure_boot=False):
    mod = object.__new__(loader_module.LoaderMod)
    mod._db = FakeDB({("heroku.loader", "secure_boot"): secure_boot})
    mod.config = {"auto_update_modules": auto_update}
    mod.download_and_install = AsyncMock(return_value=0)
    mod.update_modules_in_db = Mock()
    mod._get_modules_to_load = AsyncMock(return_value=todo)
    mod.fully_loaded = False
    mod.allmodules = Mock(add_alias=Mock(return_value=True))
    mod._settings = FakeSettings()

    def lookup(name):
        if name == "settings":
            return mod._settings
        # _update_modules suppresses AttributeError for missing Updater
        raise AttributeError(name)

    mod.lookup = lookup
    return mod


class AutoUpdateModulesGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_skips_download_and_db_map_update(self):
        todo = {"WhoisMod": "https://example.com/whois.py"}
        mod = make_loader_mod(todo, auto_update=False)

        await mod._update_modules()

        mod.download_and_install.assert_not_called()
        mod.update_modules_in_db.assert_not_called()
        self.assertTrue(mod.fully_loaded)
        # Aliases are still re-applied (same as with an empty map before)
        self.assertEqual(mod._settings.aliases, {})

    async def test_enabled_reinstalls_from_links(self):
        todo = {"WhoisMod": "https://example.com/whois.py"}
        mod = make_loader_mod(todo, auto_update=True)

        await mod._update_modules()

        mod.download_and_install.assert_awaited_once_with(
            "https://example.com/whois.py", name="WhoisMod"
        )
        mod.update_modules_in_db.assert_called_once()
        self.assertTrue(mod.fully_loaded)

    async def test_secure_boot_wins_even_when_enabled(self):
        mod = make_loader_mod(
            {"WhoisMod": "https://example.com/whois.py"},
            auto_update=True,
            secure_boot=True,
        )

        await mod._update_modules()

        mod.download_and_install.assert_not_called()
        mod.update_modules_in_db.assert_not_called()
        self.assertIn(("heroku.loader", "secure_boot", False), mod._db.set_calls)
        self.assertTrue(mod._secure_boot)

    async def test_enabled_with_empty_map_downloads_nothing(self):
        mod = make_loader_mod({}, auto_update=True)

        await mod._update_modules()

        mod.download_and_install.assert_not_called()
        mod.update_modules_in_db.assert_called_once()


class AutoUpdateConfigDefaultTest(unittest.TestCase):
    def test_config_default_is_off(self):
        mod = object.__new__(loader_module.LoaderMod)
        mod.strings = {"name": "Loader"}
        loader_module.LoaderMod.__init__(mod)
        self.assertIs(mod.config["auto_update_modules"], False)


if __name__ == "__main__":
    unittest.main()
