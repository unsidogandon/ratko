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

import git
import time
import contextlib
import random
import psutil
import os
import glob
import requests
import re
import logging
import emoji
import herokutl

from typing import Optional
from pathlib import Path
from io import BytesIO
from herokutl.errors import WebpageMediaEmptyError
from herokutl.types import InputMediaWebPage
from herokutl.tl.types import Message
from herokutl.utils import get_display_name
from .. import loader, utils, version
import platform as lib_platform
import getpass

logger = logging.getLogger(__name__)

DEFAULT_INFO_BANNER = (
    "https://raw.githubusercontent.com/unsidogandon/ratko/main/banner.jpg"
)
OLD_INFO_BANNER = "https://raw.githubusercontent.com/unsidogandon/ratko/main/banner.jpg"
CAT_BANNERS = [
    "https://cataas.com/cat?width=900&height=700&fit=cover",
    "https://cataas.com/cat/cute?width=900&height=700&fit=cover",
    "https://cataas.com/cat/funny?width=900&height=700&fit=cover",
    "https://cataas.com/cat/says/ratko?width=900&height=700&fit=cover&fontSize=28",
]
DEFAULT_INFO_MESSAGE = (
    "<blockquote>влд: {me}</blockquote>\n"
    "про ратко\n"
    "<blockquote>ос {os}\n"
    "версия платформа\n"
    "{version}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{platform}</blockquote>\n"
    "<blockquote>ping&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;uptime\n"
    "🚀{ping}&nbsp;&nbsp;✨{uptime}</blockquote>\n"
    "остальное\n"
    "<blockquote>upd: {upd}\n"
    "использование цп: {cpu_usage}\n"
    "использование оперативы: {ram_usage}\n"
    "ос: {os}\n"
    "ядрышко: {kernel}\n"
    "проц: {cpu}</blockquote>"
)
LEGACY_INFO_MARKERS = (
    "Heroku",
    "Heroku Userbot",
    "Heroku supports",
    "Friendly-Telegram",
    "GeekTG",
)


