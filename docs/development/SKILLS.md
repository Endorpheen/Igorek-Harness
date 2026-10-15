# Skills

Skills are instruction packages loaded by `factory/core/skills_manager.py`.
The repository does not bundle the six example skills mentioned by an older
catalog. Installed user skills and optional integrations are separate data.

## Package format

A package keeps its entry point at `<skill-name>/SKILL.md`. Moving that file to
the documentation tree would break discovery. Supporting scripts, references
and assets remain beside the entry point. Write reusable skill instructions in
English; a skill may still produce user-facing output in the requested language.

```yaml
---
name: example-report
description: Create a report from data supplied by the user.
must_write: true
---
```

`must_write: true` declares a write contract: the executor requires a `Write`
tool call before accepting completion. It does not prove the written content
is correct; validate the resulting artifact separately.

## Discovery

`AGENTFACTORY_SKILLS_DIRS` overrides the directory list. The legacy single-path
override is `AGENTFACTORY_SKILLS_PATH`. Without an override, the manager checks
the local data directory's `skills/` and any configured legacy location.
Package discovery uses `SKILL.md`; legacy flat Markdown skills remain supported.

Do not put personal skills, account-specific paths, credentials or generated
artifacts into the repository. Introduce bundled skills deliberately, with
English instructions, license ownership, documented dependencies and a test
for their required output. A README catalog must describe files that exist.

Variable references in skill instructions remain literal. Ambient environment
values are never substituted into the model prompt. Authorized runtime tools
receive scoped credentials separately; prompts contain variable names only.
