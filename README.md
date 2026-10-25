<div align="center">

<img src="docs/assets/brand/icon.svg" width="96" alt="SunBridge icon: a sun beside a house, joined by an arc of dots">

# ☀️ SunBridge

**Home Assistant becomes the server your Growatt ShineLan-X datalogger uploads to: local solar data, no cloud, no MQTT, no container**

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![Home Assistant](https://img.shields.io/badge/Home_Assistant-2026.9-41BDF5?logo=homeassistant&logoColor=white)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5)](https://hacs.xyz)
![License](https://img.shields.io/badge/License-MIT-green)
[![Tests](https://github.com/CaputoDavide93/SunBridge/actions/workflows/tests.yaml/badge.svg)](https://github.com/CaputoDavide93/SunBridge/actions/workflows/tests.yaml)

</div>

---

## ✨ Features

| | Feature | What it does |
|---|---|---|
| 📡 | A server inside Home Assistant | Listens on TCP (port 5279 by default) for the datalogger's uploads, acknowledges `0x03`, `0x04` and `0x50` records as the datalogger expects, and echoes its `0x16` pings |
| 🕰️ | Clock kept right | Sets the datalogger's clock to UTC after every announce, byte for byte as the server it replaces did |
| ⚡ | Live sensors | Power, PV voltage, inverter temperature and status from every live record, each matched against Home Assistant's own history of the same inverter |
| 🔋 | Energy dashboard ready | Energy today and a lifetime total that never goes down, even across restarts, so nothing is counted twice |
| 🗃️ | Old data stays old | Buffered records (stored while the server was unreachable) are acknowledged and counted, never shown as current |
| 🌙 | Night aware | Live sensors go unavailable when the inverter stops reporting; totals and the last reading time keep their value |
| 🔌 | Robust link | Records split or joined across reads, bad CRCs dropped and counted, several connections at once, reconnects, idle sockets closed |
| 🧭 | Guided setup | Detects Home Assistant's address, shows the exact datalogger settings, waits for the datalogger and shows its serials |
| ⚙️ | Simple or Advanced | Simple is the port and nothing else. Advanced adds PV current and power, grid voltage, frequency and current, working time, a serial allow-list and the upload interval |
| 🩺 | Diagnostics | Counts per record type, CRC failures, the last frame of each type and open connections, with every serial removed |

<!-- AUTOGEN:status -->
**Status: new.** Version 0.1.0.
<!-- /AUTOGEN:status -->
Tested on one Growatt **MIC 2500TL-X** single-phase inverter with a
**ShineLan-X** (Ethernet) datalogger on firmware **3.6.0.2**, and only
against that pair's captured traffic. Other Growatt dataloggers and inverters
may use other layouts. This is an independent project, not affiliated with
or endorsed by Growatt.

**What it does not do:** relay data to Growatt's cloud (while the datalogger
points at Home Assistant, the Growatt app gets nothing new), update the
datalogger's firmware, change anything on the datalogger except its clock,
write to the inverter, or read batteries, meters or three-phase inverters (never tested).

---

## 🗺️ Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-dark.svg">
  <img src="docs/assets/architecture-light.svg" width="100%" alt="The inverter's data reaches the ShineLan-X datalogger, which uploads records over TCP port 5279 to SunBridge inside Home Assistant; SunBridge acknowledges its announce and data records, echoes its pings, sets the datalogger's clock, and turns live records into sensors for the Energy dashboard.">
</picture>

The datalogger keeps one TCP connection open and sends a ping about every two
minutes and a live record every upload interval (five minutes by default).
SunBridge acknowledges the announce and data records, echoes the pings,
leaves the datalogger's answers unanswered, and decodes the live records into
sensors. It writes nothing else to the datalogger: no settings, no
upload interval. [How it works](docs/how-it-works.md) has the details.

---

## 🚀 Quick Start

1. **Install.**
   In HACS: **⋮ → Custom repositories**, add
   `https://github.com/CaputoDavide93/SunBridge` as an **Integration**, then
   download **SunBridge** and restart Home Assistant.
2. **Add it:** **Settings → Devices & services → Add integration →
   SunBridge**. The setup walks you through the rest: Simple or Advanced, the
   port (5279), the exact settings for the datalogger's web page with Home
   Assistant's address filled in, then it waits for the datalogger and shows
   what it found.
3. **Energy dashboard:** add `sensor.solar_inverter_energy_total` as solar
   production, after removing any other source for the same inverter.

Step by step, with every screen and a checklist: [docs/setup.md](docs/setup.md).

---

## 📚 Documentation

| Guide | What it covers |
|---|---|
| [Setup](docs/setup.md) | Before you start, installing, every setup screen, pointing the datalogger, going back to the cloud |
| [Entities](docs/entities.md) | Every sensor, generated from the code |
| [How it works](docs/how-it-works.md) | The connection, live versus buffered, the energy totals, night |
| [Troubleshooting](docs/troubleshooting.md) | The datalogger's light, symptoms first, then debug logging |
| [FAQ](docs/faq.md) | The Growatt app, firmware updates, buffered records, the Energy dashboard |
| [Protocol notes](docs/protocol-notes.md) | The wire format, from captured traffic |
| [Data layout](docs/data-layout.md) | Where each value sits in a record, and the evidence |
| [Development](docs/development.md) | Checks, layout, tests, fixtures, adding a register |

---

## ⚙️ Configuration

| Option | Default | Mode | What it does |
|---|---|---|---|
| Port | 5279 | both | TCP port to listen on, checked at setup |
| Setup mode | Simple | both | Simple: the core sensors. Advanced: the extra sensors and the two options below |
| Allowed datalogger serials | any | Advanced | Close connections from any other datalogger before answering them |
| Datalogger upload interval | 5 min (1 to 10) | Advanced | What the datalogger is set to on its own web page. Live sensors go unavailable after three intervals, never sooner than 15 minutes. SunBridge does not change the datalogger |

---

## 📖 Usage

<!-- AUTOGEN:entities -->
| Entity | Default entity id | Unit | Behaviour | Mode |
|---|---|---|---|---|
| Power | `sensor.solar_inverter_power` | W | Live: unavailable after the stale window | Simple |
| PV voltage | `sensor.solar_inverter_pv_voltage` | V | Live: unavailable after the stale window | Simple |
| Temperature | `sensor.solar_inverter_temperature` | °C | Live: unavailable after the stale window | Simple |
| Status | `sensor.solar_inverter_status` | waiting / producing / fault | Live: unavailable after the stale window | Simple |
| PV current | `sensor.solar_inverter_pv_current` | A | Live: unavailable after the stale window | Advanced |
| PV power | `sensor.solar_inverter_pv_power` | W | Live: unavailable after the stale window | Advanced |
| Grid voltage | `sensor.solar_inverter_grid_voltage` | V | Live: unavailable after the stale window | Advanced |
| Grid frequency | `sensor.solar_inverter_grid_frequency` | Hz | Live: unavailable after the stale window | Advanced |
| Grid current | `sensor.solar_inverter_grid_current` | A | Live: unavailable after the stale window | Advanced |
| Second temperature | `sensor.solar_inverter_second_temperature` | °C | Live: unavailable after the stale window | Advanced, disabled (unverified) |
| Energy today | `sensor.solar_inverter_energy_today` | kWh | Keeps its last value, total increasing | Simple |
| Energy total | `sensor.solar_inverter_energy_total` | kWh | Keeps its last value, total increasing | Simple |
| Last reading | `sensor.solar_inverter_last_reading` | timestamp | Keeps its last value | Simple |
| Working time | `sensor.solar_inverter_working_time` | h | Keeps its last value, total increasing | Advanced |
| Connected | `binary_sensor.solar_datalogger_connected` | on / off | On while an open connection from it has sent a decodable record | Simple |
<!-- /AUTOGEN:entities -->

The power sensor's id contains `solar` and `inverter` and it is a power
sensor, with energy today beside it, so dashboards that discover solar by name
and device class find it.

---

## 🛠️ Troubleshooting

<details>
<summary><b>Setup says "No Datalogger Yet"</b></summary>

Nothing readable arrived while setup listened. At night that is normal (the
datalogger is off): choose **Finish now**. In daylight, check the datalogger's
Server IP, Server port, ResolvDomain OFF, and that it was rebooted, then
**Try again**. Its light, and more symptoms, in
[docs/troubleshooting.md](docs/troubleshooting.md).

</details>

<details>
<summary><b>Connected stays off</b></summary>

The datalogger is not reaching Home Assistant: check its server IP and port,
and anything between them. In Advanced mode, a serial missing from the
allow-list is closed at once.

</details>

<details>
<summary><b>Not verified yet</b></summary>

Tested on one MIC 2500TL-X with a ShineLan-X on firmware 3.6.0.2. Never seen
in its captured traffic, so unverified:

- status code 3 (fault);
- the high word of the 32-bit power and energy values (always 0);
- the second temperature's meaning (register 3097), so it starts disabled;
- the moment the inverter resets energy today;
- changing the upload interval over the TCP link, so SunBridge never tries:
  it is set on the datalogger's own web page.

</details>

---

## 📁 Repo structure

```text
SunBridge/
├── custom_components/
│   └── sunbridge/             # 🧩 the integration: protocol, decoder, server, hub, sensors, flows
│       └── brand/             # 🎨 icons shown in Home Assistant and HACS
├── tests/                     # 🧪 pytest against Home Assistant 2026.9.4
│   └── fixtures/captured/     # 📼 a real datalogger's traffic (serials replaced) and HA's history
├── tools/
│   ├── gen_diagram.py         # 🗺️ draws docs/assets/*.svg
│   └── gen_docs.py            # 🤖 the generated tables and values in the docs
├── docs/                      # 📚 setup, FAQ, entities, how it works, protocol, data layout
│   └── assets/                # 🖼️ the diagrams, light and dark, and the icon's SVG source
├── .github/                   # 🤖 tests, Hassfest and HACS workflows, dependabot
├── Makefile                   # 🔧 venv, check, test, lint, diagram, hassfest
├── hacs.json  pyproject.toml  pytest.ini  requirements_test.txt
└── CHANGELOG.md  LICENSE  SECURITY.md
```

---

## 🧪 Testing

```bash
make venv     # Home Assistant 2026.9.4 test environment, built with uv
make check    # ruff, pytest, diagrams and generated docs current
make hassfest # Home Assistant's own validation (needs Docker)
```

Every captured record is checked: CRCs, descrambling, the stream split at
every read size, acknowledgements and clock commands byte for byte against the
captured replies, and each live record's decoded values against Home
Assistant's history of the same moment. End-to-end tests run a fake
datalogger over a real localhost socket, through the whole setup and the
listener.

---

## 📦 Deployment

HACS installs and updates it from this repository's releases. Each release is
listed in [CHANGELOG.md](CHANGELOG.md); restart Home Assistant after an
update.

---

## 🔒 Security

The datalogger's protocol has no authentication: keep the port on the LAN.
Diagnostics carry no serials. See [SECURITY.md](SECURITY.md).

---

## 📄 License

MIT, see [LICENSE](LICENSE). The protocol was learned only from captured
traffic of the author's own datalogger; no other project's code was read or
used.

An independent project, not affiliated with or endorsed by Growatt. Growatt
and ShineLan are names of their owner, used here only to say what this
integration works with.

---

<p align="center"><sub>Made with ❤️ by <a href="https://github.com/CaputoDavide93">Davide Caputo</a></sub></p>
