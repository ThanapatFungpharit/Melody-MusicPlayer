# Contributing to Melody

Start with [architecture and invariants](docs/architecture.md) and
[release/dependency policy](docs/releases-and-dependencies.md). Preserve named
capabilities and state owners; file or line-count reduction alone is not an
architectural improvement.

## Local validation

```console
uv sync --locked --group build
uv run --locked python -m unittest discover -s tests/unit -t . -v
uv run --locked python -m unittest discover -s tests/integration -t . -v
uv run --locked python -m unittest discover -s tests/ui -t . -v
uv run --locked --group build python -m unittest discover -s tests/packaging -t . -v
uv run --locked ruff check src tests tools packages/flet_background_audio/src/flet_background_audio
uv run --locked ruff format --check src tests tools packages/flet_background_audio/src/flet_background_audio
uv run --locked --group build ty check src tests tools
```

The unit suite is the fast default. Integration tests use temporary storage and
fake network responses. UI tests build headless controls. Packaging tests inspect
release contracts and compiled presentations; they do not build signed native
apps. Use `uv run --locked --group build python -m unittest discover -s tests -t . -v` to run all four groups.
Shared fixtures live in `tests/ui_support.py`; new tests belong in the relevant
group. Tests must not use the user's library, live cookies, or external network.

CI runs quality checks and these groups independently on pull requests and main
or master changes. Unit/integration/UI validation covers Python 3.12 and 3.13 on
Linux and Windows; packaging validation uses the packaged Python 3.13 baseline.
Native builds are a separate release workflow and depend on validation.

For stateful changes, test failure boundaries: persistence failing after bytes
are written, a callback from an obsolete source, simultaneous commands,
cancellation just before completion, and restart with an interrupted journal.
Avoid tests that merely repeat an implementation detail or require live media
downloads. Include native-device checks for background audio changes; headless
tests cannot validate OS audio focus, process recreation, or lock-screen controls.

## Licensing status

No project license has been selected in this checkout. This documentation does
not grant new modification or redistribution permissions. The maintainer must
choose a license for Melody and its local bridge before presenting the project
as licensed open source. Third-party components retain their own licenses.
