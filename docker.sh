#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${RATKO_REPO_URL:-https://github.com/unsidogandon/ratko.git}"
REPO_REF="${RATKO_REPO_REF:-main}"
APP_DIR="${RATKO_APP_DIR:-ratko}"

if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker is required: https://docs.docker.com/engine/install/\n' >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    printf 'Docker Compose v2 is required.\n' >&2
    exit 1
fi

if [ ! -f "docker-compose.yml" ] || [ ! -d "heroku" ]; then
    if [ -e "$APP_DIR" ]; then
        printf 'Target path already exists: %s\n' "$APP_DIR" >&2
        exit 1
    fi

    git clone --branch "$REPO_REF" --single-branch "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

if [ -d ".git" ]; then
    if ! git diff --quiet || ! git diff --cached --quiet; then
        printf 'Tracked local changes prevent a safe Docker update.\n' >&2
        exit 1
    fi
    git fetch origin \
        "+refs/heads/$REPO_REF:refs/remotes/origin/$REPO_REF"
    git merge --ff-only "origin/$REPO_REF"
fi

docker compose build
if ! docker compose run --rm --entrypoint sh worker -c \
    'for file in /data/sessions/*.session /data/*.session; do [ -e "$file" ] && exit 0; done; exit 1'; then
    printf 'Complete Telegram authorization in the container.\n'
    docker compose run --rm worker \
        python -m heroku --root --data-root /data --no-git --sandbox
fi
docker compose up -d
printf 'Ratko is running. Use `docker compose logs -f worker` for logs.\n'
