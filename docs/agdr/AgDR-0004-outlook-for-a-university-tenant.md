# Outlook (Microsoft 365) for the THI account: which server, which auth

> Status: **Proposed**. It waits on one answer from THI IT (see Open question).
> Researched 2026-11-21 for issue #1.

> In the context of adding Outlook mail, calendar and contacts for a THI (Technische
> Hochschule Ingolstadt) Entra ID account, facing a tenant whose consent policy is not
> public, I propose **`@softeria/ms-365-mcp-server`, pinned, with delegated Graph
> permissions only**, to achieve mail + calendar + contacts with no admin-only
> permission, accepting that THI's consent settings may still require an admin approval.

## Context

The toolkit registers local stdio servers with `claude mcp add`, pinned to an exact
version (AgDR-0003). Every Outlook option goes through Microsoft Graph or Exchange
Online, and so through Entra ID consent in THI's tenant.

## Options Considered

| Option | For | Against |
|---|---|---|
| **`@softeria/ms-365-mcp-server`** ([repo](https://github.com/Softeria/ms-365-mcp-server), [npm](https://www.npmjs.com/package/@softeria/ms-365-mcp-server)) | MIT, 0.190.2 published 2026-11-20, active. `--preset outlook` limits it to mail, calendar and contacts; `--org-mode` adds work/school tools. Ships a default app, or takes your own with `MS365_MCP_CLIENT_ID`. | Default sign-in is device code, which many tenants block. Whether its default app is from a verified publisher is unverified. |
| **Microsoft Work IQ** ([repo](https://github.com/microsoft/work-iq), [Learn](https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/work-iq/mcp/overview)) | Microsoft's own service. Per its [launch post](https://devblogs.microsoft.com/microsoft365dev/work-iq-production-ready-intelligence-for-every-agent/), no Copilot licence is needed (billed by use). An older Learn page says a licence is needed, so the sources conflict. | Its README says its permissions need tenant admin rights. Closed source (EULA). Generic Graph-path tools. Use from Claude Code is unverified. |
| **`@merill/lokka`** ([repo](https://github.com/merill/lokka)) | MIT, interactive browser login. | Generic Graph calls, no mail/calendar tools. A personal project. |
| IMAP/SMTP with OAuth ([Learn](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)) | Standard protocols. | Mail only. It still needs an app registration and consent. |
| EWS | — | Blocked by default in Exchange Online since 2026-10-01 ([Microsoft](https://techcommunity.microsoft.com/blog/exchange/retirement-of-exchange-web-services-in-exchange-online/3924440)). Not viable. |

## What decides it: THI's consent settings

- Delegated `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Contacts.ReadWrite`, `offline_access` and `User.Read` do **not** need admin consent by default. Their application versions do ([Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)).
- A tenant can still require an admin. That happens with "Do not allow user consent", or with "verified publishers, for selected permissions" when the app is unverified or a permission isn't classified as low impact ([user consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent)).
- A blocked user can send an admin a request, but only if a Global Administrator turned that workflow on ([admin consent workflow](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-admin-consent-workflow)).
- Conditional Access can block device-code sign-in, and Microsoft advises blocking it ([authentication flows](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-authentication-flows)).
- THI's policy is not public. Its IT guides sit behind a Moodle login ([THI IT service](https://www.thi.de/en/service/it-service/)).

## Proposal

1. Ask THI IT (support@thi.de) whether students can consent to a third-party app with the delegated permissions above, and whether device-code sign-in is allowed.
2. If yes: add a service with `npx -y @softeria/ms-365-mcp-server@0.190.2 --org-mode --preset outlook`, an `entra_login`-style flow, and tokens under `state/outlook/<slug>/` at `0600`. If device code is blocked, use the server's OAuth code flow (`--http`) instead.
3. If THI needs an admin to approve: ask IT to approve the app, or to say which app they allow. Work IQ also needs an admin, so it isn't a way around that.

## Consequences

- No code is written until step 1 is answered. Nothing can bypass the tenant's consent.
- The server is pinned and bumped like every other server (AgDR-0003).

## Open question

THI's user-consent and device-code settings. Ask support@thi.de.

## Artifacts

- Issue #1
