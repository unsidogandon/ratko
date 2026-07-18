#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '\033[2J\033[3;1f'
cat "$ROOT_DIR/assets/banner.txt"
printf '\n\n\033[1;32mRatko is running!\033[0m\n'
