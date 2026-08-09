#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up Google Workspace as LOCAL stdio community MCP servers (Gmail / Drive /
# Calendar) behind ONE Desktop OAuth client. Idempotent: re-run to add / remove
# apps or rotate the client secret. Non-interactive: pass
#   --client-secret <path> [--handle NAME] [--apps gmail,drive,calendar]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" google "$@"
