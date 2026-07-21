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
import errno
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import typing
from pathlib import Path

import aiohttp
import git
from git import GitCommandError, Repo
from herokutl.tl.functions.messages import (
    GetDialogFiltersRequest,
    UpdateDialogFilterRequest,
)
from herokutl.tl.types import (
    DialogFilter,
    InputBotInlineMessageID,
    InputBotInlineMessageID64,
    Message,
    TextWithEntities,
)

from .. import loader, main, utils, version
from .._internal import restart
from ..inline.types import BotInlineCall, InlineCall

logger = logging.getLogger(__name__)
NO_GIT = version.NO_GIT

os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GIT_ASKPASS"] = "echo"


@loader.tds
class UpdaterMod(loader.Module):
    """Updates Ratko and notifies you when an update is available"""

    strings = {"name": "Updater"}
    _GIT_FETCH_INTERVAL = 300
    _EMFILE_FETCH_BACKOFF = 900

    def __init__(self):
        self._notified = None
        self._last_emfile_warning = 0.0
        self._last_git_fetch = 0.0
        self._git_fetch_backoff_until = 0.0
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "GIT_ORIGIN_URL",
                version.REPO_URL,
                lambda: self.strings["origin_cfg_doc"],
                validator=loader.validators.Link(),
            ),
            loader.ConfigValue(
                "GIT_BRANCH",
                os.environ.get("RATKO_BRANCH", version.DEFAULT_BRANCH),
                lambda: "Git branch used for Ratko updates",
                validator=loader.validators.RegExp(r"^[A-Za-z0-9._/-]+$"),
            ),
            loader.ConfigValue(
                "disable_notifications",
                doc=lambda: self.strings["_cfg_doc_disable_notifications"],
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "autoupdate",
                False,
                doc=lambda: self.strings["_cfg_doc_autoupdate"],
                validator=loader.validators.Boolean(),
            ),
        )

    def _exteragram_text(self, text: str) -> str:
        return utils.replace_tg_emoji_tags(text, self._client)

    @property
    def _target_ref(self) -> str:
        return f"origin/{self.config['GIT_BRANCH']}"

    @property
    def _repo_url(self) -> str:
        return self.config["GIT_ORIGIN_URL"].rstrip("/").removesuffix(".git")

    @property
    def _github_api_url(self) -> str | None:
        prefix = "https://github.com/"
        if not self._repo_url.startswith(prefix):
            return None
        return f"https://api.github.com/repos/{self._repo_url.removeprefix(prefix)}"

    async def _set_autoupdate_state(self, call: BotInlineCall, state: bool):
        self.set("autoupdate_answered", True)
        self.config["autoupdate"] = state

        text = (
            self.strings["autoupdate_on"]
            if state
            else self.strings["autoupdate_off"].format(prefix=self.get_prefix())
        )

        await self.inline.bot(call.answer(text, show_alert=True))
        await call.delete()

    @staticmethod
    def _is_emfile_error(error: BaseException) -> bool:
        current: BaseException | None = error
        while current is not None:
            if isinstance(current, OSError) and current.errno in (errno.EMFILE, errno.EAGAIN):
                return True

            current = current.__cause__ or current.__context__

        return False

    def _log_git_poll_error(self, error: Exception):
        if self._is_emfile_error(error):
            now = time.monotonic()
            self._git_fetch_backoff_until = max(
                self._git_fetch_backoff_until,
                now + self._EMFILE_FETCH_BACKOFF,
            )
            if now - self._last_emfile_warning >= 300:
                logger.warning(
                    "Failed to build changelog: too many open files; "
                    "pausing remote fetch attempts"
                )
                self._last_emfile_warning = now
        else:
            logger.exception("Failed to build changelog")

    def _format_changelog(self, commits: list[typing.Any]) -> str:
        entries = []
        for commit in commits[:10]:
            message = commit.message
            if isinstance(message, bytes):
                message = message.decode(errors="replace")

            title = message.splitlines()[0] if message.splitlines() else commit.hexsha
            entries.append(
                f"<b>{commit.hexsha[:7]}</b>:" f" <i>{utils.escape_html(title)}</i>"
            )

        res = "\n".join(entries)

        if len(commits) > 10:
            res += self.strings["more"].format(len(commits) - 10)

        return res

    def _get_update_state(self) -> tuple[str, str, str | typing.Literal[False]]:
        with git.Repo() as repo:
            origin = repo.remote("origin")
            now = time.monotonic()
            if now >= self._git_fetch_backoff_until:
                if now - self._last_git_fetch >= self._GIT_FETCH_INTERVAL:
                    logger.debug("Fetching changelog from %s", origin.url)
                    subprocess.run(
                        [
                            "git",
                            "fetch",
                            "--quiet",
                            "origin",
                            (
                                f"+refs/heads/{self.config['GIT_BRANCH']}:"
                                f"refs/remotes/{self._target_ref}"
                            ),
                        ],
                        cwd=repo.working_dir,
                        timeout=60,
                        capture_output=True,
                        check=False,
                    )
                    self._last_git_fetch = now
            else:
                logger.debug(
                    "Skipping changelog fetch for %.0f more seconds after EMFILE",
                    self._git_fetch_backoff_until - now,
                )

            current = repo.head.commit.hexsha
            latest = next(repo.iter_commits(self._target_ref, max_count=1)).hexsha
            commits = [*repo.iter_commits(f"HEAD..{self._target_ref}")]

            return (
                current,
                latest,
                self._format_changelog(commits) if commits else False,
            )

    def get_changelog(self) -> str | typing.Literal[False]:
        if NO_GIT:
            return False
        try:
            return self._get_update_state()[2]
        except Exception as e:
            self._log_git_poll_error(e)
            return False

    def get_latest(self) -> str:
        if NO_GIT:
            return ""
        try:
            with git.Repo() as repo:
                return next(repo.iter_commits(self._target_ref, max_count=1)).hexsha
        except Exception:
            return ""

    @loader.loop(interval=60, autostart=True)
    async def poller(self):
        if NO_GIT:
            return
        try:
            current, self._pending, changelog = self._get_update_state()
        except Exception as e:
            self._log_git_poll_error(e)
            return

        if (
            self.config["disable_notifications"] and not self.config["autoupdate"]
        ) or not changelog:
            return

        if (
            self.get("ignore_permanent", False)
            and self.get("ignore_permanent") == self._pending
        ):
            await asyncio.sleep(60)
            return

        if self._pending not in {current, self._notified}:
            if not self.config["autoupdate"]:
                manual_update = True
            else:
                try:
                    async with aiohttp.ClientSession() as session:
                        if not self._github_api_url:
                            raise RuntimeError("Automatic major updates require GitHub")
                        r = await session.get(
                            url=(
                                f"{self._github_api_url}/contents/heroku/version.py"
                                f"?ref={self.config['GIT_BRANCH']}"
                            ),
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
                except Exception:
                    manual_update = True

            if manual_update:
                m = await self.inline.bot.send_photo(
                    self.tg_id,
                    f"{version.REPO_URL}/raw/{version.DEFAULT_BRANCH}/banner.jpg",
                    caption=self._exteragram_text(
                        self.strings["update_required"].format(
                            current[:6],
                            f'<a href="{self._repo_url}/compare/{{}}...{{}}">{{}}</a>'.format(
                                current[:12],
                                self._pending[:12],
                                self._pending[:6],
                            ),
                            changelog,
                        ),
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
                    f"{version.REPO_URL}/raw/{version.DEFAULT_BRANCH}/banner.jpg",
                    caption=self._exteragram_text(
                        self.strings["autoupdate_notifier"].format(
                            self._pending[:6],
                            changelog,
                            f'<a href="{self._repo_url}/compare/{{}}...{{}}">{{}}</a>'.format(
                                current[:12],
                                self._pending[:12],
                                "🔎 diff",
                            ),
                        ),
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
        if NO_GIT:
            await call.answer("Git disabled via --no-git.", show_alert=True)
            return
        if call.data not in {"heroku/update", "heroku/ignore_upd"}:
            return

        if call.data == "heroku/ignore_upd":
            self.set("ignore_permanent", self.get_latest())
            await self.inline.bot(call.answer(self.strings["latest_disabled"]))
            return

        await self._delete_all_upd_messages()

        with contextlib.suppress(Exception):
            await call.delete()

        await self.invoke("update", "-f", peer=self.inline.bot_username)

    @loader.command()
    async def changelog(self, message: Message):
        """Shows the changelog of the last major update"""
        with open("CHANGELOG.md", encoding="utf-8") as f:
            changelog = f.read().split("##")[1].strip()
        if (await self._client.get_me()).premium:
            changelog.replace(
                "Ratko",
                "<tg-emoji emoji-id=5192765204898783881>🌘</tg-emoji><tg-emoji emoji-id=5195311729663286630>🌘</tg-emoji><tg-emoji emoji-id=5195045669324201904>🌘</tg-emoji>",
            )

        await utils.answer(message, self.strings["changelog"].format(changelog))

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
                    text=self.strings[
                        "secure_boot_confirm" if secure_boot else "restart_confirm"
                    ],
                    reply_markup=[
                        {
                            "text": self.strings["btn_restart"],
                            "callback": self.inline_restart,
                            "args": (secure_boot,),
                            "style": "primary",
                        },
                        {
                            "text": self.strings["cancel"],
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

    @staticmethod
    def _serialize_inline_message_id(
        inline_message_id: str | InputBotInlineMessageID | InputBotInlineMessageID64,
    ) -> str:
        if isinstance(
            inline_message_id,
            (InputBotInlineMessageID, InputBotInlineMessageID64),
        ):
            return typing.cast(str, inline_message_id.to_json())

        return inline_message_id

    @staticmethod
    def _deserialize_inline_message_id(
        inline_message_id: str,
    ) -> str | InputBotInlineMessageID | InputBotInlineMessageID64:
        try:
            data = json.loads(inline_message_id)
        except (TypeError, ValueError):
            return inline_message_id

        if not isinstance(data, dict):
            return inline_message_id

        if data.get("_") == "InputBotInlineMessageID":
            return InputBotInlineMessageID(
                dc_id=data["dc_id"],
                id=data["id"],
                access_hash=data["access_hash"],
            )

        if data.get("_") == "InputBotInlineMessageID64":
            return InputBotInlineMessageID64(
                dc_id=data["dc_id"],
                owner_id=data["owner_id"],
                id=data["id"],
                access_hash=data["access_hash"],
            )

        return inline_message_id

    @staticmethod
    def _parse_legacy_update_message_ref(
        message_ref: typing.Any,
    ) -> tuple[int, int] | None:
        if not isinstance(message_ref, str):
            return None

        parts = message_ref.split(":")
        if len(parts) != 2:
            return None

        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    async def process_restart_message(self, msg_obj: InlineCall | Message):
        inline_message_id = getattr(msg_obj, "inline_message_id", None)
        self.set(
            "selfupdatemsg",
            (
                self._serialize_inline_message_id(inline_message_id)
                if inline_message_id is not None
                else f"{utils.get_chat_id(msg_obj)}:{msg_obj.id}"
            ),
        )

    async def restart_common(
        self,
        msg_obj: InlineCall | Message,
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
            self.strings["restarting_caption"].format(
                utils.get_platform_emoji()
                if self._client.heroku_me.premium
                else "Ratko"
            ),
        )

        await self.process_restart_message(msg_obj)

        self.db.set("Updater", "modules_count", len(self.allmodules.modules))

        self.set("restart_ts", time.time())

        handler = logging.getLogger().handlers[0]
        handler.setLevel(logging.CRITICAL)

        for client in self.allclients:
            # Terminate main loop of all running clients
            # Won't work if not all clients are ready
            if client is not message.client:
                await client.disconnect()

        await message.client.disconnect()
        restart()

    async def download_common(self):
        def _sync():
            repo_root = Path(version.__file__).resolve().parent.parent
            with Repo(repo_root) as repo:
                if repo.is_dirty(untracked_files=False):
                    raise RuntimeError("Tracked local changes prevent a safe update")

                origin = repo.remote("origin")
                branch = self.config["GIT_BRANCH"]
                logger.debug("Fetching %s from %s", branch, origin.url)
                subprocess.run(
                    ["git", "check-ref-format", "--branch", branch],
                    cwd=repo_root,
                    timeout=10,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "fetch",
                        "--quiet",
                        "origin",
                        f"+refs/heads/{branch}:refs/remotes/{self._target_ref}",
                    ],
                    cwd=repo_root,
                    timeout=120,
                    capture_output=True,
                    check=True,
                )

                current = repo.head.commit.hexsha
                target = repo.commit(self._target_ref).hexsha
                if current == target:
                    return False

                try:
                    repo.git.merge_base("--is-ancestor", current, target)
                except GitCommandError as e:
                    raise RuntimeError(
                        "Local commits diverged from the Ratko update branch"
                    ) from e

                requirements_changed = bool(
                    repo.git.diff(
                        "--name-only",
                        f"{current}..{target}",
                        "--",
                        "requirements.txt",
                    ).strip()
                )
                if requirements_changed:
                    requirements = repo.git.show(f"{target}:requirements.txt")
                    if not self.req_common(requirements):
                        raise RuntimeError("Requirements installation failed")

                subprocess.run(
                    ["git", "merge", "--ff-only", self._target_ref],
                    cwd=repo_root,
                    timeout=30,
                    capture_output=True,
                    check=True,
                )
                if requirements_changed:
                    requirements_hash = hashlib.sha256(
                        (repo_root / "requirements.txt").read_bytes()
                    ).hexdigest()
                    main._atomic_write_text(
                        repo_root / ".requirements_hash",
                        requirements_hash,
                    )

                return False

        return await asyncio.wait_for(
            asyncio.to_thread(_sync),
            timeout=900,
        )

    @staticmethod
    def req_common(requirements: str | None = None) -> bool:
        # Now we have downloaded new code, install requirements
        logger.debug("Installing new requirements...")
        temporary = None
        try:
            if requirements is None:
                requirements_path = os.path.join(
                    os.path.dirname(utils.get_base_dir()),
                    "requirements.txt",
                )
            else:
                descriptor, temporary = tempfile.mkstemp(
                    prefix="ratko-requirements-",
                    suffix=".txt",
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                    file.write(requirements)
                requirements_path = temporary

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    requirements_path,
                ],
                check=True,
                timeout=600,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logger.exception("Req install failed")
            return False
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    @loader.command()
    async def update(self, message: Message):
        if NO_GIT:
            await utils.answer(
                message,
                "<b>Git disabled via --no-git.</b>",
            )
            return
        try:
            args = utils.get_args_raw(message)
            current = utils.get_git_hash() or ""
            with git.Repo() as repo:
                upcoming = next(repo.iter_commits(self._target_ref, max_count=1)).hexsha
            if (
                "-f" in args
                or not self.inline.init_complete
                or not await self.inline.form(
                    message=message,
                    text=(
                        self.strings["update_confirm"].format(
                            current, current[:8], upcoming, upcoming[:8]
                        )
                        if upcoming != current
                        else self.strings["no_update"]
                    ),
                    reply_markup=[
                        {
                            "text": self.strings["btn_update"],
                            "callback": self.inline_update,
                            "style": "primary",
                        },
                        {
                            "text": self.strings["cancel"],
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
        msg_obj: InlineCall | Message,
    ):
        # We don't really care about asyncio at this point, as we are shutting down
        try:
            with contextlib.suppress(Exception):
                msg_obj = await utils.answer(msg_obj, self.strings["downloading"])

            await self.download_common()

            with contextlib.suppress(Exception):
                msg_obj = await utils.answer(msg_obj, self.strings["installing"])

            await self.restart_common(msg_obj)
        except Exception as e:
            logger.exception("Ratko update failed")
            self._notified = getattr(self, "_pending", None)
            if self.config["autoupdate"]:
                self.config["autoupdate"] = False
            with contextlib.suppress(Exception):
                await utils.answer(
                    msg_obj,
                    "<b>Update failed without changing local files:</b> "
                    f"<code>{utils.escape_html(str(e))}</code>",
                )

    @loader.command()
    async def source(self, message: Message):
        await utils.answer(
            message,
            self.strings["source"].format(self.config["GIT_ORIGIN_URL"]),
        )

    async def client_ready(self):
        configured_url = self.config["GIT_ORIGIN_URL"].rstrip("/").removesuffix(".git")
        if configured_url == "https://github.com/coddrago/Heroku":
            self.config["GIT_ORIGIN_URL"] = version.REPO_URL

        if not NO_GIT:
            try:
                with git.Repo() as repo:
                    origin = repo.remote("origin")
                    target_url = self.config["GIT_ORIGIN_URL"]
                    if origin.url.rstrip("/").removesuffix(".git") != target_url.rstrip(
                        "/"
                    ).removesuffix(".git"):
                        origin.set_url(target_url)
            except Exception as e:
                raise loader.LoadError("Can't load due to repo init error") from e

        if not self.get("autoupdate_answered"):
            self.set("autoupdate_answered", self.get("autoupdate", False))

        self._markup = lambda: self.inline.generate_markup(
            [
                {
                    "text": self.strings["update"],
                    "data": "heroku/update",
                    "style": "primary",
                },
                {
                    "text": self.strings["ignore"],
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

        if (
            self.inline.init_complete
            and self.inline.bot
            and not self.config["autoupdate"]
            and not self.get("autoupdate_answered", False)
        ):
            await self.inline.bot.send_photo(
                self.tg_id,
                photo=f"{version.REPO_URL}/raw/{version.DEFAULT_BRANCH}/banner.jpg",
                caption=self._exteragram_text(self.strings["autoupdate"]),
                reply_markup=self.inline.generate_markup(
                    [
                        [
                            {
                                "text": "✅ Turn on",
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
        ratko_folder_exists = False

        if filters:

            for folder in filters:
                title = getattr(folder, "title", None)

                if title:
                    raw_title = getattr(title, "text", title)

                    if str(raw_title).strip() in {"Heroku", "Ratko"}:
                        ratko_folder_exists = True

        if ratko_folder_exists:
            return
        else:
            try:
                await self._client(
                    UpdateDialogFilterRequest(
                        folder_id,
                        DialogFilter(
                            folder_id,
                            title=TextWithEntities(text="Ratko", entities=[]),
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
                                if (
                                    dialog.is_channel
                                    and dialog.name
                                    and any(
                                        name in dialog.name.lower()
                                        for name in ("heroku", "ratko")
                                    )
                                )
                                or (
                                    self._client.loader.inline.init_complete
                                    and dialog.entity.id
                                    == self._client.loader.inline.bot_id
                                )
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
                    "Can't create Ratko folder. Possible reasons are:\n"
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

        msg = self.strings["success"].format(utils.ascii_face(), took)
        msg = self._exteragram_text(msg)
        ms = self.get("selfupdatemsg")

        if legacy_message_ref := self._parse_legacy_update_message_ref(ms):
            chat_id, message_id = legacy_message_ref
            await self._client.edit_message(chat_id, message_id, msg)
            return

        await self.inline.bot.edit_message_text(
            inline_message_id=self._deserialize_inline_message_id(str(ms)),
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
            msg = self.strings[
                "secure_boot_complete" if secure_boot else "full_success"
            ].format(utils.ascii_face(), took)
        else:
            fails = modules_count - len(self.allmodules.modules)
            msg = self.strings[
                "secure_boot_fail" if secure_boot else "full_fail"
            ].format(utils.ascii_face(), took, fails)

        msg = self._exteragram_text(msg)

        if ms is None:
            return

        self.set("selfupdatemsg", None)

        if legacy_message_ref := self._parse_legacy_update_message_ref(ms):
            chat_id, message_id = legacy_message_ref
            await self._client.edit_message(chat_id, message_id, msg)
            await asyncio.sleep(60)
            await self._client.delete_messages(chat_id, message_id)
            return

        await self.inline.bot.edit_message_text(
            inline_message_id=self._deserialize_inline_message_id(str(ms)),
            text=self.inline.sanitise_text(msg),
        )

    @loader.command()
    async def rollback(self, message: Message):
        if not (args := utils.get_args_raw(message)).isdigit():
            await utils.answer(message, self.strings["invalid_args"])
            return
        if int(args) > 10:
            await utils.answer(message, self.strings["rollback_too_far"])
            return
        await self.inline.form(
            message=message,
            text=self.strings["rollback_confirm"].format(num=args),
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
        await utils.answer(call, self.strings["rollback_process"].format(num=number))
        utils.ensure_child_watcher()
        await asyncio.create_subprocess_shell(
            f"git reset --hard HEAD~{number}", stdout=asyncio.subprocess.PIPE
        )
        await self.restart_common(call)

    async def ubstop_func(self, call: Message | InlineCall):
        await utils.answer(
            call,
            self.strings["ub_stop"].format(emoji=utils.get_platform_emoji()),
        )

        exit()

    @loader.command()
    async def ubstop(self, message: Message):
        """| stops your userbot"""

        args = utils.get_args(message)
        if "-f" in args or "--force" in args:
            await self.ubstop_func(message)
            return

        await self.inline.form(
            message=message,
            text=self.strings["stop_ub_confirm"].format(
                utils.get_platform_emoji()
                if self.client.heroku_me.premium
                else "Ratko"
            ),
            reply_markup=[
                [
                    {
                        "text": "✅",
                        "callback": self.ubstop_func,
                        "style": "primary",
                    },
                ],
                [{"text": "❌", "action": "close", "style": "primary"}],
            ],
            silent=True,
        )
