# 🧑‍💻 Development

## Checks

```bash
make venv       # Home Assistant 2026.9.4 test environment, built with uv
make check      # ruff, mypy, pytest with coverage, diagrams and generated docs current
make hassfest   # Home Assistant's own validation, in Docker
```

| Check | What it enforces | Configured in |
|---|---|---|
| `make lint` | ruff's correctness, import and bug-pattern rules | `pyproject.toml` |
| `make typing` | mypy in strict mode over `custom_components/sunbridge`, against the pinned Home Assistant's own types, with no per-module overrides | `pyproject.toml` |
| `make test` | the suite, with branch coverage of the integration; it fails under 95% | `pyproject.toml` |

## CI

| Workflow | When | What |
|---|---|---|
| `tests.yaml` | push, pull request | `make check`'s steps on the development pin (`requirements_test.txt`), the release notes check, then the suite on the oldest supported Home Assistant (`.github/ha-minimum/`, which must equal `hacs.json`'s `homeassistant`) and the newest one (`.github/ha-latest/`, bumped weekly by Dependabot) |
| `hassfest.yaml` | push, pull request | Home Assistant's manifest and config-flow validation |
| `validate.yaml` | push, pull request | HACS's own repository checks |
| `codeql.yaml` | push, pull request | GitHub's code scanning of the Python and the workflows |
| `dependency-review.yaml` | pull request | refuses a new dependency with a known vulnerability |
| `release.yaml` | a `v*` tag | see [Releasing](#releasing) |

Code scanning, dependency review and HACS's checks run on the public
repository only; GitHub offers them free there.

To run the suite on the other two releases locally:

```bash
uv venv -q --python 3.14 .venv-ha-latest
VIRTUAL_ENV=.venv-ha-latest uv pip install -q -r .github/ha-latest/requirements.txt
.venv-ha-latest/bin/python -m pytest -q
```

`make diagram` redraws the diagrams in `docs/assets/` (the architecture and
the setup flow) and the generated blocks in the README (the status, Simple
or Advanced, and the entities), [entities.md](entities.md) and
[setup.md](setup.md); `make check` fails when any of them is stale.

## Screenshots

```bash
make screenshots   # retakes docs/assets/screenshots/, about three minutes
```

A developer tool, not part of `make check` or CI. On first use it builds
`.venv-screenshots/` (gitignored) from `tools/screenshots/requirements.txt`:
the Home Assistant release the tests pin, its frontend, and Playwright with
Chromium. Then `tools/screenshots/take_screenshots.py`:

1. starts Home Assistant in a new temporary config directory on a free
   localhost port, with SunBridge and `tools/screenshots/screenshot_env/`
   copied into `custom_components/`, and creates the owner through the
   onboarding API;
2. drives the guided setup in the real frontend, photographing each screen
   in the light and the dark theme. SunBridge listens on 15279 there, so a
   real listener on 5279 is never in the way. While the waiting screen
   shows, a fake datalogger (`tools/screenshots/replay.py`) replays the
   captured announce, so setup genuinely finds a datalogger;
3. writes the captured live records' power into the power sensor's history
   (their own values and spacing, the times moved to end as Home Assistant
   started), sends the last live record over the socket, and photographs
   both devices, the power sensor's history, the Energy dashboard's solar
   setting, and the entry's Reconfigure form (opened, never saved).

Only demo data reaches an image. Every captured frame is replayed with its
serials replaced by `DLGEXAMPLE` and `INVEXAMPLE` (re-scrambled, CRC
recomputed). `screenshot_env` replaces the network adapters Home Assistant
reports with one at the documentation address 192.0.2.10, so that is the
address setup detects, and the tool stops before the first picture of the
port screen if it is not. Each PNG is re-saved without metadata and
quantised to 256 colours.

`--keep` leaves the temporary directory (and Home Assistant's log) behind;
`--only setup-port,device-inverter` retakes only the named screens, though
the whole setup still runs. If building `pymicro-vad` or `pyspeex-noise`
fails on macOS, set `SDKROOT` to an SDK that links (one under
`/Library/Developer/CommandLineTools/SDKs/`) for the first run.

### Social preview

`docs/assets/social-preview.png` (1280 × 640, no metadata) is the image
GitHub shows when the repository is linked: the icon from
`docs/assets/brand/icon.svg` on ink `#14233F`, the name with the amber
`#FFC94D` → `#FF7A2F` gradient, and the tagline in paper `#F6F1E7`. GitHub
does not read it from the tree: upload it under **Settings → General →
Social preview**.

## Layout

| File | Role |
|---|---|
| `protocol.py` | CRC, scrambling, stream reassembly, acks and the clock command. No Home Assistant imports |
| `decoder.py` | The register table and every value read from it. No Home Assistant imports |
| `server.py` | The asyncio TCP server: connections, replies, counters. No Home Assistant imports |
| `hub.py` | What Home Assistant may see: live versus buffered, the lifetime floor, staleness, devices, signals |
| `sensor.py`, `binary_sensor.py` | The entities, updated by dispatcher signals |
| `config_flow.py` | Simple and Advanced setup, and the options |
| `diagnostics.py` | Counters and state, with no serials |

## Tests

- `tests/test_protocol.py`: every captured record's CRC and descrambling, the
  stream split at every read size, bad CRCs and garbage, and the acks, ping
  echoes and clock commands byte for byte against the captured replies.
- `tests/test_decoder.py`: every captured live record against Home Assistant's
  history of the same moment, and the plausibility checks.
- `tests/test_listener.py`: a fake datalogger on a real localhost socket
  sending captured records, and the entity states that result.
- `tests/test_config_flow.py`: the whole guided setup, including the waiting
  step listening on a real localhost port for a fake datalogger, the timeout,
  and the port being freed when setup is abandoned.
- `tests/test_init.py`, `tests/test_hacs.py`, `tests/test_docs.py`.

## Fixtures

`tests/fixtures/captured/` holds the traffic of the author's own datalogger
on 25 Oct 2026, about three hours of it:

| File | What it holds |
|---|---|
| `uploads-2026-10-25.jsonl` | Every frame the datalogger sent: the bytes on the wire (`raw`) and the same frame descrambled (`plain`) |
| `replies-2026-10-25.jsonl` | What the server answered each one: acks, ping echoes and clock commands |
| `ha-history-2026-10-25.json` | Home Assistant's own history of the inverter's sensors over the same hours, the ground truth for the decoder |

The tests read both serials from the first captured announce, so no test
names one.

In this repository the two serials are replaced by `DLGEXAMPLE` (datalogger)
and `INVEXAMPLE` (inverter) in every frame, plain and scrambled, with each
CRC recomputed, so every frame is still valid and every test runs against
them unchanged.

The protocol comes only from these captures and from Home Assistant's history;
no other project's source was read or used.

## Releasing

The version lives in three places, and the release refuses unless all three
agree: `custom_components/sunbridge/manifest.json`, the newest section of
[CHANGELOG.md](../CHANGELOG.md), and the tag.

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
   (today's date), drop any empty `###` heading under it, and put a fresh
   empty `## [Unreleased]` above it.
2. Set `"version": "X.Y.Z"` in `manifest.json`.
3. `make release-notes TAG=vX.Y.Z` checks the three agree and prints the
   notes; `make check` must pass. Commit both files
   (`chore: release X.Y.Z`) and merge to `main`.
4. Tag the merged commit and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "SunBridge X.Y.Z"
   git push origin vX.Y.Z
   ```

5. `release.yaml` checks the tag against the manifest and the changelog,
   runs every check on the tagged commit, zips
   `custom_components/sunbridge/` as `sunbridge.zip`, and publishes the
   GitHub release with that section of the changelog as its notes. HACS
   offers the update from there.

Versions follow [Semantic Versioning](https://semver.org/): a change that
renames an entity or needs the user to act is a major one.

## Adding a register

1. Find it in the captures and match it against ground truth (Home
   Assistant's history, or another reading you can trust).
2. Write the evidence into [data-layout.md](data-layout.md).
3. Add a test that runs every captured record.
4. Without ground truth, it is an Advanced sensor; if its meaning is unknown,
   it is also disabled by default.
