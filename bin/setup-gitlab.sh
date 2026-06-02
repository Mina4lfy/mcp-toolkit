#!/usr/bin/env bash
# Thin wrapper — see ../setup-mcp.py for the actual logic.
# Sets up the zereight/gitlab-mcp server (vendored under vendor/gitlab-mcp,
# launched via the @zereight/mcp-gitlab npx package). Uses the same `api_token`
# flow as Atlassian / Tempo / Azure DevOps (Tiberriver), with GitLab env vars
# (GITLAB_PERSONAL_ACCESS_TOKEN / GITLAB_API_URL + optional project / read-only
# flags). Works against gitlab.com (SaaS) and self-hosted GitLab.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../setup-mcp.py" gitlab "$@"
