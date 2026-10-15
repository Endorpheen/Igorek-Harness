# ChatGPT plan quota

The optional quota counter reads subscription limits over HTTPS. It does not
bundle, locate or launch Codex CLI, and it does not need an agent subprocess.
Model authentication and counter confirmation are separate operations.

## Existing connections

Previously confirmed counter bindings remain usable. A binding identifies the
profile configuration, OAuth subject, email and ChatGPT account. Native login
credentials are read only from the configured external account location;
refresh tokens are excluded. Legacy separate counter credentials remain
readable without rewriting them. The model's credentials are not replaced.

A new counter connection asks the owner to confirm the account. Only binding
metadata is stored for a native or profile-backed connection. If no native
snapshot is available, the existing profile access token can be used when the
quota service authorizes it. A service refusal remains a refusal; the counter
never opens a sign-in browser, refreshes a token or tries another account.
An expired or unsupported credential can make the optional counter unavailable
while the model connection remains configured.

## Request and response boundary

The adapter uses a fixed HTTPS destination, one `GET`, a bounded response and a
timeout. `Authorization` and `ChatGPT-Account-Id` identify the confirmed account;
redirects cannot forward credentials. The response account/email must match.
A changed account, profile or locked consumer invalidates a reply before it can
be published. No response body or credential is included in an error message.

Supported windows are selected by duration: five hours and seven days. Missing
or malformed limits are unavailable, never zero. Unrelated limit families are
excluded. Previously read values retain their observation timestamp and an
explicit error when the latest request fails; account mismatches hide the cache.

The subscription-usage service is an implementation-dependent integration,
not a claim of a permanently supported public quota API. Its response and
permissions may change. Account authentication, renewal and sign-out stay with
the existing account owner/model connection, outside this read-only adapter.

## Verification

`tests/test_codex_quota.py`, `test_codex_quota_direct.py` and
`test_chatgpt_usage.py` cover account binding, direct HTTP, supported windows,
refusal without retry/login/refresh, legacy bindings and source-file retention.
Packaging rejects the obsolete bundled quota executable so it cannot return
through a cached runtime. Native Linux first-task/restart acceptance is separate
from real account and device acceptance; see [testing](../TESTING.md).
