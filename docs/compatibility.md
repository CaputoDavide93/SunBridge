# 🔌 Compatibility

SunBridge was written from, and is tested against, one real installation.
Everything else is listed honestly as unknown until someone reports it.

| Inverter | Datalogger | Firmware | Status | Evidence |
|---|---|---|---|---|
| Growatt MIC 2500TL-X (single phase) | ShineLan-X (Ethernet) | 3.6.0.2 | ✅ Tested | Captured traffic and Home Assistant's own history of the same hours, pinned by the test suite |
| Other MIC TL-X sizes | ShineLan-X | any | ❔ Unknown | The register layout may differ |
| Any Growatt single-phase inverter | Other dataloggers with a **Server IP** setting (ShineWiFi models, for example) | any | ❔ Unknown | Likely the same protocol, never seen |
| Three-phase inverters, batteries, meters | any | any | ❌ Not read | Never tested |

**Statuses.** *Tested* means the maintainer ran it and compared every value
with the inverter. *Reported* means a hardware report (below) says it works and
shows the readings side by side; no report has been received yet, so nothing is
listed as reported. *Unknown* means nobody has tried it.

## Submit a hardware report

Open the
[hardware report form](https://github.com/CaputoDavide93/SunBridge-Growatt-Home-Assistant/issues/new?template=hardware_report.yml).
It asks for:

1. The inverter model, and the datalogger model and firmware (on its web page).
2. The SunBridge and Home Assistant versions.
3. Whether it worked, including "partly" and "never found the datalogger".
4. A few readings side by side with the inverter's display or the Growatt app.
5. The downloaded diagnostics file (it carries no serials or addresses).

A report that it did **not** work is as useful as one that did. Never attach a
raw packet capture to a public issue: every record holds both serials. Say you
have one and a private way to share it will be arranged.
