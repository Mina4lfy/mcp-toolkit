# Adopt Google's official hosted Workspace MCP for Gmail/Drive/Calendar

> In the context of the toolkit's Google setup, facing three fragmented
> community vendors (one per app) with their own OAuth flows, token formats, and
> a recurring reliability tax, I decided to replace them with Google's **official
> hosted** Workspace MCP servers behind a **single user-created OAuth client**,
> to achieve unified auth + provider-hosted reliability + official-vendor trust,
> accepting that it remains three connected servers and may differ in per-tool
> depth from the community vendors.

## Context

The toolkit registered Gmail, Calendar, and Drive as three separate community
MCP servers — `@gongrzhe/server-gmail-autoauth-mcp`, `@cocal/google-calendar-mcp`,
`@piotr-agier/google-drive-mcp`. Each was a distinct npm package by a distinct
author, each ran its own `oauth_browser` flow (a vendored `auth` subcommand), and
each wrote its OAuth token in its own on-disk format. That fragmentation was the
root cause of several recurring operational problems:

- **Node-24 `node-fetch` breakage** — the `node-fetch@2` premature-close bug broke
  every `google-auth-library`-based Google server, forcing an undici-preload shim.
- **Drive `:3000` port collision** — `@piotr-agier/google-drive-mcp` squatted port
  3000 for its whole session, colliding with other local setup scripts.
- **Three token formats** — no shared credential; re-auth had to be repeated per app.

Google now ships **official, Google-hosted** Workspace MCP servers (one per product)
that share **one OAuth 2.0 client and one consent screen**. Claude Code can register
OAuth-protected HTTP remotes with a pre-created client
(`claude mcp add --transport http --client-id … --client-secret --callback-port …`).

The original README documented "one vendor per service" as a deliberate depth-over-
breadth choice — so switching Google reverses that decision *for Google only* and is
recorded here.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **Keep the 3 community vendors** | Deep per-app tools; no migration | Fragmented auth (3 flows, 3 token formats); the Node-24 shim + `:3000` collision persist; community trust |
| **Community unified server** (`taylorwilsdon/google_workspace_mcp`) | Truly ONE connected server routing by request type; one OAuth; deep coverage | Not official; still self-hosted (npx/uvx + local token); another vendor to pin/audit |
| **Official Google hosted servers** (chosen) | Official-vendor trust; Google-hosted (no vendor/submodule/npx, no Node-24 shim, no `:3000`, no local token); ONE OAuth client/consent; unifies auth | Still 3 connected servers (Google ships per-product — does not reduce server count); per-tool depth may differ from the community vendors; live OAuth needs Cloud Console + a browser |

## Decision

Chosen: **Official Google hosted Workspace MCP servers**, behind one user-created
OAuth 2.0 client (Web application, redirect `http://localhost:33418/callback`).

Rationale: the owner prioritised official trust + provider-hosted reliability + a
single credential to manage over collapsing the connected-server count (which only
the community unified server achieves, at the cost of official trust). The official
option retires the documented reliability problems outright and unifies auth to one
consent, which was the practical pain.

Implemented as a new `remote_oauth` auth flavour with a single `google` service and
an **idempotent converge flow**: re-running `./bin/setup-google.sh` reads existing
state, shows registered apps, and applies only the delta (add / remove / rotate),
reusing the stored OAuth client. Scope limited to Gmail/Drive/Calendar; Chat/People
are one `SERVICES["google"]["apps"]` entry away.

## Consequences

- Three `SERVICES["google-*"]` entries, three `bin/setup-google-*.sh` wrappers, the
  `oauth_browser` machinery, and three `vendor/` submodules were removed. The
  PEP-723 `google-auth*` deps (only used by the removed connection test) were dropped.
- Google is now the documented exception to "one vendor per service."
- Auth is completed interactively (`claude mcp login "<title>"`) after registration —
  one browser sign-in grants the union of scopes for all selected apps.
- Residual risk: whether Google accepts the `http://localhost:33418/callback` redirect
  on the CLI path. Documented fallback: add the same endpoints as custom connectors in
  the claude.ai UI (redirect `https://claude.ai/api/mcp/auth_callback`) — identical
  Cloud Console prep either way.
- Per-tool depth may differ from the community vendors; revisit if a needed capability
  is missing (the community unified server remains a fallback).

## Artifacts

- Ticket: Mina4lfy/mcp-toolkit#2
- Source of truth: Google docs — <https://developers.google.com/workspace/guides/configure-mcp-servers>
- Claude Code OAuth-remote mechanics: <https://code.claude.com/docs/en/mcp>
