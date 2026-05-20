#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up sooperset/mcp-atlassian against a self-hosted Jira (+ optional Confluence) DC
# instance, authenticating with a Jira DC Personal Access Token.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" atlassian-sooperset "$@"
