# Contributing

Start with a small, reproducible issue and check existing work before changing
behavior. Describe the current result, expected result, version, platform and
minimal reproduction. Send vulnerabilities through the [private channel](SECURITY.md).

## Development

Use Python from `.python-version`, `uv`, Node.js 24 or newer and Java 17 for
Android. Dependencies are recorded in `uv.lock`, `desktop/package-lock.json`
and the Gradle version catalog. Do not commit environments, keys or build outputs.

```bash
uv sync --locked --dev
npm --prefix desktop ci
uv run --no-sync python tools/run_tests.py --coverage
npm --prefix desktop test
```

See [testing](TESTING.md) for system prerequisites, Android checks and limits.
Tests run with temporary data and fake service responses. Real account and
physical device acceptance is a separate step and must be reported separately.

## Changes

- English is the primary language for code comments, docstrings, skills,
  commit messages and technical documentation. User-facing Russian is retained.
- Keep reusable code, tests, assets and documentation in their corresponding
  directories; consult the [project map](PROJECT_LAYOUT.md).
- Preserve public commands, saved data and owner approval boundaries.
- Add a regression for a behavior fix, including the valid behavior that must
  continue working. Do not delete or skip a failing test to make a build green.
- Avoid broad reformatting or module moves mixed with unrelated behavior fixes.
- Explain the concrete effect and validation in the pull request. List any
  remaining failures, missing platform checks and migration requirements.
- Source comments and commit messages use English; translations and UI strings
  follow the language of the relevant user interface.

## License and attribution

Original contributions are submitted under [Apache-2.0](../LICENSE), unless
explicitly agreed otherwise. Preserve third-party license and copyright notices.
Do not add code or assets without redistribution rights. Existing third-party
notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
