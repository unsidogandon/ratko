# ©️ Dan Gazizullin, 2021-2023
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

# ©️ Codrago, 2024-2030
# This file is a part of Heroku Userbot
# 🌐 https://github.com/coddrago/Heroku
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import asyncio
import hashlib
import json
import logging
import os
import typing
from pathlib import Path
from urllib.parse import urlsplit

import orjson

from . import utils
from ._internal import fetch_text
from .database import Database
from .tl_cache import CustomTelegramClient
from .types import Module

logger = logging.getLogger(__name__)

# The ruamel parser is created lazily: with the parsed-pack cache below
# the startup path parses no YAML at all, and importing ruamel costs
# ~50ms on its own
_yaml_parser = None


def _get_yaml_parser():
    global _yaml_parser
    if _yaml_parser is None:
        from ruamel.yaml import YAML

        _yaml_parser = YAML(typ="safe")

    return _yaml_parser


PACKS = Path(__file__).parent / "langpacks"

# Mirrors loader.BASE_DIR (importing it here would be circular): the
# parsed language packs cache lives next to the module langpack caches
_CACHE_BASE_DIR = (
    os.environ.get("RATKO_DATA_ROOT")
    or os.environ.get("HEROKU_DATA_ROOT")
    or (
        "/data"
        if "DOCKER" in os.environ
        else os.path.normpath(os.path.join(utils.get_base_dir(), ".."))
    )
)
CORE_LANGPACKS_CACHE_PATH = Path(_CACHE_BASE_DIR) / "loaded_modules" / "core-langpacks"

# In-memory memo of the persistent cache: path -> (signature, parsed)
_parsed_pack_memo: dict = {}
SUPPORTED_LANGUAGES = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "uk": "🇺🇦 Український",
    "pz": "🇺🇦 потужні",
    "de": "🇩🇪 Deutsch",
    "ja": "🇯🇵 日本語",
    "unsido": "🏴‍☠️ Unsido",
}
LANGUAGE_ALIASES = {
    "ua": "uk",
    "jp": "ja",
}
LANGUAGE_COMPAT_ALIASES = {
    "uk": ("ua",),
    "ja": ("jp",),
}
MEME_LANGUAGES = {
    "leet": "🏴‍☠️ 1337",
    "uwu": "🏴‍☠️ UwU",
    "tiktok": "🏴‍☠️ TikTokKid",
    "neofit": "🏴‍☠️ Neofit",
}


def normalize_language(language: str) -> str:
    return LANGUAGE_ALIASES.get(language, language)


def normalize_language_token(language: str) -> str:
    return language if utils.check_url(language) else normalize_language(language)


def iter_language_codes(language: str) -> typing.Iterator[str]:
    if utils.check_url(language):
        yield language
        return

    language = normalize_language(language)
    yield language
    yield from LANGUAGE_COMPAT_ALIASES.get(language, ())


def get_language_pack_path(language: str) -> Path | None:
    for code in iter_language_codes(language):
        for suffix in (".json", ".yml"):
            path = PACKS / f"{code}{suffix}"
            if path.exists():
                return path

    return None


def fmt(text: str, kwargs: dict) -> str:
    for key, value in kwargs.items():
        if f"{{{key}}}" in text:
            text = text.replace(f"{{{key}}}", str(value))

    return text