@loader.tds
class RatkoInfoMod(loader.Module):
    """Show userbot info"""

    strings = {"name": "RatkoInfo"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "custom_message",
                DEFAULT_INFO_MESSAGE,
                doc=lambda: (
                    self.strings("_cfg_cst_msg")
                    + "\n"
                    + (
                        "\n"
                        + self.strings("_cfg_cst_ph").format(
                            "\n" + utils.config_placeholders()
                        )
                        if utils.config_placeholders()
                        else ""
                    )
                ),
            ),
            loader.ConfigValue(
                "banner_url",
                DEFAULT_INFO_BANNER,
                lambda: self.strings("_cfg_banner"),
                validator=loader.validators.RandomLink(),
            ),
            loader.ConfigValue(
                "random_cats",
                False,
                "Show random cat images in .info",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "ping_emoji",
                "🪐",
                lambda: self.strings["ping_emoji"],
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "quote_media",
                False,
                "Switch preview media to quote",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "invert_media",
                False,
                "Switch preview invert media",
                validator=loader.validators.Boolean(),
            ),
        )

    def _get_os_name(self):
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME"):
                        return line.split("=")[1].strip().strip('"')
        except FileNotFoundError:
            return self.strings["non_detectable"]

    def _get_effective_info_template(self) -> str:
        custom_message = self.config["custom_message"]
        if not custom_message:
            return DEFAULT_INFO_MESSAGE

        if any(marker in custom_message for marker in LEGACY_INFO_MARKERS):
            return DEFAULT_INFO_MESSAGE

        return custom_message

    def _get_effective_banner(self) -> tuple[str | None, bool]:
        if self.config.get("random_cats"):
            return f"{random.choice(CAT_BANNERS)}&t={time.time_ns()}", True

        banner_url = self.config["banner_url"] or DEFAULT_INFO_BANNER
        if banner_url == OLD_INFO_BANNER:
            banner_url = DEFAULT_INFO_BANNER

        return banner_url, False

    async def _render_info(self, start: float) -> str:
        try:
            up_to_date = utils.is_up_to_date()
            if up_to_date:
                upd = self.strings["up-to-date"]
            else:
                upd = self.strings["update_required"].format(prefix=self.get_prefix())
        except Exception:
            upd = ""

        me = (
            '<b><a href="tg://user?id={}">{}</a></b>'.format(
                self._client.heroku_me.id,
                utils.escape_html(get_display_name(self._client.heroku_me)),
            )
            .replace("{", "")
            .replace("}", "")
        )
        build = utils.get_commit_url()
        _version = f"<i>{'.'.join(list(map(str, list(version.__version__))))}</i>"
        prefix = f"«<code>{utils.escape_html(self.get_prefix())}</code>»"

        platform = utils.get_named_platform()
        platform_emoji = utils.get_named_platform_emoji()

        for emoji, icon in [
            ("🍊", '<tg-emoji emoji-id="5449599833973203438">🧡</tg-emoji>'),
            ("🍇", '<tg-emoji emoji-id="5449468596952507859">💜</tg-emoji>'),
            ("😶‍🌫️", '<tg-emoji emoji-id="5370547013815376328">😶‍🌫️</tg-emoji>'),
            ("❓", '<tg-emoji emoji-id="5407025283456835913">📱</tg-emoji>'),
            ("🍀", '<tg-emoji emoji-id="5395325195542078574">🍀</tg-emoji>'),
            ("🦾", '<tg-emoji emoji-id="5386766919154016047">🦾</tg-emoji>'),
            ("🚂", '<tg-emoji emoji-id="5359595190807962128">🚂</tg-emoji>'),
            ("🐳", '<tg-emoji emoji-id="5431815452437257407">🐳</tg-emoji>'),
            ("🕶", '<tg-emoji emoji-id="5407025283456835913">📱</tg-emoji>'),
            ("🐈‍⬛", '<tg-emoji emoji-id="6334750507294262724">🐈‍⬛</tg-emoji>'),
            ("✌️", '<tg-emoji emoji-id="5469986291380657759">✌️</tg-emoji>'),
            ("💎", '<tg-emoji emoji-id="5471952986970267163">💎</tg-emoji>'),
            ("🛡", '<tg-emoji emoji-id="5282731554135615450">🌩</tg-emoji>'),
            ("🌼", '<tg-emoji emoji-id="5224219153077914783">❤️</tg-emoji>'),
            ("🎡", '<tg-emoji emoji-id="5226711870492126219">🎡</tg-emoji>'),
            ("🐧", '<tg-emoji emoji-id="5361541227604878624">🐧</tg-emoji>'),
            ("🧃", '<tg-emoji emoji-id="5422884965593397853">🧃</tg-emoji>'),
            ("🦅", '<tg-emoji emoji-id="5427286516797831670">🦅</tg-emoji>'),
            ("💻", '<tg-emoji emoji-id="5469825590884310445">💻</tg-emoji>'),
            ("🍏", '<tg-emoji emoji-id="5372908412604525258">🍏</tg-emoji>'),
        ]:
            platform_emoji = platform_emoji.replace(emoji, icon)
        data = {
            "me": me,
            "version": _version,
            "build": build,
            "prefix": prefix,
            "platform": platform,
            "platform_emoji": platform_emoji,
            "upd": upd,
            "python_ver": lib_platform.python_version(),
            "uptime": utils.formatted_uptime(),
            "cpu_usage": utils.get_cpu_usage(),
            "ram_usage": f"{utils.get_ram_usage()} MB",
            "branch": version.branch,
            "hostname": lib_platform.node(),
            "user": getpass.getuser(),
            "os": self._get_os_name() or self.strings("non_detectable"),
            "kernel": lib_platform.release(),
            "cpu": f"{psutil.cpu_count(logical=False)} ({psutil.cpu_count()}) core(-s); {psutil.cpu_percent()}% total",
            "ping": round((time.perf_counter_ns() - start) / 10**6, 3),
            "htl_ver": herokutl.__version__,
            "git_status": utils.get_git_status(),
        }
        template = self._get_effective_info_template()
        data = await utils.get_placeholders(data, template)

        try:
            return template.format(**data)
        except KeyError:
            logger.exception("Missing placeholder in custom_message")
            return DEFAULT_INFO_MESSAGE.format(**data)

    @loader.command()
    async def infocmd(self, message: Message):
        start = time.perf_counter_ns()
        banner_url, force_web_media = self._get_effective_banner()

        rendered = await self._render_info(start)

        if force_web_media and banner_url:
            try:
                response = await utils.run_sync(
                    requests.get,
                    banner_url,
                    timeout=20,
                )
                response.raise_for_status()

                photo = BytesIO(response.content)
                photo.name = "cat.jpg"

                await message.client.send_file(
                    message.to_id,
                    photo,
                    caption=rendered,
                    reply_to=getattr(message, "reply_to_msg_id", None),
                    force_document=False,
                    parse_mode="html",
                )
                with contextlib.suppress(Exception):
                    if getattr(message, "out", False):
                        await message.delete()
                return
            except Exception:
                logger.exception("Failed to send cat photo, falling back to plain info")

        media = str(banner_url)

        if banner_url and (self.config["quote_media"] is True or force_web_media):
            media = InputMediaWebPage(str(banner_url), optional=True)

        elif not banner_url:
            media = None

        try:
            match True:
                case _ if self._get_effective_info_template() == DEFAULT_INFO_MESSAGE:
                    await utils.answer(
                        message,
                        rendered,
                        file=media,
                        reply_to=getattr(message, "reply_to_msg_id", None),
                        invert_media=self.config["invert_media"],
                    )
                case _:
                    if "{ping}" in self._get_effective_info_template():
                        message = await utils.answer(message, self.config["ping_emoji"])
                    await utils.answer(
                        message,
                        rendered,
                        file=media,
                        reply_to=getattr(message, "reply_to_msg_id", None),
                        invert_media=self.config["invert_media"],
                    )
        except WebpageMediaEmptyError:
            await utils.answer(
                message,
                self.strings["no_banner"].format(
                    link=banner_url,
                ),
                reply_to=getattr(message, "reply_to_msg_id", None),
            )

    @loader.command()
    async def ubinfo(self, message: Message):
        await utils.answer(message, self.strings("desc"))
