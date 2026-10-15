# OpenCode integration

Desktop includes a separate OpenCode 2 workspace alongside Igorek chat and the
ChatGPT browser panel. It is an integration with its own process/session
lifecycle, not a name for Igorek's shared runtime.

Verified Igorek profiles can be selected through the shared profile catalog.
Profile identity and model limits are carried through that route; credentials
stay in the host-managed provider path. Selecting a profile does not grant
arbitrary plugin or host-service access.

## Execution boundaries

The Linux process path uses its explicit sandbox configuration, working-folder
bindings and host bridge. KVM is a separate mode requiring its image and actual
host capabilities. Do not describe an unavailable VM as active isolation.
Provider traffic uses the approved host route; a guest cannot replace the
recipient of an injected token through its request path.

## Registered host services

Services such as media tools are enrolled by the owner and addressed by their
registered identity. The bridge accepts the defined operations and bounded
configuration, not a free-form host command. Availability and grants are checked
at the operation boundary. A profile switch must not inherit another profile's
private permissions.

## Acceptance

Check model selection, tool calls, approvals, browser/workspace tabs, reconnect,
profile changes and session continuation. Mobile should observe the intended
shared session/profile without exposing provider credentials. Unit tests using
fake services are separate from an installed OpenCode process and real accounts.

Windows backend/sandbox acceptance remains a separate release gate. See
[isolation](../architecture/ISOLATION.md) and [testing](../TESTING.md).
