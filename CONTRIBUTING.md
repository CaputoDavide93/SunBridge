# 🤝 Contributing

Thanks for helping. SunBridge was learned from one inverter and one
datalogger, so the most valuable thing you can send is evidence from yours.

## Reports from other hardware

Use the **Hardware report** form under
[New issue](https://github.com/CaputoDavide93/SunBridge/issues/new/choose),
whether SunBridge worked or not. It asks for:

- the inverter model, the datalogger model and its firmware (on the
  datalogger's web page);
- the integration's diagnostics: **Settings → Devices & services →
  SunBridge → ⋮ → Download diagnostics**;
- a few readings from the inverter's own display or the Growatt app, taken
  at the same time as SunBridge's, so a value can be checked against
  something other than itself.

A new register is accepted only with that kind of evidence: it is written up
in [docs/data-layout.md](docs/data-layout.md) and tested against every
captured record (see [docs/development.md](docs/development.md#adding-a-register)).

## Sharing data safely

The datalogger and inverter **serials** identify your installation. Before
anything goes into an issue:

- **Diagnostics** leave out every serial and every connection address
  ([SECURITY.md](SECURITY.md) lists what they hold), so you can attach them
  as they are. Read the file first anyway.
- **Logs:** Home Assistant's log holds your own addresses and host names.
  Replace LAN addresses with documentation ones (`192.0.2.10`) and any
  serial with `DLGEXAMPLE` (datalogger) or `INVEXAMPLE` (inverter).
- **Raw traffic** (a packet capture of port 5279) carries both serials in
  every record: the "scrambling" is a fixed XOR, not encryption. Never
  attach one to an issue. Say in the issue that you have one, and the
  maintainer will arrange a private way to share it.
- **Screenshots:** the datalogger's device page shows its serial; crop it
  or cover it.

Security problems never go in a public issue: report them privately, as
[SECURITY.md](SECURITY.md) explains.

## Bugs and ideas

The **Bug report** and **Feature request** forms ask for what is needed to
act on them. Questions are welcome too, through the **Question** form: read
[docs/faq.md](docs/faq.md) and [docs/troubleshooting.md](docs/troubleshooting.md)
first, since most answers are there.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.14 (uv fetches it if
missing).

```bash
make venv      # .venv with the pinned Home Assistant, pytest, ruff and mypy
make check     # ruff, strict mypy, pytest with the coverage gate, generated docs current
make hassfest  # Home Assistant's own validation (needs Docker)
```

`make check` is what CI's main job runs; CI also runs the suite on the
oldest supported Home Assistant and the newest release. A pull request
needs it green, plus:

- a test that covers the change and failed before it;
- documentation that is true of the code: the entity tables and the
  diagrams are generated (`make diagram`), never edited by hand;
- an entry in [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`, for
  anything a user would notice;
- no serials, LAN addresses or private diagnostics in code, tests, docs,
  screenshots or commit messages: tests read the serials from the captured
  fixtures, and examples use `DLGEXAMPLE`, `INVEXAMPLE` and `192.0.2.x`.

Commits are small and conventional (`fix:`, `feat:`, `docs:`, `ci:`,
`chore:`, `refactor:`).

## Rules that do not bend

- **Clean room.** The protocol comes only from captured traffic and Home
  Assistant's own history of the same inverter. Do not copy or adapt code
  from another project that reads these dataloggers, and do not send a
  change based on one.
- **Hands off the inverter.** SunBridge sends only the acknowledgement, the
  ping echo and the datalogger's clock; `tests/test_outbound_is_bounded.py`
  pins that list. A change that writes anything else is not accepted.
- **Stable identifiers.** The domain `sunbridge`, the unique ids and the
  device identifiers never change: renaming them orphans every user's
  entities and history.

## Code of conduct

Be kind and assume good faith. The
[code of conduct](.github/CODE_OF_CONDUCT.md) applies to issues, pull
requests and every other project space.
