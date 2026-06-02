#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up GitHub's official remote MCP server (https://api.githubcopilot.com/mcp/).
# Hosted by GitHub — no local install, nothing to vendor. Auth is a GitHub
# Personal Access Token sent as an 'Authorization: Bearer <PAT>' header over the
# HTTP transport. The PAT is validated against api.github.com before registering.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" github "$@"
