# ❓ FAQ

Short answers to the questions people ask before and after installing
SunBridge. Setup, step by step, is in [setup.md](setup.md); problems are in
[troubleshooting.md](troubleshooting.md).

## Getting started

### What is SunBridge?

SunBridge is a free, open-source (MIT) Home Assistant integration that reads
a Growatt solar inverter locally through its ShineLan-X datalogger. You point
the datalogger at Home Assistant instead of Growatt's cloud, and SunBridge
receives its uploads, answers them as the cloud server would, and turns them
into sensors for power, voltage, temperature, status and energy.

### Does SunBridge work without internet?

Yes. The datalogger uploads to Home Assistant over your local network, and
SunBridge answers it there, so nothing on the way needs the internet. You
need the internet only to install SunBridge, and to update the datalogger's
firmware (see below).

### Do I need a Growatt account or an installer code?

No. SunBridge never talks to Growatt's cloud, so it needs no account, no
login and no installer code. You need only the datalogger's own web page
login, which is printed on its label.

### Do I need MQTT, a container or extra hardware?

No. SunBridge runs inside Home Assistant itself: no MQTT broker, no proxy
container, no RS485 adapter or wiring to the inverter. The datalogger you
already have does the talking.

### How do I install it?

Install it through HACS as a custom repository, then add it from **Settings →
Devices & services**.

A guided setup shows the exact settings for the datalogger's web page and
waits until the datalogger connects. Every screen is in [setup.md](setup.md).

## Hardware

### Which inverters and dataloggers work?

Tested: a Growatt **MIC 2500TL-X** single-phase inverter with a
**ShineLan-X** datalogger on firmware **3.6.0.2**. Other Growatt single-phase
inverters (other MIC TL-X sizes, for example) are unverified, because their
register layout may differ. Batteries, meters and three-phase inverters are
not read. The full table is in the [README](../README.md#supported-hardware).

### Does it work with ShineWiFi-X or other Growatt dataloggers?

Likely, but unverified. A datalogger should work if its web page has a
**Server IP** setting and it speaks the same protocol as the ShineLan-X.
Nobody has tested one yet. If you try it,
[open an issue](https://github.com/CaputoDavide93/SunBridge/issues) with the
model and firmware, whether it worked or not.

### Is it safe for my inverter?

SunBridge never writes to the inverter and never changes its settings. It
sends exactly three things, all to the datalogger: the echo of its ping, a
one-byte acknowledgement of each record, and the datalogger's clock after it
connects. `tests/test_outbound_is_bounded.py` pins that list, so a fourth
kind of message cannot appear without a deliberate change to the test.

## The Growatt cloud

### Can I still use the Growatt app?

Not for new data. The datalogger uploads to one server: while it points at
Home Assistant, Growatt's cloud receives nothing new, and the app shows the
last data it had.

### Can I go back to the Growatt cloud?

Yes, at any time. On the datalogger's web page, turn **ResolvDomain** back
on, put back the original server values and reboot it. The steps are in
[Going back to Growatt's cloud](setup.md#going-back-to-growatts-cloud).

### How does the datalogger get firmware updates?

From Growatt's cloud only. SunBridge never sends firmware or settings. To
update, point the datalogger back at the cloud for a while (ResolvDomain ON,
the original server values, reboot), let it update, then set it to Home
Assistant again. A new firmware may change the data layout: the
[data layout](data-layout.md) was checked on firmware 3.6.0.2.

## Data and the Energy dashboard

### How do I add it to the Energy dashboard?

**Settings → Dashboards → Energy → Solar panels → Add solar production** and
pick `sensor.solar_inverter_energy_total`. It never goes down, even across
restarts, so the dashboard cannot count the same energy twice. Remove any
other source for the same inverter first.

### How often does it update?

Every time the datalogger uploads a live record: every five minutes by
default. The interval is set on the datalogger's own web page (Data Transfer
Interval); SunBridge's Advanced mode accepts 1 to 10 minutes.

### What happens when Home Assistant restarts or is offline?

The datalogger stores its records while it cannot reach Home Assistant, and
replays them as buffered records when it reconnects. SunBridge acknowledges
and counts them but never shows them as current, so the power graph keeps a
gap rather than old values. The lifetime total catches up with the next live
record, because it is the inverter's own counter.

### What are buffered records?

When the datalogger cannot reach its server it stores records, and replays
them (record type `0x50`) once it can. They carry old values, so SunBridge
acknowledges and counts them but never shows them as current readings. They
appear under `records.by_type` in diagnostics.

### What happens at night?

The datalogger is powered by the inverter, so it stops at night. After three
upload intervals without a live record (never sooner than 15 minutes), the
live sensors turn unavailable, and the energy totals keep their last value.

### When does "Energy today" reset?

When the inverter resets it: SunBridge shows the inverter's own counter. The
exact moment (midnight by the inverter's clock, or start-up in the morning)
has not been verified.

### Can SunBridge change the upload interval?

No. It is set on the datalogger's own web page (Data Transfer Interval). The
Advanced setting only tells SunBridge what to expect, so live values are
marked out of date at the right time.

## Security

### Is the connection secure?

No: the datalogger's protocol has no authentication and its scrambling is a
fixed XOR. Keep the port on your local network, never forwarded from the
internet. See [SECURITY.md](../SECURITY.md).
