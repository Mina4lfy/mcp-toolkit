#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up Google Workspace via Google's OFFICIAL hosted remote MCP servers
# (Gmail / Drive / Calendar), sharing ONE OAuth client. Idempotent: re-run to
# add / remove apps or rotate the client secret.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" google "$@"
