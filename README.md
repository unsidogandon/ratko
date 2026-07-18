# Ratko Userbot

Ratko is a maintained Telegram userbot based on the current
[`coddrago/Heroku`](https://github.com/coddrago/Heroku/tree/beta) beta branch.
It keeps the `heroku` Python package and internal database namespaces so existing
modules, sessions and backups remain compatible.

[Russian documentation](README_RU.md)

## Security

Third-party modules execute Python code with the same operating-system and
Telegram permissions as Ratko. Install modules only from developers you trust.
Commands such as `.terminal` and `.eval` intentionally provide full owner-level
access and must never be granted to other users.

## Requirements

- Python 3.10 or newer
- Git and FFmpeg
- Telegram `API_ID` and `API_HASH` from <https://my.telegram.org/apps>

## Installation

Ubuntu and Debian:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
git clone --branch main https://github.com/unsidogandon/ratko.git
cd ratko
./install.sh
```

Manual installation on other systems:

```bash
git clone --branch main https://github.com/unsidogandon/ratko.git
cd ratko
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m heroku
```

Root users must add `--root`. The package remains named `heroku` for module
compatibility.

## Docker

Docker Compose v2 is required:

```bash
git clone --branch main https://github.com/unsidogandon/ratko.git
cd ratko
./docker.sh
```

Application code is stored in the immutable image under `/app`. Sessions,
configuration and downloaded modules are persisted in the `worker` volume under
`/data`.

## Updating

For a regular installation, use `.update` in Telegram. Updates are accepted only
when they can be applied as a Git fast-forward; local tracked changes and
diverging commits are not deleted.

Docker installations intentionally disable the in-process updater. Update and
recreate the container with:

```bash
./docker.sh
```

Users of the older Ratko release should create a `.backupall` backup and run its
existing `.update -f` once after the new `main` branch is published. On first
startup, Ratko migrates root-level `ratko-*.session` and `heroku-*.session` files
into `sessions/` and continues to read both formats.

## Verification

Safe checks that do not connect to Telegram:

```bash
python -m compileall -q heroku
bash -n install.sh docker.sh banner.sh
git diff --check
```

## License And Credits

Ratko is distributed under the [GNU AGPLv3](LICENSE). It is derived from Heroku
and Hikka; their copyright notices and internal compatibility names are retained.
Thanks to Codrago, Hikari and the Telethon/herokutl contributors.
