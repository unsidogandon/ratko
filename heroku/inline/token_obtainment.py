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
import contextlib
import logging
import os
import random
import re
import typing

import aiohttp

from .. import main, utils
from .._internal import fw_protect
from . import utils as inutils
from .types import InlineUnit

if typing.TYPE_CHECKING:
    from ..inline.core import InlineManager

logger = logging.getLogger(__name__)


class TokenObtainment(InlineUnit):
    @staticmethod
    def _get_bot_headers() -> dict:
        hdrs = inutils.headers.copy()
        hdrs.update(
            {
                "x-aj-referer": "https://webappinternal.telegram.org/botfather",
                "x-requested-with": "XMLHttpRequest",
            }
        )
        return hdrs

    @staticmethod
    def _find_bot_id(content: str, username: str = "") -> typing.Optional[str]:
        if username:
            username = username.strip("@")
            match = re.search(
                rf'href="/botfather/bot/(\d+)".*?@{re.escape(username)}(?:<|\s)',
                content,
                flags=re.DOTALL,
            )
            return match.group(1) if match else None

        match = re.search(
            r'href="/botfather/bot/(\d+)".*?@(\w+_[0-9A-Za-z]{6}_bot)(?:<|\s)',
            content,
            flags=re.DOTALL,
        )
        return match.group(1) if match else None

    async def _get_bot_token(
        self: "InlineManager",
        session: aiohttp.ClientSession,
        url: str,
        bot_id: typing.Union[str, int],
        headers: dict,
        retries: int = 8,
    ) -> typing.Optional[str]:
        for _ in range(retries):
            async with session.get(url + f"/bot/{bot_id}", headers=headers) as resp:
                if resp.status == 200:
                    with contextlib.suppress(Exception):
                        text = (await resp.json())["h"]
                        token = re.search(r"(\d+:[A-Za-z0-9\-_]{35})", text)
                        if token:
                            return token.group(1)

            await asyncio.sleep(1.5)

        return None

    async def _save_bot_token(
        self: "InlineManager",
        session: aiohttp.ClientSession,
        url: str,
        _hash: str,
        bot_id: typing.Union[str, int],
        token: str,
        headers: dict,
    ) -> bool:
        self._db.set("heroku.inline", "bot_token", token)
        self._token = token
        self.bot_id = bot_id

        for method, value in {
            "settings[inline]": "true",
            "settings[inph]": "user@heroku:~$",
            "settings[infdb]": "1",
        }.items():
            await fw_protect()

            async with session.post(
                url + f"/api?hash={_hash}",
                data={method: value, "bid": bot_id, "method": "changeSettings"},
                headers=inutils.headers,
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "Error while changing bot inline settings: resp%s\nbody: %s",
                        resp.status,
                        await resp.text(),
                    )
                    return False

        commands = {
            "start": "Welcome message",
            "profile": "Get main information about bot",
        }

        def get_cmds(x) -> dict[str, str]:
            return dict(inutils.BOT_COMMANDS_PATTERN.findall(x))

        async with session.get(
            url + f"/bot/{bot_id}/commands", headers=headers
        ) as resp:
            if resp.status != 200:
                logger.warning("Unable to get bot commands list")
            else:
                _cmds = get_cmds((await resp.json())["h"])
                has_commands = any(cmd in commands for cmd in _cmds)
                if not has_commands:
                    await self._set_commands(session, url, _hash, commands)

        return True

    async def _create_bot(
        self: "InlineManager", session: aiohttp.ClientSession, url: str, _hash: str
    ):
        logger.info("User doesn't have bot, attempting creating new one")

        if self._db.get("heroku.inline", "custom_bot", False):
            username = self._db.get("heroku.inline", "custom_bot").strip("@")
            data = {"username": username, "method": "checkBotUsername"}
            await fw_protect()

            async with session.post(
                url + f"/api?hash={_hash}", data=data, headers=inutils.headers
            ) as resp:
                if resp.status != 200:
                    logger.error("Error while username check: resp%s", resp.status)
                    return False

                content = await resp.json()
            if not content.get("ok", False):
                logger.error(f"Username @{username} is not available in BotFather")
                return False
        else:
            logger.error("No custom bot username specified in database. Inline bot creation failed. Please set a bot username using .ch_ratko_bot <username>")
            return False

        try:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                open(f"{os.getcwd()}/assets/heroku.png", "rb"),
                filename="heroku.png",
                content_type="image/png",
            )
            form.add_field("method", "uploadMedia")
            form.add_field("target", "bot_userpic")
            async with session.post(
                url + f"/api?hash={_hash}", data=form, headers=inutils.headers
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "Error while uploading bot userpic: resp%s", resp.status
                    )
                    raise RuntimeError("Upload failed")
                content = await resp.json()
                photo_id = content["media"]["photo_id"]
        except (RuntimeError, KeyError, OSError):
            photo_id = ""

        data = {
            "title": f"🪐 ratko {utils.get_version_raw()}"[:64],
            "about": "",
            "username": username,
            "userpic": photo_id,
            "method": "createBot",
        }
        await fw_protect()
        async with session.post(
            url + f"/api?hash={_hash}", data=data, headers=inutils.headers
        ) as resp:
            if resp.status != 200:
                logger.error(
                    "Error while creating the bot: resp%s\ncontent: %s\ndata: %s",
                    resp.status,
                    await resp.text(),
                    data,
                )
                return False

            content = await resp.json()
            if content.get("error", False) == "NEWBOT_LIMIT_EXCEEDED":
                logger.error(
                    "Error while creating the bot. You've reached the limit of bots per account. "
                    "Please, remove some of your bots or use one of yours by setting its username with "
                    "command ch_ratko_bot @username"
                )
                return False
            elif content.get("error", False) == "Error":
                logger.error(
                    "Error while creating the bot. Please, send the following information to the developers: %s\ndata: %s",
                    content,
                    data,
                )
                # в этом случае бот может быть создан, так шо стоит перепроверить его наличие
                # тута главное собрать побольше информации для нас, чтобы понять, в каких случаях может возникать эта ошибка
            elif not content.get("ok", False):
                logger.error(
                    "Error while creating the bot. Maybe you've been banned: %s\ndata: %s",
                    content,
                    data,
                )
                return False
        bot_id = content.get("bot_id")
        if bot_id:
            headers = self._get_bot_headers()
            token = await self._get_bot_token(session, url, bot_id, headers)
            if token:
                return await self._save_bot_token(
                    session,
                    url,
                    _hash,
                    bot_id,
                    token,
                    headers,
                )

        for _ in range(8):
            if await self._assert_token(
                session, url, _hash, create_new_if_needed=False
            ):
                return True

            await asyncio.sleep(1.5)

        logger.error("Bot was created, but token could not be obtained")
        return False

    async def _assert_token(
        self: "InlineManager",
        session: aiohttp.ClientSession,
        url: str,
        _hash: str,
        create_new_if_needed: bool = True,
        revoke_token: bool = False,
    ) -> bool:
        if self._token:
            return True

        logger.info("Bot token not found in db, attempting search in BotFather")

        async with session.get(url, headers=inutils.headers) as resp:
            if resp.status != 200:
                logger.error("Error while getting bot list: resp%s", resp.status)
                return False
            content = await resp.text()

        username = self._db.get("heroku.inline", "custom_bot", False)
        bot_id = self._find_bot_id(content, username or "")

        if bot_id:
            hdrs = self._get_bot_headers()
            if revoke_token:
                async with session.post(
                    url + f"/api?hash={_hash}",
                    data={"bid": bot_id, "method": "revokeAccessToken"},
                    headers=inutils.headers,
                ) as resp:
                    if resp.status != 200:
                        logger.error("Error while revoking token: resp%s", resp.status)
                        return False

                    token = (await resp.json())["token"]
            else:
                token = await self._get_bot_token(session, url, bot_id, hdrs, retries=1)
                if not token:
                    logger.error("Error while getting token for bot %s", bot_id)
                    return False

            return await self._save_bot_token(
                session,
                url,
                _hash,
                bot_id,
                token,
                hdrs,
            )

        return (
            await self._create_bot(session, url, _hash)
            if create_new_if_needed
            else False
        )

    async def _reassert_token(
        self: "InlineManager", session: aiohttp.ClientSession, url: str, _hash: str
    ):
        is_token_asserted = await self._assert_token(
            session, url, _hash, revoke_token=True
        )
        if not is_token_asserted:
            self.init_complete = False
        else:
            await self.register_manager(ignore_token_checks=True)

    async def _dp_revoke_token(
        self: "InlineManager",
        session: aiohttp.ClientSession,
        url: str,
        _hash: str,
        already_initialised: bool = True,
    ):
        if already_initialised:
            await self._stop()
            logger.error("Got polling conflict. Attempting token revocation...")

        self._db.set("heroku.inline", "bot_token", None)
        self._token = None
        if already_initialised:
            asyncio.ensure_future(self.reassert_token())
        else:
            return await self._reassert_token(session, url, _hash)

    async def _check_bot(
        self: "InlineManager",
        session: aiohttp.ClientSession,
        url: str,
        _hash: str,
        username: str,
    ):
        async with session.get(url, headers=inutils.headers) as resp:
            if resp.status != 200:
                logger.error("Error while getting bot list: resp%s", resp.status)
                return False
            content = await resp.text()
        result = re.search(inutils.BOT_ID_PATTERN.format(username), content)

        if not result:
            data = {"username": username, "method": "checkBotUsername"}
            await fw_protect()

            async with session.post(
                url + f"/api?hash={_hash}", data=data, headers=inutils.headers
            ) as resp:
                if resp.status != 200:
                    logger.error("Error while username check: resp%s", resp.status)
                    return False

                content = await resp.json()
            result = content.get("ok", False)
        return result

    async def _set_commands(
        self: "InlineManager",
        session: aiohttp.ClientSession,
        url: str,
        _hash: str,
        commands: dict[str, str],
    ):
        bid = self.bot_id
        if not bid:
            raise RuntimeError("Bot is not initialized")

        for command, desc in commands.items():
            data = {
                "bid": bid,
                "lang_code": "",
                "scopes[]": ["users", "chats", "chatadmins"],
                "command": command,
                "description": desc,
                "replace": "",
                "method": "setCommand",
            }

            logger.debug("Setting bot command %s: %s", command, desc)

            async with session.post(
                url + f"/api?hash={_hash}", data=data, headers=inutils.headers
            ) as resp:
                if resp.status != 200 or not (await resp.json()).get("ok", False):
                    logger.error(
                        "Error while setting command: resp%s: %s",
                        resp.status,
                        await resp.json(),
                    )
                    continue

            await asyncio.sleep(random.randint(1, 3))

    async def assert_token(
        self: "InlineManager",
        create_new_if_needed: bool = True,
        revoke_token: bool = False,
    ):
        return await self._main_token_manager(
            1, create_new_if_needed=create_new_if_needed, revoke_token=revoke_token
        )

    async def create_bot(self: "InlineManager"):
        return await self._main_token_manager(2)

    async def dp_revoke_token(self: "InlineManager", already_initialised: bool = True):
        return await self._main_token_manager(
            3, already_initialised=already_initialised
        )

    async def reassert_token(self: "InlineManager"):
        return await self._main_token_manager(4)

    async def check_bot(self: "InlineManager", username: str):
        return await self._main_token_manager(5, username=username)

    async def set_commands(self: "InlineManager", commands: dict[str, str]):
        """
        Sets bot's commands in the menu

        :param commands: dict of commands and their descriptions
        """
        return await self._main_token_manager(6, commands=commands)
