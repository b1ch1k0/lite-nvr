# Contributing

- Run it locally: `sudo ./setup.sh` on a throw-away VM/container (Ubuntu 24.04 or Rocky 9).
- After code changes: `sudo ./deploy/install.sh` re-applies everything (data is kept).
- Keep it light: the target box is 2 vCPU / 2 GB. No re-encoding of main streams.
- Translations: templates are in `app/templates/` — an English UI is the most wanted contribution.
- Releases: bump `app/__init__.py`, update `CHANGELOG.md`, tag `vX.Y.Z` — GitHub Actions builds the tarball and the RPM.
