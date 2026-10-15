Android runtime dependency notices

APACHE-2.0.txt contains the full Apache License, Version 2.0 used by the
runtime dependencies listed in runtime-dependencies.json. That inventory
records actual resolved artifacts and upstream POM license declarations,
including inherited parent declarations. It contains no local cache paths.

The terminal's MIT notice remains at assets/terminal/XTERM-LICENSE.txt.
OkHttp's public-suffix notice remains at okhttp3/internal/publicsuffix/NOTICE
in the APK. Preserve those separate notices and any upstream copyright
notices. This inventory does not replace third-party licenses or grant
rights to external web content.

Regenerate and review the inventory after changing Android dependencies.
The inventory records debug and release runtime variants separately; debug
tooling is not counted as release code. Permanent signing is a separate gate.
