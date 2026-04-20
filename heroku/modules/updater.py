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


import ast
import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import time
import typing

import aiohttp
import git
from git import GitCommandError, Repo
from herokutl.extensions.html import CUSTOM_EMOJIS
from herokutl.tl.functions.messages import (
    GetDialogFiltersRequest,
    UpdateDialogFilterRequest,
)
from herokutl.tl.types import DialogFilter, Message, TextWithEntities

from .. import loader, main, utils, version
from .._internal import restart
from ..inline.types import BotInlineCall, InlineCall

logger = logging.getLogger(__name__)
REPO_URL = "https://github.com/unsidogandon/ratko"
REPO_API_URL = "https://api.github.com/repos/unsidogandon/ratko"


def _is_no_git() -> bool:
    return os.environ.get("HEROKU_NO_GIT") == "1"


def _repo_path() -> str:
    return os.path.abspath(os.path.join(utils.get_base_dir(), ".."))


@loader.tds
class UpdaterMod(loader.Module):
    """Updates itself, tracks latest ratko releases, and notifies you if update is required"""

    strings = {"name": "Updater"}

    def __init__(self):
        self._notified = None
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "GIT_ORIGIN_URL",
                REPO_URL,
                lambda: self.strings("origin_cfg_doc"),
                validator=loader.validators.Link(),
            ),
            loader.ConfigValue(
                "disable_notifications",
                doc=lambda: self.strings("_cfg_doc_disable_notifications"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "autoupdate",
                False,
                doc=lambda: self.strings("_cfg_doc_autoupdate"),
                validator=loader.validators.Boolean(),
            ),
        )

    def _exteragram_text(self, text: str) -> str:
        return utils.replace_tg_emoji_tags(text, self._client)

    async def _set_autoupdate_state(self, call: BotInlineCall, state: bool):
        self.set("autoupdate", True)
        if not state:
            self.config["autoupdate"] = False
            await self.inline.bot(
                call.answer(
                    self.strings("autoupdate_off").format(prefix=self.get_prefix())
                )
            )
            return

        self.config["autoupdate"] = True

        await self.inline.bot(call.answer(self.strings("autoupdate_on")))

    def get_changelog(self) -> str:
        if _is_no_git() or not utils.is_git_repo():
            return False
        try:
            utils.run_git("fetch", "origin", version.branch, timeout=60)
            raw_diff = utils.run_git(
                "log",
                "--format=%H%x1f%s",
                f"HEAD..origin/{version.branch}",
                timeout=30,
            )
            if not raw_diff:
                return False
        except Exception:
            return False

        commits = [line for line in raw_diff.splitlines() if line.strip()]
        rendered = []
        total_limit = 360

        for commit in commits[:10]:
            if chr(31) not in commit:
                continue

            sha, text = commit.split(chr(31), 1)
            item = f"<b>{sha[:7]}</b>: <i>{utils.escape_html(text)}</i>"

            if sum(len(x) for x in rendered) + len(rendered) + len(item) > total_limit:
                break

            rendered.append(item)

        res = "\n".join(rendered)

        if len(rendered) < len([c for c in commits[:10] if chr(31) in c]):
            res += self.strings("more").format(
                len([c for c in commits if chr(31) in c]) - len(rendered)
            )
        elif len(commits) > 10:
            res += self.strings("more").format(len(commits) - 10)

        return res

    def get_latest(self) -> str:
        if _is_no_git() or not utils.is_git_repo():
            return ""
        return utils.run_git("rev-parse", f"origin/{version.branch}")

    @loader.loop(interval=60, autostart=True)
    async def poller_announcement(self):
        async with aiohttp.ClientSession() as session:
            try:
                url = "https://api.github.com/repos/coddrago/assets/contents/heroku/announcment.txt"
                r = await session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"Accept": "application/vnd.github.v3.raw"},
                )

                match r.status:
                    case 200:
                        announcement = (await r.text()).strip()
                        previous = self.get("announcement", "")
                        if announcement and announcement != previous:
                            await self.inline.bot.send_message(
                                self.tg_id,
                                self._exteragram_text(
                                    self.strings("announcement").format(announcement)
                                ),
                            )
                            self.set("announcement", announcement)
                    case _:
                        pass
            except Exception:
                pass

    @loader.loop(interval=60, autostart=True)
    async def poller(self):
        if _is_no_git():
            return
        if (
            self.config["disable_notifications"] and not self.config["autoupdate"]
        ) or not self.get_changelog():
            return

        self._pending = self.get_latest()

        if (
            self.get("ignore_permanent", False)
            and self.get("ignore_permanent") == self._pending
        ):
            await asyncio.sleep(60)
            return

        if self._pending not in {utils.get_git_hash(), self._notified}:
            if not self.config["autoupdate"]:
                manual_update = True
            else:
                try:
                    async with aiohttp.ClientSession() as session:
                        r = await session.get(
                            url=f"{REPO_API_URL}/contents/heroku/version.py?ref={version.branch}",
                            headers={"Accept": "application/vnd.github.v3.raw"},
                        )
                        text = await r.text()

                    new_version = ""
                    for line in text.splitlines():
                        if line.strip().startswith("__version__"):
                            new_version = ast.literal_eval(line.split("=")[1])

                    if version.__version__[0] == new_version[0]:
                        manual_update = False
                    else:
                        logger.info("Got a major update, updating manually")
                        manual_update = True
                except:
                    manual_update = True

            if manual_update:
                m = await self.inline.bot.send_photo(
                    self.tg_id,
                    "https://raw.githubusercontent.com/unsidogandon/ratko/main/banner.jpg",
                    caption=self._exteragram_text(
                        self.strings("update_required").format(
                            utils.get_git_hash()[:6],
                            f'<a href="{REPO_URL}/compare/{{}}...{{}}">{{}}</a>'.format(
                                utils.get_git_hash()[:12],
                                self.get_latest()[:12],
                                self.get_latest()[:6],
                            ),
                            self.get_changelog(),
                        )
                    ),
                    reply_markup=self._markup(),
                )

                self._notified = self._pending
                self.set("ignore_permanent", False)

                await self._delete_all_upd_messages()

                self.set("upd_msg", m.message_id)

            else:
                m = await self.inline.bot.send_photo(
                    self.tg_id,
                    "https://raw.githubusercontent.com/unsidogandon/ratko/main/banner.jpg",
                    caption=self._exteragram_text(
                        self.strings("autoupdate_notifier").format(
                            self.get_latest()[:6],
                            self.get_changelog(),
                            f'<a href="{REPO_URL}/compare/{{}}...{{}}">{{}}</a>'.format(
                                utils.get_git_hash()[:12],
                                self.get_latest()[:12],
                                "🔎 diff",
                            ),
                        )
                    ),
                )
                await self.invoke("update", "-f", peer=self.inline.bot_username)

    async def _delete_all_upd_messages(self):
        for client in self.allclients:
            with contextlib.suppress(Exception):
                await client.loader.inline.bot.delete_message(
                    client.tg_id,
                    client.loader.db.get("Updater", "upd_msg"),
                )

    @loader.callback_handler()
    async def update_call(self, call: InlineCall):
        """Process update buttons clicks"""
        if _is_no_git():
            await call.answer("Git disabled via --no-git.", show_alert=True)
            return
        if call.data not in {"heroku/update", "heroku/ignore_upd"}:
            return

        if call.data == "heroku/ignore_upd":
            self.set("ignore_permanent", self.get_latest())
            await self.inline.bot(call.answer(self.strings("latest_disabled")))
            return

        await self._delete_all_upd_messages()

        with contextlib.suppress(Exception):
            await call.delete()

        await self.invoke("update", "-f", peer=self.inline.bot_username)

    @loader.command()
    async def changelog(self, message: Message):
        """Shows the changelog of the last major update"""
        with open("CHANGELOG.md", mode="r", encoding="utf-8") as f:
            changelog = f.read().split("##")[1].strip()
        if (await self._client.get_me()).premium:
            changelog.replace(
                "🌑 Heroku",
                "<tg-emoji emoji-id=5192765204898783881>🌘</tg-emoji><tg-emoji emoji-id=5195311729663286630>🌘</tg-emoji><tg-emoji emoji-id=5195045669324201904>🌘</tg-emoji>",
            )

        await utils.answer(message, self.strings("changelog").format(changelog))

    @loader.command()
    async def restart(self, message: Message):
        args = utils.get_args_raw(message)
        secure_boot = any(trigger in args for trigger in {"--secure-boot", "-sb"})
        try:
            if (
                "-f" in args
                or not self.inline.init_complete
                or not await self.inline.form(
                    message=message,
                    text=self.strings(
                        "secure_boot_confirm" if secure_boot else "restart_confirm"
                    ),
                    reply_markup=[
                        {
                            "text": self.strings("btn_restart"),
                            "callback": self.inline_restart,
                            "args": (secure_boot,),
                            "style": "primary",
                        },
                        {
                            "text": self.strings("cancel"),
                            "action": "close",
                            "style": "danger",
                        },
                    ],
                )
            ):
                raise
        except Exception:
            await self.restart_common(message, secure_boot)

    async def inline_restart(self, call: InlineCall, secure_boot: bool = False):
        await self.restart_common(call, secure_boot=secure_boot)

    async def process_restart_message(self, msg_obj: typing.Union[InlineCall, Message]):
        self.set(
            "selfupdatemsg",
            (
                msg_obj.inline_message_id
                if hasattr(msg_obj, "inline_message_id")
                else f"{utils.get_chat_id(msg_obj)}:{msg_obj.id}"
            ),
        )

    async def restart_common(
        self,
        msg_obj: typing.Union[InlineCall, Message],
        secure_boot: bool = False,
    ):
        if (
            hasattr(msg_obj, "form")
            and isinstance(msg_obj.form, dict)
            and "uid" in msg_obj.form
            and msg_obj.form["uid"] in self.inline._units
            and "message" in self.inline._units[msg_obj.form["uid"]]
        ):
            message = self.inline._units[msg_obj.form["uid"]]["message"]
        else:
            message = msg_obj

        if secure_boot:
            self._db.set(loader.__name__, "secure_boot", True)

        msg_obj = await utils.answer(
            msg_obj,
            self.strings("restarting_caption").format(
                utils.get_platform_emoji()
                if self._client.heroku_me.premium
                else "ratko"
            ),
        )

        await self.process_restart_message(msg_obj)

        self.db.set("Updater", "modules_count", len(self.allmodules.modules))

        self.set("restart_ts", time.time())

        with contextlib.suppress(Exception):
            await main.heroku.web.stop()

        handler = logging.getLogger().handlers[0]
        handler.setLevel(logging.CRITICAL)

        for client in self.allclients:
            # Terminate main loop of all running clients
            # Won't work if not all clients are ready
            if client is not message.client:
                await client.disconnect()

        if "LAVHOST" in os.environ:
            await self.client.send_message("lavhostbot", "🔄 Restart")
            return

        await message.client.disconnect()
        restart()

    async def download_common(self):
        try:
            if not utils.is_git_repo():
                return False

            old_commit = utils.get_git_hash()
            utils.run_git("fetch", "origin", version.branch, check=True, timeout=120)
            utils.run_git(
                "pull",
                "--ff-only",
                "origin",
                version.branch,
                check=True,
                timeout=120,
            )
            new_commit = utils.get_git_hash()

            if not old_commit or not new_commit or old_commit == new_commit:
                return False

            return (
                "requirements.txt"
                in utils.run_git(
                    "diff",
                    "--name-only",
                    f"{old_commit}..{new_commit}",
                    "--",
                    "requirements.txt",
                    timeout=30,
                ).splitlines()
            )
        except subprocess.CalledProcessError:
            return False

    @staticmethod
    def req_common():
        # Now we have downloaded new code, install requirements
        logger.debug("Installing new requirements...")
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    os.path.join(
                        os.path.dirname(utils.get_base_dir()),
                        "requirements.txt",
                    ),
                    "--user",
                ],
                check=True,
                timeout=600,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logger.exception("Req install failed")

    @loader.command()
    async def update(self, message: Message):
        if _is_no_git():
            await utils.answer(
                message,
                "<b>Git disabled via --no-git.</b>",
            )
            return
        try:
            args = utils.get_args_raw(message)
            current = utils.get_git_hash()
            upcoming = self.get_latest()
            if (
                "-f" in args
                or not self.inline.init_complete
                or not await self.inline.form(
                    message=message,
                    text=(
                        self.strings("update_confirm").format(
                            current, current[:8], upcoming, upcoming[:8]
                        )
                        if upcoming != current
                        else self.strings("no_update")
                    ),
                    reply_markup=[
                        {
                            "text": self.strings("btn_update"),
                            "callback": self.inline_update,
                            "style": "primary",
                        },
                        {
                            "text": self.strings("cancel"),
                            "action": "close",
                            "style": "danger",
                        },
                    ],
                )
            ):
                raise
        except Exception:
            await self.inline_update(message)

    @loader.command()
    async def autoupdate(self, message: Message):
        """| switch autoupdate state"""
        self.config["autoupdate"] = not self.config["autoupdate"]
        if self.config["autoupdate"]:
            await utils.answer(message, self.strings["autoupdate_on"])
        else:
            await utils.answer(
                message, self.strings["autoupdate_off"].format(prefix=self.get_prefix())
            )

    async def inline_update(
        self,
        msg_obj: typing.Union[InlineCall, Message],
        hard: bool = False,
    ):
        # We don't really care about asyncio at this point, as we are shutting down
        if hard:
            os.system(f"cd {utils.get_base_dir()} && cd .. && git reset --hard HEAD")

        try:
            if "LAVHOST" in os.environ:
                msg_obj = await utils.answer(
                    msg_obj,
                    self.strings("restarting_caption").format(
                        utils.get_platform_emoji()
                        if self._client.heroku_me.premium
                        else "ratko"
                    ),
                )
                await self.process_restart_message(msg_obj)
                self.set("restart_ts", time.time())
                await self.client.send_message("lavhostbot", "/update")
                return

            with contextlib.suppress(Exception):
                msg_obj = await utils.answer(msg_obj, self.strings("downloading"))

            req_update = await self.download_common()

            with contextlib.suppress(Exception):
                msg_obj = await utils.answer(msg_obj, self.strings("installing"))

            if req_update:
                self.req_common()

            await self.restart_common(msg_obj)
        except (GitCommandError, subprocess.CalledProcessError):
            if not hard:
                await self.inline_update(msg_obj, True)
                return

            logger.critical("Got update loop. Update manually via .terminal")

    @loader.command()
    async def source(self, message: Message):
        await utils.answer(
            message,
            self.strings("source").format(self.config["GIT_ORIGIN_URL"]),
        )

    async def client_ready(self):
        if not utils.is_git_repo():
            os.environ["HEROKU_NO_GIT"] = "1"
            logger.info("Git repository not found, updater will run in no-git mode")

        self._markup = lambda: self.inline.generate_markup(
            [
                {
                    "text": self.strings("update"),
                    "data": "heroku/update",
                    "style": "primary",
                },
                {
                    "text": self.strings("ignore"),
                    "data": "heroku/ignore_upd",
                    "style": "danger",
                },
            ]
        )

        if self.get("selfupdatemsg") is not None:
            try:
                await self.update_complete()
            except Exception:
                logger.exception("Failed to complete update!")

        if self.get("do_not_create", False):
            pass
        else:
            try:
                await self._add_folder()
            except Exception:
                logger.exception("Failed to add folder!")

            self.set("do_not_create", True)

        if not self.inline.init_complete or not self.inline.bot:
            logger.info("Inline bot is not ready, skipping updater startup banner")
            return

        if not self.config["autoupdate"] and not self.get("autoupdate", False):
            await self.inline.bot.send_photo(
                self.tg_id,
                photo="https://raw.githubusercontent.com/unsidogandon/ratko/main/banner.jpg",
                caption=self._exteragram_text(self.strings("autoupdate")),
                reply_markup=self.inline.generate_markup(
                    [
                        [
                            {
                                "text": f"✅ Turn on",
                                "callback": self._set_autoupdate_state,
                                "args": (True,),
                                "style": "success",
                            }
                        ],
                        [
                            {
                                "text": "🚫 Turn off",
                                "callback": self._set_autoupdate_state,
                                "args": (False,),
                                "style": "danger",
                            }
                        ],
                    ]
                ),
            )

    async def _add_folder(self):
        folders = await self._client(GetDialogFiltersRequest())

        try:
            folder_id = (
                max(
                    (folder for folder in folders.filters if hasattr(folder, "id")),
                    key=lambda x: x.id,
                ).id
                + 1
            )
        except ValueError:
            folder_id = 2

        folders = await self._client(GetDialogFiltersRequest())
        filters = getattr(folders, "filters", folders)
        heroku_f = False

        if filters:
            for folder in filters:
                title = getattr(folder, "title", None)

                if title:
                    raw_title = getattr(title, "text", title)

                    if str(raw_title).strip() == "Heroku":
                        heroku_f = True

        if heroku_f is True:
            return
        else:
            try:
                await self._client(
                    UpdateDialogFilterRequest(
                        folder_id,
                        DialogFilter(
                            folder_id,
                            title=TextWithEntities(text="Heroku", entities=[]),
                            pinned_peers=(
                                [
                                    await self._client.get_input_entity(
                                        self._client.loader.inline.bot_id
                                    )
                                ]
                                if self._client.loader.inline.init_complete
                                else []
                            ),
                            include_peers=[
                                await self._client.get_input_entity(dialog.entity)
                                async for dialog in self._client.iter_dialogs(
                                    None,
                                    ignore_migrated=True,
                                )
                                if "heroku" in dialog.name
                                or "Heroku" in dialog.name
                                and dialog.is_channel
                                or (
                                    self._client.loader.inline.init_complete
                                    and dialog.entity.id
                                    == self._client.loader.inline.bot_id
                                )
                                or dialog.entity.id
                                in [
                                    2445389036,
                                    2341345589,
                                    2410964167,
                                ]  # official heroku chats
                            ],
                            emoticon="🐱",
                            exclude_peers=[],
                            contacts=False,
                            non_contacts=False,
                            groups=False,
                            broadcasts=False,
                            bots=False,
                            exclude_muted=False,
                            exclude_read=False,
                            exclude_archived=False,
                        ),
                    )
                )
            except Exception:
                logger.critical(
                    "Can't create Heroku folder. Possible reasons are:\n"
                    "- User reached the limit of folders in Telegram\n"
                    "- User got floodwait\n"
                    "Ignoring error and adding folder addition to ignore list\n",
                    exc_info=True,
                )

    async def update_complete(self):
        logger.debug("Self update successful! Edit message")
        start = self.get("restart_ts")
        try:
            took = round(time.time() - start)
        except Exception:
            took = "n/a"

        msg = self.strings("success").format(utils.ascii_face(), took)
        msg = self._exteragram_text(msg)
        ms = self.get("selfupdatemsg")

        if ":" in str(ms):
            chat_id, message_id = ms.split(":")
            chat_id, message_id = int(chat_id), int(message_id)
            await self._client.edit_message(chat_id, message_id, msg)
            return

        await self.inline.bot.edit_message_text(
            inline_message_id=ms,
            text=self.inline.sanitise_text(msg),
        )

    async def full_restart_complete(self, secure_boot: bool = False):
        start = self.get("restart_ts")

        try:
            took = round(time.time() - start)
        except Exception:
            took = "n/a"

        self.set("restart_ts", None)
        ms = self.get("selfupdatemsg")

        modules_count = self.db.get("Updater", "modules_count")
        try:
            modules_count = int(modules_count)
        except Exception:
            modules_count = len(self.allmodules.modules)

        if modules_count <= len(self.allmodules.modules):
            msg = self.strings(
                "secure_boot_complete" if secure_boot else "full_success"
            ).format(utils.ascii_face(), took)
        else:
            fails = modules_count - len(self.allmodules.modules)
            msg = self.strings(
                "secure_boot_fail" if secure_boot else "full_fail"
            ).format(utils.ascii_face(), took, fails)

        msg = self._exteragram_text(msg)

        if ms is None:
            return

        self.set("selfupdatemsg", None)

        if ":" in str(ms):
            chat_id, message_id = ms.split(":")
            chat_id, message_id = int(chat_id), int(message_id)
            await self._client.edit_message(chat_id, message_id, msg)
            await asyncio.sleep(60)
            await self._client.delete_messages(chat_id, message_id)
            return

        await self.inline.bot.edit_message_text(
            inline_message_id=ms,
            text=self.inline.sanitise_text(msg),
        )

    @loader.command()
    async def rollback(self, message: Message):
        if not (args := utils.get_args_raw(message)).isdigit():
            await utils.answer(message, self.strings("invalid_args"))
            return
        if int(args) > 10:
            await utils.answer(message, self.strings("rollback_too_far"))
            return
        form = await self.inline.form(
            message=message,
            text=self.strings("rollback_confirm").format(num=args),
            reply_markup=[
                [
                    {
                        "text": "✅",
                        "callback": self.rollback_confirm,
                        "args": [args],
                        "style": "success",
                    }
                ],
                [
                    {
                        "text": "❌",
                        "action": "close",
                        "style": "danger",
                    }
                ],
            ],
        )

    async def rollback_confirm(self, call: InlineCall, number: int):
        await utils.answer(call, self.strings("rollback_process").format(num=number))
        await asyncio.create_subprocess_shell(
            f"git reset --hard HEAD~{number}", stdout=asyncio.subprocess.PIPE
        )
        await self.restart_common(call)

    @loader.command()
    async def ubstop(self, message: Message):
        """| stops your userbot"""

        if "LAVHOST" in os.environ:
            await utils.answer(
                message,
                self.strings["ub_stop"].format(emoji=utils.get_platform_emoji()),
            )
            await self.client.send_message("lavhostbot", "⏹ Stop")
        else:
            await utils.answer(
                message,
                self.strings["ub_stop"].format(emoji=utils.get_platform_emoji()),
            )
            exit()
