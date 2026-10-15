# Desktop plugins

The ordinary Igorek chat is the main interface for plugin work. Plugins require
both a configured connection and the intended model/profile grant.

## OpenAI Docs

The public documentation MCP supports searching and reading official OpenAI
information without an API key. Enabling a connection does not automatically
allow every profile to use it. Select the intended profile and save its access
in the owner-controlled permissions interface.

## Private integrations

Gmail reading, Google Calendar and other private services have separate account
consent and profile permissions. Reusing an existing authorized connection must
not silently broaden its scopes. Locked Vault/interface state, revoked grants
and profile changes invalidate access at the operation boundary.

Calendar creation and changes require confirmation. A connection check or an
OAuth redirect alone does not prove that a real calendar operation succeeded.
Inspect the committed result and the target calendar.

## Google Calendar setup

Use the normal connection flow in the application when Google is already
configured. Otherwise prepare an OAuth client in the owner's Google project,
enable Calendar API and configure the supported redirect URI and consent scopes.
Store client credentials in the designated private configuration/Vault path,
never in source control or issue attachments.

If consent is canceled, incomplete, for a different account/profile or missing
required scopes, keep the integration disconnected. If Calendar API is disabled,
explain that action and offer the relevant project activation link. Do not ask
the user to repeat unrelated setup or claim a mocked token exchange as real login.

Official references: [Calendar API authorization](https://developers.google.com/workspace/calendar/api/auth),
[OAuth for installed applications](https://developers.google.com/identity/protocols/oauth2/native-app).

## Verification

Test default deny, exact profile identity, reauthentication, revocation, lock,
stale replies, canceled consent and connection failures. Confirm real account
consent and reads/writes separately with the account owner. Synthetic network
fixtures do not establish access to a real Google account.
