# mcp-toolkit — Handover Assessment

**Date**: 2026-08-08
**Assessor**: Mina Alfy
**Status**: handover

What this is: the record of adopting `mcp-toolkit` into ApexYard governance — what the repo is, what state it's in, what's risky, and what happens next. Read "Quality Risks" and "Next Steps" if you're picking up work; the rest is context.

## Origin

- **Where it came from**: personal project, self-authored — adopted into the portfolio rather than inherited
- **Original owner**: Mina Alfy (sole contributor)
- **Repo location**: `https://github.com/Mina4lfy/mcp-toolkit` (**public**)
- **First commit date**: 2026-05-16
- **Last commit on `main`**: 2026-07-03 (`d6bec64`)
- **Last push (any branch)**: 2026-07-17

## Current State

### What it does

A setup harness that registers MCP servers into Claude Code across seven providers (Google Workspace, Atlassian DC, Tempo, LinkedIn, Azure DevOps ×2, GitHub, GitLab). One Python entry script dispatches on a `SERVICES` table; seven thin bash wrappers under `bin/` are the user-facing commands. Five auth flavours (`remote_oauth`, `api_token`, `cookie_paste`, `entra_login`, `remote_http`). All credentials land under `./state/<service>/<slug>/env` at mode `0600`, gitignored.

### Tech stack

| | |
|---|---|
| Language | Python 3.10+ (`setup-mcp.py`, 2,365 lines, 59 functions) + Bash (7 wrappers, ~59 lines total) |
| Runtime | `uv run --script` via PEP-723 inline metadata |
| Dependencies | **None** — `dependencies = []`, pure stdlib |
| Framework | None (raw CLI script) |
| Database | None |
| Test framework | **None** |
| CI | **None** — no `.github/`, no pipeline of any kind |
| Lint | **None** — no ruff / flake8 / pylint / shellcheck config |
| License | **None** |

### Build status

There is no build. Verification performed instead:

