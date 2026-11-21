# Pin every launched MCP server package to an exact version

> In the context of the toolkit registering stdio MCP servers that hold live OAuth
> tokens and API tokens, facing `npx -y <pkg>` and `uvx <pkg>` resolving the latest
> release at every server start, I decided to **pin each package to an exact
> version** that matches its `vendor/` source pin, to achieve **reviewed,
> reproducible code on the operator's machine**, accepting that upgrades become a
> manual, reviewed step.

## Context

[AgDR-0002](AgDR-0002-revert-google-to-local-stdio-servers.md) moved Google from
Google-hosted endpoints back to community npm packages, and did not record the
supply-chain cost. Every server was launched unpinned, so:

- A new upstream release ran on the next server start, with no review and no record.
  These processes hold Gmail, Drive and Calendar refresh tokens, and Jira, GitLab and
  Azure DevOps tokens. A compromised release would be remote code execution with that
  access.
- The `Source pin: vendor/<repo> @ <sha>` lines in `SERVICES` governed nothing at run
  time, so behaviour verified at that SHA was not the behaviour that ran.
- Several servers started together could race to install a new release into the same
  npx cache. Claude Code's MCP logs show `npm error code ENOTEMPTY` crashes on
  2026-09-03 and 2026-10-29. The second was the day `@cocal/google-calendar-mcp@2.8.1`
  was published.

## Options Considered

1. **Keep `latest`.** No upkeep, but it keeps every risk above.
2. **Pin exact versions in the launch command** (`npx -y pkg@X.Y.Z`, `uvx pkg@X.Y.Z`),
   matched to the `vendor/` pin.
3. **Install into a toolkit-owned `node_modules` and launch by path.** This adds a
   lockfile with integrity hashes, but means more machinery per service and a
   separate install step on every machine.

## Decision

Option 2. Every `npx` and `uvx` package in `SERVICES` names an exact version.
Where a service has a `vendor/` submodule, the submodule points at the commit of
that same release tag, and its `scopes_note` cites it as `@ <sha> (vX.Y.Z)`.
`tests/test_pins.py` fails if a package is unpinned, or if its version differs from
the cited release.

Two submodules sat between releases and were moved to the release tag:
`mcp-atlassian` from `d8bc786` to `4e2b836` (v0.21.1), and `azure-devops-mcp` from
`1cd5d89` to `70e94c6` (v2.7.0). The files cited as evidence are identical across
each move.

The Google packages have no submodule. They are pinned to the versions that were
running when this decision was made: Gmail 1.1.11, Drive 2.13.0, Calendar 2.8.1.

## Consequences

- Upstream fixes arrive only when someone bumps a pin. The bump process is in the
  README.
- A pinned version that is already cached is not installed again at boot, which
  removes the npx cache race between servers that start together.
- Option 3 stays open if integrity hashes become a requirement.

## Artifacts

- Issue #19
- `tests/test_pins.py`
