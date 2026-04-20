# ©️ Codrago, 2024-2030
# This file is a part of Heroku Userbot
# 🌐 https://github.com/coddrago/Heroku
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import logging
import os
import subprocess
import typing

import herokutl

from .. import version

parser = herokutl.utils.sanitize_parse_mode("html")
logger = logging.getLogger(__name__)
REPO_URL = "https://github.com/unsidogandon/ratko"


def _repo_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def run_git(*args: str, check: bool = False, timeout: int = 15) -> str:
    try:
        process = subprocess.run(
            ["git", "-C", _repo_path(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        if check:
            raise
        return ""

    if process.returncode != 0:
        if check:
            raise subprocess.CalledProcessError(
                process.returncode,
                process.args,
                output=process.stdout,
                stderr=process.stderr,
            )
        return ""

    return process.stdout.strip()


def is_git_repo() -> bool:
    return bool(run_git("rev-parse", "--show-toplevel"))


def _is_no_git() -> bool:
    return os.environ.get("HEROKU_NO_GIT") == "1"


# GeekTG Compatibility
def get_git_info() -> typing.Tuple[str, str]:
    """
    Get git info
    :return: Git info
    """
    if _is_no_git():
        return ("", "")
    hash_ = get_git_hash()
    return (
        hash_,
        f"{REPO_URL}/commit/{hash_}" if hash_ else "",
    )


def get_git_hash() -> str:
    """
    Get current Heroku git hash
    :return: Git commit hash
    """
    if _is_no_git():
        return ""
    return run_git("rev-parse", "HEAD")


def get_commit_url() -> str:
    """
    Get current Heroku git commit url
    :return: Git commit url
    """
    if _is_no_git():
        return "Unknown"
    try:
        hash_ = get_git_hash()
        if not hash_:
            return "Unknown"
        return f'<a href="{REPO_URL}/commit/{hash_}">#{hash_[:7]}</a>'
    except Exception:
        return "Unknown"


def get_git_status() -> str:
    """
    :return: 'Clean' or 'X files modified'.
    """
    if _is_no_git():
        return "Git disabled"
    try:
        output = run_git("status", "--porcelain", timeout=5)
        if output == "" and not is_git_repo():
            return "Not a Git repo"

        if not output:
            return "Clean"

        count = len(output.splitlines())
        word = "file" if count == 1 else "files"
        return f"{count} {word} modified"

    except subprocess.TimeoutExpired:
        return "Unknown"
    except Exception:
        return "Unknown"


def get_last_commit_message() -> str:
    """
    Get the message of the last commit
    :return: Last commit message
    """
    if _is_no_git():
        return "Unknown"
    return run_git("log", "-1", "--pretty=%B") or "Unknown"


def get_commit_count() -> int:
    """
    Get the total number of commits in the repository
    :return: Number of commits
    """
    if _is_no_git():
        return 0
    try:
        return int(run_git("rev-list", "--count", "HEAD") or 0)
    except Exception:
        return 0


def is_up_to_date():
    if _is_no_git() or not is_git_repo():
        return False
    return not bool(run_git("rev-list", "--max-count=1", f"HEAD..origin/{version.branch}"))
