# v0 validation

The local release checks passed on Ubuntu 22.04 x86_64. The clean environment
used the public Ubuntu 22.04 container image, a new unprivileged user, and Xvfb
for native GUI checks. [Recorded results](checks.json) and the
[visual release summary](v0.html) contain the evidence snapshot.

| Check | Result |
| --- | --- |
| Unit suite | 69 tests passed |
| Native UI | 40 contracts passed; task switching, live reports, clipboard and zoom |
| Real-client integration | Two patched clients against the original Herdr 0.9.3 server passed |
| Desktop behavior | An actual external xdg-open kept the previous browser; Herdr links opened in their originating task |
| Update behavior | Applying an update preserved the server, selected task and browser opt-out |
| Clean install | System dependencies, launcher, payload integrity and Codex instruction preservation passed |
| Reinstall/failures | Backup, paths with spaces, repeat install, corrupt download and unsafe archive rejection passed |
| Public-content inspection | Private task adapters/data excluded; no credential markers or internal service references found in the public source |
| HTML overview | Desktop and phone rendering passed, including expanded sections |

The minimal image exposed a missing `gio` dependency during testing. The
installer now explicitly installs `libglib2.0-bin`.

Physical recipient desktops, other Linux releases, ARM, macOS and Windows
remain unverified. The client is the recorded prebuilt snapshot; a new client
build has not been proven byte-for-byte reproducible. Public download
verification is recorded on the repository's main branch after publication.
