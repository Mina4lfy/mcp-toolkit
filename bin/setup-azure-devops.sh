#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up Microsoft's official @azure-devops/mcp server. Default auth is an
# interactive Entra browser flow on first tool call; alternatives are azcli,
# pat, envvar, env (Azure SDK env-credential chain).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" azure-devops "$@"
