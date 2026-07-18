"""Represents current userbot version"""

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

# Ratko modifications, 2026

__version__ = (2, 2, 0)

PROJECT_NAME = "Ratko"
REPO_URL = "https://github.com/unsidogandon/ratko"
REPO_API_URL = "https://api.github.com/repos/unsidogandon/ratko"
DEFAULT_BRANCH = "main"

import os

NO_GIT = any(
    os.environ.get(variable) == "1"
    for variable in ("HEROKU_NO_GIT", "RATKO_NO_GIT")
)
if not NO_GIT:
    import git
else:
    git = None

if configured_branch := os.environ.get("RATKO_BRANCH"):
    branch = configured_branch
elif NO_GIT:
    branch = DEFAULT_BRANCH
else:
    try:
        assert git is not None
        with git.Repo(
            path=os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        ) as repo:
            branch = repo.active_branch.name
    except Exception:
        branch = DEFAULT_BRANCH
