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
import atexit
import logging
import os
import random
import signal
import sys
from collections.abc import Callable
from urllib.parse import unquote, urljoin, urlsplit


def validate_url(url):
    """Check that the given URL is a valid HTTPS target for code downloads"""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(char) < 33 or char == "\\" for char in url)
    ):
        raise ValueError("Code downloads require an HTTPS URL without credentials")

    parsed.port  # accessing the port raises ValueError if it is malformed
    return parsed


def auth_for_url(url, auth, trusted_url):
    """Attach basic auth only if the target stays within the trusted root"""
    target = validate_url(url)
    if not auth or not trusted_url:
        return None

    trusted = validate_url(trusted_url)
    if (target.hostname, target.port or 443) != (
        trusted.hostname,
        trusted.port or 443,
    ):
        return None

    path = unquote(target.path)
    root = unquote(trusted.path).rstrip("/") + "/"
    if (
        ".." in path.split("/")
        or "%" in path
        or "\\" in path
        or not path.startswith(root)
    ):
        return None

    return tuple(auth.split(":", 1))


def fetch_text(url, *, auth=None, trusted_url=None, max_size=5 * 1024 * 1024):
    """
    Download remote code with HTTPS-only validation, manual redirect
    handling and a size limit
    """
    import requests

    with requests.Session() as session:
        session.trust_env = False
        for _ in range(6):
            validate_url(url)
            session.cookies.clear()
            with session.get(
                url,
                auth=auth_for_url(url, auth, trusted_url),
                allow_redirects=False,
                timeout=(10, 30),
                stream=True,
            ) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue

                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > max_size:
                        raise ValueError("Downloaded content exceeds the size limit")

                return content.decode("utf-8-sig")

    raise requests.TooManyRedirects("Too many redirects while downloading code")


async def fw_protect():
    await asyncio.sleep(random.randint(1000, 2000) / 1000)


def get_startup_callback() -> Callable:
    return lambda *_: os.execl(
        sys.executable,
        sys.executable,
        "-m",
        os.path.relpath(os.path.abspath(os.path.dirname(os.path.abspath(__file__)))),
        *sys.argv[1:],
    )


def die():
    """Platform-dependent way to kill the current process group"""
    match True:
        case _ if "DOCKER" in os.environ:
            sys.exit(0)
        case _ if sys.platform == "win32":
            sys.exit(0)
        case _:
            os.killpg(os.getpgid(os.getpid()), signal.SIGTERM)


def restart():
    if "--sandbox" in " ".join(sys.argv):
        exit(0)

    if "HEROKU_DO_NOT_RESTART2" in os.environ:
        print(
            "herokutl 2.0.5 or higher is required; reinstall requirements.txt."
        )
        sys.exit(0)

    logging.getLogger().setLevel(logging.CRITICAL)

    print("🔄 Restarting...")

    if "HEROKU_DO_NOT_RESTART" not in os.environ:
        os.environ["HEROKU_DO_NOT_RESTART"] = "1"
    else:
        os.environ["HEROKU_DO_NOT_RESTART2"] = "1"

    if "DOCKER" in os.environ or sys.platform == "win32":
        atexit.register(get_startup_callback())
    else:
        signal.signal(signal.SIGTERM, get_startup_callback())
    die()


def print_banner(banner: str):
    print("\033[2J\033[3;1f")
    with open(
        os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "assets",
                banner,
            )
        ),
    ) as f:
        print(f.read())
