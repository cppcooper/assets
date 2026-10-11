#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MASTER_SCRIPT="$REPO_ROOT/scripts/download-and-extract-assets.py"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'init.sh: Python 3 is required but was not found in PATH.\n' >&2
    exit 127
fi

if [[ ! -f "$MASTER_SCRIPT" ]]; then
    printf 'init.sh: missing master script: %s\n' "$MASTER_SCRIPT" >&2
    exit 1
fi

cd "$REPO_ROOT"
exec python3 "$MASTER_SCRIPT" "$@"
