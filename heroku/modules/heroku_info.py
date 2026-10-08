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

import getpass
import logging
import platform as lib_platform
import random
import re
import time
from io import BytesIO

import herokutl
import psutil
import requests

from herokutl.errors import WebpageMediaEmptyError
from herokutl.tl.types import Message
from herokutl.types import InputMediaWebPage
from herokutl.utils import get_display_name

from .. import loader, utils, version

logger = logging.getLogger(__name__)

DEFAULT_INFO_BANNER = (
    "https://raw.githubusercontent.com/unsidogandon/ratko/main/banner.jpg"
)
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
    "своп: {swap_usage}\n"
    "ос: {os}\n"
    "ядрышко: {kernel}\n"
    "проц: {cpu}</blockquote>"
)
MAX_CAT_IMAGE_SIZE = 10 * 1024 * 1024


@loader.tds
class HerokuInfoMod(loader.Module):
    """Show userbot info"""

    strings = {"name": "RatkoInfo"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "custom_message",
                DEFAULT_INFO_MESSAGE,
                doc=lambda: (
                    self.strings["_cfg_cst_msg"]
                    + "\n"
                    + (
                        "\n"
                        + self.strings["_cfg_cst_ph"].format(
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
                lambda: self.strings["_cfg_banner"],
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
                "☃️",
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
            loader.ConfigValue(
                "rich_mode",
                True,
                lambda: self.strings["_cfg_rich_mode"],
                validator=loader.validators.Boolean(),
            ),
        )

    def _get_os_name(self):
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME"):
                        return line.split("=")[1].strip().strip('"')
        except FileNotFoundError:
            return self.strings["non_detectable"]

    def _get_effective_info_template(
        self, template_key: str = "info_message"
    ) -> str:
        if template_key == "rich_info_message":
            custom_message = self.config["custom_message"]
            if not custom_message or custom_message == DEFAULT_INFO_MESSAGE:
                return self.strings["rich_info_message"]

            return custom_message

        return self.config["custom_message"] or DEFAULT_INFO_MESSAGE

    def _get_effective_banner(self) -> tuple[str | None, bool]:
        if self.config.get("random_cats"):
            return f"{random.choice(CAT_BANNERS)}&t={time.time_ns()}", True

        return self.config["banner_url"], False

    @staticmethod
    def _download_cat(url: str) -> bytes:
        with requests.get(url, stream=True, timeout=(5, 20)) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/"):
                raise ValueError("Random cat response is not an image")

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    content_length = int(content_length)
                except ValueError:
                    content_length = None

                if content_length and content_length > MAX_CAT_IMAGE_SIZE:
                    raise ValueError("Random cat image exceeds 10 MiB")

            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if len(body) + len(chunk) > MAX_CAT_IMAGE_SIZE:
                    raise ValueError("Random cat image exceeds 10 MiB")
                body.extend(chunk)

        return bytes(body)

    @staticmethod
    def _get_runtime_info() -> dict:
        """Collect blocking Git and CPU metrics outside the event loop."""
        try:
            up_to_date = utils.is_up_to_date()
        except Exception:
            up_to_date = None
        return {
            "up_to_date": up_to_date,
            "build": utils.get_commit_url(),
            "cpu_usage": utils.get_cpu_usage(),
            "git_status": utils.get_git_status(),
        }

    async def _render_info(
        self,
        start: float,
        template_key: str = "info_message",
    ) -> str:
        runtime = await utils.run_sync(self._get_runtime_info)
        try:
            if runtime["up_to_date"] is None:
                upd = ""
            elif runtime["up_to_date"]:
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
        build = runtime["build"]
        _version = f'<i>{".".join(list(map(str, list(version.__version__))))}</i>'
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
        swap = utils.get_swap_usage()
        swap_usage = (
            swap["error"]
            if "error" in swap
            else f"{swap['used']} MB / {swap['total']} MB ({swap['percent']}%)"
        )
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
            "cpu_usage": runtime["cpu_usage"],
            "ram_usage": f"{utils.get_ram_usage()} MB",
            "swap_usage": swap_usage,
            "branch": version.branch,
            "hostname": lib_platform.node(),
            "user": getpass.getuser(),
            "os": self._get_os_name() or self.strings["non_detectable"],
            "kernel": lib_platform.release(),
            "cpu": f"{psutil.cpu_count(logical=False)} ({psutil.cpu_count()}) core(-s); {psutil.cpu_percent()}% total",
            "ping": round((time.perf_counter_ns() - start) / 10**6, 3),
            "htl_ver": herokutl.__version__,
            "git_status": runtime["git_status"],
        }
        rich_banner_url = None
        if template_key == "rich_info_message":
            rich_banner_url, _ = self._get_effective_banner()
            data["banner_url"] = (
                utils.escape_html(str(rich_banner_url)) if rich_banner_url else ""
            )
            data["img"] = (
                f'<img src="{data["banner_url"]}"/>'
                if rich_banner_url
                else ""
            )
        template = self._get_effective_info_template(template_key)
        data = await utils.get_placeholders(data, template)

        try:
            rendered = template.format(**data)
        except KeyError:
            logger.exception("Missing placeholder in custom_message")
            return DEFAULT_INFO_MESSAGE.format(**data)

        if template_key == "rich_info_message" and not rich_banner_url:
            # Баннер не настроен: выкидываем пустой <figure><img src=""/></figure>
            # из rich-шаблона (кастомные <figure> с реальной картинкой не трогаем)
            rendered = re.sub(
                r"<figure>\s*<img\b[^>]*?src=\"\"[^>]*>\s*</figure>",
                "",
                rendered,
            )

        return rendered

    @loader.command()
    async def infocmd(self, message: Message):
        start = time.perf_counter_ns()

        if self.config["rich_mode"]:
            await utils.answer_with_media_fallback(
                message,
                rich_message=await self._render_info(
                    start,
                    template_key="rich_info_message",
                ),
                reply_to=getattr(message, "reply_to_msg_id", None),
            )
            return

        banner_url, random_cat = self._get_effective_banner()
        rendered = await self._render_info(start)
        media = str(banner_url)
        cat_downloaded = False

        if random_cat and banner_url:
            try:
                content = await utils.run_sync(self._download_cat, banner_url)
                photo = BytesIO(content)
                photo.name = "cat.jpg"
                media = photo
                cat_downloaded = True
            except Exception:
                logger.exception("Failed to download cat image, falling back to web media")

        if banner_url and not cat_downloaded and (
            self.config["quote_media"] is True or random_cat
        ):
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
        await utils.answer(message, self.strings["desc"])
