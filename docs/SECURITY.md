# Security policy

Igorek can read files, run commands and connect to external services. Use it only
with accounts, working directories and permissions you intend to grant it.

## Supported versions

The latest development version on the default branch receives security fixes.
There is no supported stable release series yet. Linux, Android and Windows
release acceptance must pass before a build is advertised as stable.

## Reporting a vulnerability

Use **Security → Advisories → Report a vulnerability** in the project's public
GitHub repository. If that button is unavailable, do not post exploit details or credentials in a
public issue. Report the unavailable private channel without including details.

Include the version or commit, affected interface, prerequisites, reproducible
steps using synthetic data, expected and observed behavior, and security impact.
Never attach live tokens, cookies, Vault contents or personal conversations.
An acknowledgment or remediation deadline is not guaranteed at this stage.

## Boundaries to review

- API authentication, device pairing and revocation, TLS and redirect handling.
- File paths, symlinks, subprocess arguments and command approval signatures.
- Electron main/preload/renderer boundaries and untrusted browser pages.
- Host/guest separation, egress credentials and failure when isolation is unavailable.
- Secret storage, consent, OAuth state/PKCE and private plugin permissions.

The strictest boundary available depends on the selected execution mode.
Approval prompts and model instructions do not replace operating system isolation.
See [isolation](architecture/ISOLATION.md) and [policy](architecture/POLICY.md)
for implementation-specific limits. Test fixtures are not service credentials.

Do not actively test systems, accounts or data you do not own or have permission
to assess. A report about third-party software should identify the affected
component and distinguish upstream behavior from Igorek integration behavior.
