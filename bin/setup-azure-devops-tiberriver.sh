#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up the Tiberriver256/mcp-server-azure-devops community server as a
# PAT-based fallback for environments where the Microsoft server's Entra /
# azcli auth isn't workable. Uses the same `api_token` flow as Atlassian /
# Tempo, with Azure DevOps env vars (AZURE_DEVOPS_ORG_URL / _PAT / _AUTH_METHOD).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" azure-devops-tiberriver "$@"
