Locally vendored web-client libraries (no runtime CDN requests).

1) SheetJS Community Edition (xlsx.full.min.js)
   Version: 0.20.3
   Upstream: https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js
   License: Apache License 2.0 (XLSX-LICENSE.txt)
   Purpose: parse .xlsx/.xls in a Web Worker (workers/xlsx-worker.js).
   Only cell reading and formatting are used. Formulas and macros are not
   executed; the worker cannot access the network.

2) highlight.js (highlight.min.js)
   Version: 11.11.1 (git 08cb242e7d)
   Upstream: https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/highlight.min.js
   License: BSD 3-Clause (HIGHLIGHT-LICENSE.txt)
   Purpose: syntax highlighting in the side-panel file viewer (file-viewer.js).
   Loaded lazily when source code is opened in the viewer.

3) xterm.js (xterm.js, xterm.css)
   Version: 5.5.0
   Upstream: https://registry.npmjs.org/@xterm/xterm/-/xterm-5.5.0.tgz
   Integrity (npm dist): sha512-hqJHYaQb5OptNunnyAnkHyM8aCjZ1MEIDTQu1iIbbTD/xops91NB5yq1ZK/dC2JDbVWtF23zUtl9JE2NqwT87A==
   Files: lib/xterm.js -> vendor/xterm.js; css/xterm.css -> vendor/xterm.css
   License: MIT (XTERM-LICENSE.txt)
   Purpose: terminal emulation in the side-panel Terminal tab.
   Loaded lazily when the tab is first opened. It connects to the application's
   own origin (wss://127.0.0.1:<port>/api/terminal), without external requests.

4) @xterm/addon-fit (addon-fit.js)
   Version: 0.10.0
   Upstream: https://registry.npmjs.org/@xterm/addon-fit/-/addon-fit-0.10.0.tgz
   Integrity (npm dist): sha512-UFYkDm4HUahf2lnEyHvio51TNGiLK66mqP2JoATy7hRZeXaGMRDr00JiSF7m63vR5WKATF605yEggJKsw0JpMQ==
   Files: lib/addon-fit.js -> vendor/addon-fit.js
   License: MIT (XTERM-LICENSE.txt, same license text as xterm.js)
   Purpose: fit the terminal dimensions to the panel container.

Do not edit vendored files manually. Version updates require a separate review
of licenses and behavior.
