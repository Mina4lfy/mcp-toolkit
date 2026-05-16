#!/usr/bin/env bash
# Thin wrapper — see ../setup-google-mcp.py for the actual logic.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-google-mcp.py" drive "$@"