- `python3 -c "ast.parse(...)"` on `setup-mcp.py` — **parses clean**
- `uv` on PATH — present
- `npx` on PATH — present
- `git submodule update --init --recursive` (the README's documented clone step) — **fails**: `fatal: no submodule mapping found in .gitmodules`

### Test coverage

Zero. No test files tracked, no coverage config, no CI. A 2,365-line script that handles OAuth client secrets, PATs, and session cookies has no automated verification of any kind.

### Repo activity

All figures in this subsection are as of 2026-08-08.

- Commits on `main`: 13 (15 across all branches)
- Commits in last 90 days: 13 on `main` — the whole history is recent
- Contributors: 1 human author (git reports 3 committer identities, including an `apexyard CI` identity)
- Open issues: 2 (`#1` Outlook MCP support, P0 · `#4` revert Google to local stdio)
- Open PRs: 1 (`#5` — the revert implementing `#4`)
- Merged PRs: 1 (`#3`)
- Branch protection on `main`: **not enabled**

## Harnessability assessment

**Overall verdict**: `low`

> ⚠ Harnessability: LOW
>
> Rex's architecture handbooks will fire advisory-only on this codebase. The blocking gate (`ENFORCEMENT: blocking`) will generate false positives. Recommended: adopt as advisory-only, plan a follow-up to add the missing scaffolding (typescript strict, lint baseline, etc.)

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Type safety | `none` | No `pyproject.toml`, no `mypy.ini`, no `pyrightconfig.json` anywhere in the tree |
| Module boundaries | `flat` | One 2,365-line `setup-mcp.py` at repo root; no package, no `src/`, no module split |
| Framework opinionation | `weak` | PEP-723 header declares `dependencies = []` — stdlib-only script, no framework to supply conventions |
| Test coverage signal | `absent` | No test files tracked, no pytest/coverage config, no CI step |
| Lint baseline | `absent` | No ruff/flake8/pylint config, no `.pre-commit-config.yaml`, no shellcheck config |

Zero of five dimensions in the top bucket, and the override rule applies independently (type safety `none` + framework opinionation `weak`). See AgDR-0042 for the scoring rationale.

This is expected for a stdlib CLI utility, not a condemnation — but it means Rex's blocking architecture handbooks have nothing to anchor against here. Adopt advisory-only.

## Quality Risks

### Blocking — the repo does not clone into a working state

**1. `.gitmodules` was deleted while the gitlinks were left behind.** Commit `7beea07` ("upd: gitignore vendor packages") removed all 24 lines of `.gitmodules` and added `vendor/` to `.gitignore`, but the five `vendor/*` entries remain in the index as mode-`160000` gitlinks:

```
160000 1cd5d89 vendor/azure-devops-mcp
160000 7ad868b vendor/azure-devops-mcp-tiberriver
160000 74a8c83 vendor/gitlab-mcp
160000 d8bc786 vendor/mcp-atlassian
160000 b9db692 vendor/tempo-filler-mcp-server
```

Consequences on a fresh clone: `git submodule update --init --recursive` — the exact command README § "Cloning the toolkit on a new machine" tells you to run — fails outright, and `vendor/` contains five empty directories. The pinned SHAs are recorded but unresolvable.

**2. The LinkedIn flow ships broken.** `setup-mcp.py:260` declares `"local_pkg_dir": "vendor/linkedin-mcp"` and launches via `uv run --directory vendor/linkedin-mcp linkedin-mcp` — but `vendor/linkedin-mcp/` is neither a tracked gitlink nor present in the tree. `bin/setup-linkedin.sh`, documented in the README's quickstart *and* its Layout diagram, does not exist. The README devotes a full section and a 14-row tool table to a flow that cannot run from any clone of this repo.

**3. The anti-fabrication audit trail is unverifiable.** README § "Anti-fabrication note" grounds every claimed env var in a specific vendored source line (`vendor/mcp-atlassian/src/mcp_atlassian/jira/config.py:180`, and 15 more — 16 citations in total). Those files don't exist on a clone. The citations are the repo's core trust claim, and right now a reader cannot check a single one.

Note that risks 1–3 are one root cause with three symptoms. Locally-launched servers actually resolve from package registries at runtime (`uvx mcp-atlassian`, `npx -y @zereight/mcp-gitlab`), so `vendor/` is a pin-and-audit record rather than a runtime dependency — **except** LinkedIn, which is the one entry that genuinely launches from local source.

### Security

The credential handling itself is careful, and that's worth stating plainly:

- Secrets read via `getpass` (no echo); env files written at `0o600` (`setup-mcp.py:703`)
- The Google client secret is passed through `MCP_CLIENT_SECRET`, never on the command line
- Echoed `claude mcp add` commands redact env values to `KEY=***`
- PATs are validated live (`api.github.com/user`, ADO `/_apis/projects`) before registration
- **A scan of all reachable history found no committed secrets**

The residual concerns are process, not code:

- **Public repo, credential-adjacent code, no secret-scanning CI.** One careless commit is the whole exposure. `.gitignore` covers `state/` and `*_client_secret*.json`, but nothing verifies that.
- **No branch protection on `main`.** Anything can land directly.
- **LinkedIn flow deliberately violates LinkedIn's User Agreement §8.2** — the README says so honestly and ships rate caps, but adopting this repo means adopting that account-ban risk. Currently moot (see risk 2), and worth a conscious decision before it's fixed.

### Dependencies

Genuinely the strong point. Zero Python dependencies. Vendored servers are pinned to explicit SHAs with recorded versions (`gitlab-mcp` v2.1.18, `tempo-filler` v2.0.2, `azure-devops-mcp` v2.7.0+34, `azure-devops-mcp-tiberriver` v0.1.45). The stated policy — pins don't move until you move them, because surprise scope changes are an audit problem — is correct. The only gap is that the pins are currently unresolvable (risk 1).

### Technical debt

- One 2,365-line file with 59 functions and no module boundary
- No type hints, no type checker, no linter
- No tests on credential-handling code paths
- Duplicated shape across the five auth flavours (`_setup_api_token`, `_setup_remote_oauth`, `_setup_cookie_paste`, `_setup_entra_login`, `_setup_remote_http`) — each 97–261 lines with substantially similar prompt/validate/write/register sequences

### Operational

- No CI, no automated verification of any kind
- No LICENSE on a public repo — default is all-rights-reserved, which contradicts the "clone it on a new machine" framing
- **The main-branch design decision is contested and mid-reversal.** AgDR-0001 records the move to Google's hosted Workspace MCP; issue `#4` reports it fails in practice (*"hosted MCP: caller does not have permission"*); PR `#5` reverts to local stdio servers. `main` currently ships the approach that doesn't work, and AgDR-0001 has no superseding record.

## Integration Plan

### Roles that apply

| Role | Why |
|------|-----|
| `tech-lead` | Always |
| `backend-engineer` | The substance is Python CLI + service-integration logic |
| `platform-engineer` | Developer tooling, shell wrappers — and the CI gap is the first real work item |
| `security-auditor` | OAuth clients, PATs, session cookies, `0600` state files, a public repo. Auto-fires on this diff surface anyway. |

No `frontend-engineer` (no UI) and no `sre` (no deployment surface).

### Workflows that kick in

- [ ] PR workflow (`.claude/rules/pr-workflow.md`) — every change through a PR
- [ ] AgDR for technical decisions — the repo already has the habit (`docs/agdr/AgDR-0001`)
- [ ] Code Reviewer agent (Rex) on every PR — **advisory-only**, per the LOW harnessability verdict
- [ ] Security Reviewer agent (Hakim) on first pass and on any credential-path change
- [ ] `/audit-deps` — low value here (zero deps); the vendored-pin refresh is the real equivalent

### Hooks to enable

- [ ] `block-git-add-all`
- [ ] `block-main-push`
- [ ] `validate-branch-name` — the repo already follows `feature/GH-4-...`
- [ ] `validate-pr-create` — PR titles already follow `feat(GH-4): ...`
- [ ] `pre-push-gate`
- [ ] `check-secrets` — highest value of the six, given the public-repo + credentials combination

### CI templates to copy in

- [ ] `golden-paths/pipelines/security.yml` — secret detection first; it's the exposure that matters
- [ ] `golden-paths/pipelines/pr-title-check.yml`
- [ ] `golden-paths/pipelines/ci.yml` — needs adapting; the shipped template is TypeScript-shaped and this is Python + Bash

### Registry entry

```yaml
- name: mcp-toolkit
  repo: Mina4lfy/mcp-toolkit
  workspace: workspace/mcp-toolkit
  docs_subpath: docs
  status: handover
  roles:
    - tech-lead
    - backend-engineer
    - platform-engineer
    - security-auditor
```

## Next Steps

All seven were filed as tracker tickets on 2026-08-08.

1. ~~Restore `.gitmodules` for the five `vendor/*` gitlinks (or drop the gitlinks entirely) — the README's documented clone path currently fails outright~~ → Filed as [#6](https://github.com/Mina4lfy/mcp-toolkit/issues/6)
2. ~~Fix or remove the LinkedIn flow — `bin/setup-linkedin.sh` and `vendor/linkedin-mcp/` are documented but absent, so `./setup-mcp.py linkedin` cannot run~~ → Filed as [#7](https://github.com/Mina4lfy/mcp-toolkit/issues/7)
3. ~~Resolve issue #4 / PR #5 and supersede AgDR-0001 with the outcome — `main` currently ships an approach its own tracker says doesn't work~~ → Filed as [#8](https://github.com/Mina4lfy/mcp-toolkit/issues/8)
4. ~~Add secret-scanning CI — copy `golden-paths/pipelines/security.yml`; a public credential-handling repo with no scanning is the sharpest edge here~~ → Filed as [#9](https://github.com/Mina4lfy/mcp-toolkit/issues/9)
5. ~~Add a lint baseline — `ruff` for the Python, `shellcheck` for the seven wrappers~~ → Filed as [#10](https://github.com/Mina4lfy/mcp-toolkit/issues/10)
6. ~~Add a LICENSE — a public repo with none is all-rights-reserved by default, which contradicts the "clone it on a new machine" framing~~ → Filed as [#11](https://github.com/Mina4lfy/mcp-toolkit/issues/11)
7. ~~Enable branch protection on `main` — require a PR before merging~~ → Filed as [#12](https://github.com/Mina4lfy/mcp-toolkit/issues/12)

Suggested order: #6 and #7 first (both are first-run blockers), then #8 (the contested design decision), then the scaffolding four.

## Post-Handover Checklist

- [ ] Restore the submodule mapping before anyone else clones this repo
- [ ] Decide whether LinkedIn support stays (and accept the §8.2 account risk) or gets cut
- [ ] Close out #4 / #5 and record the superseding AgDR
- [ ] Land secret-scanning CI in the first week
- [ ] Add `mcp-toolkit` to the weekly `/stakeholder-update` rollup
- [ ] Treat Rex's architecture handbooks as advisory-only on this repo until a lint + type baseline exists
- [ ] Re-run `/handover` after the scaffolding work to re-score harnessability

## Open Questions

- Is LinkedIn support intended to ship, or was it a local experiment that leaked into the README? The answer decides whether risk 2 is a bug or a doc cleanup.
- Are the `vendor/` gitlinks meant to be resolvable submodules (restore `.gitmodules`) or a pure pin record (drop the gitlinks and record SHAs in the README)? Both are defensible; the current half-state is not.
- Is the repo public deliberately? Nothing in it requires publicity, and private removes the whole leak-exposure class.
- Does anything besides this laptop consume the toolkit? That determines how much CI is proportionate.
