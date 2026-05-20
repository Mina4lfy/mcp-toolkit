#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up tranzact/tempo-filler-mcp-server for logging time on Jira tasks via
# the Tempo plugin (Jira Data Center). Same Jira PAT works for both this and
# the Atlassian server if they target the same Jira instance.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" tempo-filler "$@"
