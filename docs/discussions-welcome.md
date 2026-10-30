# Draft: GitHub Discussions welcome post

Not posted. Discussions are currently off (see `.github/ISSUE_TEMPLATE/config.yml`);
if they are turned on, post this under Announcements and pin it.

---

**Title:** Welcome to SunBridge

SunBridge turns Home Assistant into the server your Growatt ShineLan-X
datalogger uploads to. No Growatt account, no cloud, no MQTT broker, no
container: the datalogger already sends its readings over your network, and
SunBridge receives them inside Home Assistant.

**Where it stands.** It is new. One hardware pair is verified: a MIC 2500TL-X
inverter on a ShineLan-X, firmware 3.6.0.2. Everything else is unknown, not
broken. See [compatibility](../docs/compatibility.md).

**How you can help most.**

- Run it on different hardware and send a
  [hardware report](https://github.com/CaputoDavide93/SunBridge-Growatt-Home-Assistant/issues/new?template=hardware_report.yml),
  including when it did not work.
- Say what is unclear in the setup guide.
- Ask questions here. Bugs and feature requests belong in issues.

**Good to know.** While the datalogger points at Home Assistant, the Growatt
app gets no new data; you can point it back at any time. SunBridge does not
write to the inverter.

Thanks for trying it.
