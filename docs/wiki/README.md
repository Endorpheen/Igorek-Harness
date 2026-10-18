# Igorek Harness Wiki

This directory builds the bilingual technical encyclopedia. It is independent of
the application runtime and does not require an application version bump.

## Local build

Install Python 3.13 and uv. Keep the documentation environment separate from the
application environment. For example, on Linux/macOS:

```bash
uv venv docs/wiki/.venv --python 3.13
uv pip sync --python docs/wiki/.venv/bin/python --require-hashes docs/wiki/requirements.lock
source docs/wiki/.venv/bin/activate
python -m unittest discover -s tools/wiki -p 'test_*.py' -v
mkdocs build --config-file docs/wiki/mkdocs.yml --strict
python tools/wiki/check.py
PLAYWRIGHT_BROWSERS_PATH=build/wiki/browsers python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=build/wiki/browsers python tools/wiki/browser_check.py
```

On Windows use `docs/wiki/.venv/Scripts/python.exe` and activate with
`docs/wiki/.venv/Scripts/Activate.ps1`. The browser check starts its own temporary
loopback HTTP server and closes it after verification. Nothing is installed into
the Igorek application, and no persistent documentation server is needed.

Build output and reports are under `build/wiki/`. To preview interactively, run
`mkdocs serve --config-file docs/wiki/mkdocs.yml` and open its loopback URL.

## Update from ZIP exports

Download the English and Russian exports locally, then run from the repository
root:

```bash
python tools/wiki/import_zip.py --en /path/to/english.zip --ru /path/to/russian.zip
```

Either argument can be supplied independently. The importer recognizes the
Russian export's `ru_md/` wrapper. It validates paths, archive limits, duplicate
members and symlinks before writing public files. It imports technical Markdown
and supported image assets. The export README and `_raw/` JSON are transport
metadata and may contain a personal Devin organization URL; they are excluded.
Do not commit the original ZIPs or private export metadata.

Imports are additive: a partial export never removes existing pages or assets,
and updating one language does not remove the other. `import-manifest.json`
records the source commit, export timestamp, archive hash and original file
hashes. Original page bytes and diagram counts are verified by `check.py`.

Unchanged upstream pages retain local edits. If an incoming page and its local
copy have both changed since the last import, the importer refuses the entire
update before writing files and lists the conflicting pages. Review those
conflicts; use `--accept-upstream` only when deliberately replacing local edits
with the supplied export. Never delete pages to make a partial import pass.

## Maintain the current edition

Keep original imported pages under `content/en/` and `content/ru/`. Put verified
current-behavior revisions under the matching path in `updates/en/` and
`updates/ru/`; these are rendered together with the original page. Update both
languages in the same commit. `edition.json` identifies the release and review
date. Its `historical_pages` list explicitly separates obsolete implementations
from the current reference; no original technical content is silently removed.

The initial export describes 0.2.10. The current edition documents the 0.2.12
direct-project OpenCode lifecycle, read-only progress, owner-only stopping,
Windows integrity and supported-command limits, Vault boundaries, branding and
release acceptance. Original source citations stay pinned to their export
commit; current revisions link explicitly to the reviewed release commit.
Re-review the current notes after each import, rather than assuming that a new
export automatically validates them. There is no automatic Devin synchronization.

`hooks.py` derives the complete section tree from numeric page IDs and uses the
matching Russian headings for localized navigation. The same page paths pair
the language versions. It repairs export-only presentation artifacts at build
time: empty source citations, numeric chapter links, doubled quotes, whitespace
in Mermaid node declarations, unquoted edge labels, pseudo-subgraph markers,
flowchart titles and source-note lines inside diagrams. Original Markdown remains
unchanged. The site retains all 174 original diagrams and adds the two localized
current architecture diagrams.

Diagram colors and fonts are defined centrally in `content/assets/wiki.css`
using Material's Mermaid theme variables. Exported default white/black styling
is removed only in the rendered version, while meaningful role colors remain.
Diagrams retain their natural size inside horizontally scrollable containers,
so mobile readers can inspect readable labels instead of a shrunken whole graph.

## GitHub Pages

The `Wiki documentation` workflow builds and checks both languages, all local
links/assets, diagram rendering, search, language switching, theme switching and
mobile navigation. It preserves a reviewable site/report artifact on PRs.
Dependencies have exact versions and hashes; Actions use pinned commit SHAs.

First publication requires explicit owner approval. Before that approval, leave
GitHub Pages unconfigured and the repository variable `WIKI_PUBLISH_ENABLED`
unset. The deploy job cannot run on PRs and also requires that variable to equal
`true` on `main`.

After approval, enable Settings → Pages → GitHub Actions, set the repository
variable `WIKI_PUBLISH_ENABLED=true`, and merge the reviewed PR through the
protected branch. Future commits changing `docs/wiki/`, `tools/wiki/` or the Wiki
workflow automatically rebuild, verify and deploy. A manual workflow dispatch
can publish the already approved main branch if needed. No paid service or
always-on workstation is involved.

The expected address is `https://endorpheen.github.io/Igorek-Harness/`.
It becomes available only after Pages is enabled and the deployment succeeds.
Verify the actual public address after first deployment:

```bash
python tools/wiki/browser_check.py --url https://endorpheen.github.io/Igorek-Harness/ --output build/wiki/public-browser
```

Disable the repository variable to stop future automatic deployments. Previous
successful Pages content remains available if a later build fails. Reverting a
documentation commit and rerunning the workflow republishes that revision.