class BaseTranslator:
    def _get_pack_content(
        self,
        pack: Path,
        prefix: str = "heroku.modules.",
    ) -> dict | None:
        parsed = self._load_parsed_pack(pack)
        if pack.suffix == ".json":
            # JSON packs are consumed as-is (legacy behaviour)
            return parsed

        return self._flatten_pack(parsed, prefix)

    def _get_pack_raw(
        self,
        content: str,
        suffix: str,
        prefix: str = "heroku.modules.",
    ) -> dict | None:
        parsed = self._parse_pack(content, suffix)
        if suffix == ".json":
            # JSON packs are consumed as-is (legacy behaviour)
            return parsed

        return self._flatten_pack(parsed, prefix)

    @staticmethod
    def _parse_pack(content: str, suffix: str):
        if suffix == ".json":
            return json.loads(content)

        parsed = _get_yaml_parser().load(content)
        if not isinstance(parsed, dict):
            raise ValueError("Translation pack must be a mapping")

        return parsed

    @staticmethod
    def _flatten_pack(parsed: dict, prefix: str) -> dict:
        def flatten(pack):
            return {
                (
                    f"{module.strip('$')}.{key}"
                    if module.startswith("$")
                    else f"{prefix}{module}.{key}"
                ): value
                for module, strings in pack.items()
                for key, value in strings.items()
                if key != "name"
            }

        # Detect the nesting, not the length of a language/module name. This
        # supports named packs like unsido and modules with two-letter names.
        if parsed and all(
            isinstance(pack, dict)
            and all(isinstance(strings, dict) for strings in pack.values())
            for pack in parsed.values()
        ):
            return {language: flatten(pack) for language, pack in parsed.items()}

        return flatten(parsed)

    def _load_parsed_pack(self, pack: Path) -> dict:
        """Parse a local language pack, with a persistent cache.

        A cold boot parses the YAML files (~70ms each); the parsed
        result is then stored as JSON next to the module langpack
        caches, so every subsequent boot loads it in ~1ms instead.
        The cache is invalidated by the pack file's (mtime, size)
        signature. Pack values are plain strings, so the JSON
        round-trip is type-exact.
        """
        key = str(pack)
        try:
            stat = pack.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None

        memo = _parsed_pack_memo.get(key)
        if memo is not None and memo[0] == signature:
            return memo[1]

        parsed = None
        cache_file = (
            CORE_LANGPACKS_CACHE_PATH
            / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"
            if signature is not None
            else None
        )
        if cache_file is not None:
            try:
                cached = orjson.loads(cache_file.read_bytes())
                if (
                    isinstance(cached, dict)
                    and cached.get("signature") == list(signature)
                    and isinstance(cached.get("data"), dict)
                ):
                    parsed = cached["data"]
            except (OSError, orjson.JSONDecodeError):
                parsed = None

        if parsed is None:
            parsed = self._parse_pack(pack.read_text(encoding="utf-8"), pack.suffix)
            if cache_file is not None:
                try:
                    CORE_LANGPACKS_CACHE_PATH.mkdir(parents=True, exist_ok=True)
                    cache_file.write_bytes(
                        orjson.dumps({"signature": list(signature), "data": parsed})
                    )
                except OSError:
                    logger.warning(
                        "Failed to cache parsed langpack %s", pack, exc_info=True
                    )

        _parsed_pack_memo[key] = (signature, parsed)
        return parsed

    def getkey(self, key: str) -> typing.Any:
        return self._data.get(key, False)

    def gettext(self, text: str) -> typing.Any:
        return self.getkey(text) or text

    def _get_module_pack_raw(self, content: str) -> dict | None:
        data = _get_yaml_parser().load(content)
        if not isinstance(data, dict) or any(
            not isinstance(key, str) for key in data
        ):
            return None

        if all(isinstance(value, str) for value in data.values()):
            return data

        if all(
            isinstance(pack, dict)
            and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in pack.items()
            )
            for pack in data.values()
        ):
            return data

        return None

    async def load_module_translations(
        self, pack_url: str, cache_path: Path = None, *, cache_only: bool = False
    ) -> bool | dict:
        if cache_only:
            if cache_path is None:
                return {}
            try:
                data = self._get_module_pack_raw(
                    cache_path.read_text(encoding="utf-8")
                )
            except FileNotFoundError:
                return {}
            except Exception:
                logger.warning(
                    "Unable to decode cached %s", cache_path, exc_info=True
                )
                return {}
            return (
                self._select_module_language(data) if isinstance(data, dict) else {}
            )

        if not hasattr(self, "_module_translation_semaphore"):
            self._module_translation_semaphore = asyncio.Semaphore(4)
        try:
            async with self._module_translation_semaphore:
                content = await utils.run_sync(fetch_text, pack_url)
            data = self._get_module_pack_raw(content)
            if data is None:
                logger.warning("Invalid module translation pack from %s", pack_url)
        except Exception:
            logger.exception("Unable to decode %s", pack_url)
            data = None
            content = None

        if not isinstance(data, dict):
            content = None
            if cache_path and cache_path.exists():
                try:
                    data = self._get_module_pack_raw(
                        cache_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    logger.exception("Unable to decode cached %s", cache_path)
                    return False

                if not isinstance(data, dict):
                    return {}

            else:
                return {}

        if cache_path and content is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(content, encoding="utf-8")
            except Exception:
                logger.exception("Failed to save `%s`'s cache copy", pack_url)

        return self._select_module_language(data)

    def _select_module_language(self, data: dict) -> dict:
        if all(isinstance(value, str) for value in data.values()):
            return data

        if lang := self.db.get(__name__, "lang", False):
            return next(
                (
                    data[code]
                    for language in lang.split()
                    for code in iter_language_codes(language)
                    if code in data
                ),
                data.get("en", {}),
            )

        return data.get("en", {})


class _LazyLanguagePacks(dict):
    """Language pack map that loads packs on first access.

    Only ``en`` and the active user language are parsed during startup;
    any other language is loaded (from the parsed-pack cache) the first
    time it is actually requested.
    """

    __slots__ = ("_translator",)

    def __init__(self, translator: "Translator"):
        super().__init__()
        self._translator = translator

    def __missing__(self, lang: str):
        pack_path = get_language_pack_path(lang)
        data = self._translator._get_pack_content(pack_path) if pack_path else {}
        self[lang] = data
        return data


class Translator(BaseTranslator):
    def __init__(self, client: CustomTelegramClient, db: Database):
        self._client = client
        self.db = db
        self._data = {}
        self.raw_data = _LazyLanguagePacks(self)

    async def init(self) -> bool:
        self._data = self._get_pack_content(PACKS / "en.yml")
        self.raw_data["en"] = self._data.copy()
        any_ = False
        if lang := self.db.get(__name__, "lang", False):
            for language in map(normalize_language_token, lang.split()):
                if utils.check_url(language):
                    try:
                        data = self._get_pack_raw(
                            await utils.run_sync(fetch_text, language),
                            Path(urlsplit(language).path).suffix,
                        )
                        if data and all(isinstance(pack, dict) for pack in data.values()):
                            data = self._select_module_language(data)
                    except Exception:
                        logger.exception("Unable to decode %s", language)
                        continue

                    self._data.update(data)
                    self.raw_data[language] = data
                    any_ = True
                    continue

                if possible_path := get_language_pack_path(language):
                    data = self._get_pack_content(possible_path)
                    self._data.update(data)
                    self.raw_data[language] = data
                    any_ = True

        # The rest of the supported languages are loaded lazily on
        # first access (see _LazyLanguagePacks) instead of being
        # preloaded here: that cost ~6 YAML parses on every boot
        return any_


class ExternalTranslator(BaseTranslator):
    def __init__(self):
        self.data = {}
        for lang in SUPPORTED_LANGUAGES:
            pack_path = get_language_pack_path(lang)
            self.data[lang] = (
                self._get_pack_content(pack_path, prefix="") if pack_path else {}
            )

    def get(self, key: str, lang: str) -> str:
        return self.data[lang].get(key, False) or key

    def getdict(self, key: str, **kwargs) -> dict:
        return {
            lang: fmt(self.data[lang].get(key, False) or key, kwargs)
            for lang in self.data
        }


# getattr() sentinel: a never-existing attribute name. It only serves as the
# `next()` default when no `strings_<lang>` attribute matched, so getattr falls
# back to the base strings. Must never evaluate anything per lookup.
_MISSING_STRINGS_ATTR = "_ratko_missing_strings_"


class Strings:
    def __init__(self, mod: Module, translator: Translator):  # skipcq: PYL-W0621
        self._mod = mod
        self._translator = translator

        if not translator:
            logger.debug("Module %s got empty translator %s", mod, translator)

        self._base_strings = mod.strings  # Back 'em up, bc they will get replaced
        self.external_strings = {}

    def get(self, key: str, lang: str | None = None) -> str:
        try:
            return self._translator.raw_data[lang][f"{self._mod.__module__}.{key}"]
        except KeyError:
            return self[key]

    def __getitem__(self, key: str) -> str:
        return (
            self.external_strings.get(key, None)
            or (
                self._translator.getkey(f"{self._mod.__module__}.{key}")
                if self._translator is not None
                else False
            )
            or (
                getattr(
                    self._mod,
                    next(
                        (
                            f"strings_{lang}"
                            for original_lang in (
                                self._translator.db.get(
                                    __name__,
                                    "lang",
                                    "en",
                                ).split(" ")
                                if self._translator is not None
                                else ["en"]
                            )
                            for lang in (
                                list(iter_language_codes(original_lang))
                                + (
                                    ["en"]
                                    if original_lang in ["leet", "uwu", "neofit"]
                                    else ["ru"] if original_lang == "tiktok" else []
                                )
                            )
                            if hasattr(self._mod, f"strings_{lang}")
                            and isinstance(getattr(self._mod, f"strings_{lang}"), dict)
                            and key in getattr(self._mod, f"strings_{lang}")
                        ),
                        _MISSING_STRINGS_ATTR,
                    ),
                    self._base_strings,
                ).get(key)
                if self._translator is not None
                else self._base_strings.get(key)
            )
            or self._base_strings.get(key, f"Unknown strings: {key}")
        )

    def __call__(
        self,
        key: str,
        _: typing.Any | None = None,  # Compatibility tweak for FTG\GeekTG
    ) -> str:
        return self.__getitem__(key)

    def __iter__(self):
        return self._base_strings.__iter__()


translator = ExternalTranslator()
