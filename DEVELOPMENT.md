# Development

Use Ubuntu 22.04 x86_64 with the system Python and the dependencies listed in
`tools/install_bundle.py`. Do not run native UI tests on your active desktop.

## Unit tests

```bash
/usr/bin/python3 -m unittest \
  herdr_browser.test_store herdr_browser.test_router \
  herdr_browser.test_reports herdr_browser.test_recency \
  herdr_browser.test_dashboard \
  herdr_browser.test_interactive_figures herdr_browser.test_presentation \
  tools.test_install_bundle
```

Native tests use a private Xvfb display, an isolated state directory, and a
disposable Herdr session. See `.github/workflows/check.yml` for the automated
test commands.

## Rebuild the client

The upstream commit is `7b116c05bfda646af39d2524c54e70c751f57ee8`. Apply the patch
to that commit, then use upstream's build instructions:

```bash
git clone https://github.com/herdrdev/herdr.git upstream-herdr
cd upstream-herdr
git checkout 7b116c05bfda646af39d2524c54e70c751f57ee8
git apply ../patches/herdr-0.9.3-browser-context.patch
just build
```

The recorded original build used Rust 1.96.1 and Zig 0.16.0. A new local build is
not guaranteed to produce a byte-identical binary; do not change the `v0`
manifest to silently substitute a different client.

## Package the recorded v0 client

Download and verify the `v0` release, then extract its `client/herdr`. The packager
requires the client hash recorded in `provenance.json`:

```bash
git checkout v0
python3 tools/build_release.py --client /path/to/verified/client/herdr
```

The resulting archive and `SHA256SUMS` are under `dist/`, outside Git tracking.
The packager includes an explicit source tree, not your home directory or
runtime state. It normalizes archive metadata for repeatable packaging.
Check out the tag when reproducing a release; `main` can contain later
source, documentation or test updates. The hashes in `provenance.json` describe
the fixed v0 snapshot, not later changes on `main`.

To test the installer without uploading:

```bash
bash install.sh --from dist --prefix /tmp/herdr-browser-check --skip-deps
```

Use a disposable user/container for `--codex` tests. The option writes that
user's `AGENTS.md`.

## Dashboard example and README preview

```bash
python3 tools/render_dashboard_demo.py
```

This regenerates `docs/dashboard.html` from illustrative records using the
shared renderer. `docs/assets/dashboard.png` is its desktop preview. Keep the
example labeled as sample data; never copy a personal task export into it.

Dashboard records and completed-work history use the existing report SQLite
database. HTTP changes are restricted to the task URL and same origin.
Registration through `herdr-browser agent` resolves the actual originating
Herdr task before writing a record.
