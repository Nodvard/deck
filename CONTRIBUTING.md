# Feedback for Nodvard Deck

Thanks for your interest in Nodvard Deck! Bug reports, ideas and hints about the documentation are
very welcome.

## How to help

- **Bugs:** open an issue using the *Bug report* template. Include your Nodvard Deck version,
  how you run it (Docker on Pi / x86) and the steps to reproduce.
- **Ideas and features:** open an issue using the *Feature request* template.
- **Documentation:** if something is unclear or wrong, open an issue and say where.
- **Security issues:** never in public issues — see [SECURITY.md](SECURITY.md).

## Code contributions

Nodvard Deck is developed by its maintainer alone and **does not accept code contributions**
(pull requests or patches) from outside. This keeps the licensing simple: the project is offered
under the PolyForm Noncommercial License and, separately, under a commercial license. Please
describe problems and proposed fixes in words in an issue instead of sending code; pull requests
will be closed without review.

You are welcome to build and adapt Nodvard Deck for your own noncommercial use under the terms of
the [LICENSE](LICENSE). The sections below help with that.

## Development setup

See the *Development* section of the [README](README.en.md). In short:

```bash
python -m venv .venv
.venv/bin/pip install -c deploy/constraints.txt -e sdk/python -e "backend[dev]"
git config core.hooksPath .githooks   # pre-commit hook: rebuilds changed extension frontends
cd frontend && npm ci
```

## Checks for your own changes

If you change the code for yourself, these checks show whether everything still works:

```bash
.venv/bin/pytest sdk/python/tests backend/tests
python scripts/check_core_purity.py
node scripts/build_extension_frontends.mjs --check
cd frontend
npm run typecheck && npm run typecheck:extensions
npm test && npm run test:extensions
```

## How the code is organized

- **The core stays vendor-neutral.** Anything specific to one product (Proxmox, Docker,
  Pi-hole, …) belongs in an extension under `extensions/`. `scripts/check_core_purity.py`
  enforces this.
- **Extensions only talk to the core through `nodvard_sdk`.** See
  [docs/en/EXTENSION-API.md](docs/en/EXTENSION-API.md).
- **Changes to managed systems go through the gate.** Actions that modify a server, VM or
  container are proposed and approved — never executed silently.
- **Database changes need an Alembic migration.**
- **Extension frontends are bundled.** After changing an extension's frontend, rebuild with
  `node scripts/build_extension_frontends.mjs`.
- **Dependencies need a permissive license.** Allowed: MIT, BSD, Apache-2.0, ISC, PSF,
  MPL-2.0 (unmodified). A few reviewed exceptions, such as EPL-2.0 for `asyncssh` (used
  unmodified), are listed with their reasons in `scripts/check_licenses.py`. GPL, AGPL, LGPL,
  SSPL or unknown licenses are rejected by the license check. Bundled licenses are listed in
  `THIRD_PARTY_LICENSES`.
- **No secrets or personal data** in code, tests or fixtures. Use example values such as
  `192.168.1.10`, `admin` and `example.com`.

## Code of conduct

Please be respectful. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
