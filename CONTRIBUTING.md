# Development

Work from the mattergram directory. Use Python 3.14 for migration tooling; the
SDK supports Python 3.11+. Tests currently pass on Windows/Python 3.11, 3.13 and
3.14; the complete OS/dependency compatibility matrix remains open.

```sh
uv sync --locked
uv run --locked ruff check --config pyproject.toml tools src tests contracts examples
uv run --locked ruff format --check --config pyproject.toml tools src tests contracts examples
uv run --locked mypy
uv run --locked pytest
```

Limit formatting to maintained paths and pass the project configuration explicitly.
Never format, import, execute or edit the donor snapshots. The tools test suite is
offline and uses isolated temporary fixtures. Add behavior tests for source changes,
missing dependencies, guarded/dynamic imports and repeatability when extending tools.

Restore and inspect the pinned donors:

```sh
uv run --locked python -m tools.fetch_upstream
uv run --locked python -m tools.inventory_upstream --source aiogram
uv run --locked python -m tools.build_manifest --check
uv run --locked python -m tools.fetch_upstream --verify
```

Restoration requires network access to GitHub and raw.githubusercontent.com. Existing
snapshots are verified and never silently replaced. `--verify` uses no network.
`--create-lock` is for an absent lock and refuses to overwrite one. Upstream updates
require a separate reviewed lock and manifest; do not change commits in place.

The complete inventory is `build/inventory/aiogram.json`. Use `--focus
aiogram.dispatcher.event.handler` for a focused file and dependency paths. The reviewed
scope is `manifests/core-plan.json`; `python -m tools.build_manifest` regenerates
`manifests/core.json` with provenance. Review both the decision changes and generated
diff. The graph is static and reports its limits; linked tests are not coverage proof.

The extraction/codemod/audit commands and their ownership boundaries are documented
in [porting-tools.md](docs/porting-tools.md). `python -m tools.verify_extraction`
checks repeated extraction of the real donor allowlist and unchanged maintained
source. `python -m tools.promote_core --check` requires a passing strict audit and
exact maintained-source replay; native boundaries are hash-pinned read-only inputs.
`python -m tools.verify_behavioral_port` checks all 13 maintained upstream suite
adaptations. The current selected core passes both gates; this is scoped readiness,
not approval of unimplemented API/UI/dialog features.

Build and verify consumer installs:

```sh
uv run --locked python -m build
uv run --locked twine check --strict dist/*
uv run --locked python -m tools.verify_distributions
```

Use a fresh `dist` directory. Verification checks archive content and installs the
wheel in a temporary virtual environment outside checkout, then repeats with a wheel
rebuilt from sdist. It verifies package identity, import location and typing resources,
then runs 44 copied model/routing/FSM/local HTTP+WS tests against the installed
artifact without editable mode. It also asserts the base install has no Redis dependency, installs the optional extra and runs configuration checks. With an explicit real Redis profile, all 13 native Redis cases must execute without skips for both installed artifacts. Real Mattermost integration is checked separately.

CI executes the same commands and retains failure reports. It has no publishing
permission. Repository hosting, required checks and environments are not configured
by the YAML file alone. The real Mattermost profile is documented in
`docs/native-client.md` and `docs/native-routing.md` and has its own CI job, including
SDK-owned WS routing. The release workflow remains pending; an absent stand is not
a passing integration. No external GitHub run has been observed yet.

Before publishing, resolve E7 schema licensing, validate package names again, set up
the repository and Trusted Publishers, then verify TestPyPI and PyPI installs of the
same exact artifacts. No release command is provided until that path is implemented.

The Redis/Memory profiles and transaction/lease boundaries are documented in
[native-redis.md](docs/native-redis.md). Start the explicitly owned compose service
and run `uv run --locked python -m tools.verify_redis_stand --mattermost --distributions`
after building current distributions. Default pytest skips real Redis cases without
MATTERGRAM_REDIS_URL; the mandatory Redis verifier/CI job rejects skipped cases.
