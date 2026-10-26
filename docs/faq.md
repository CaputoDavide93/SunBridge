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

By hand, once: **Settings → Dashboards → Energy → Solar panels → Add solar
production** and pick **Energy total** of **Solar Inverter**
(`sensor.solar_inverter_energy_total`). SunBridge never changes the Energy
settings itself. Remove any other source for the same inverter first, or
its energy is counted twice.

Two rules keep the total honest: buffered records (replays of old data)
never reach it, and a single reading the inverter could not have made (lower
than the last, or more than 50 kW could have added since) is ignored, even
across restarts. Three live readings in a row that agree are accepted as a
reset, and the dashboard starts a new cycle. Details in
[setup.md](setup.md#4-energy-dashboard).

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
upload intervals without a live record (never sooner than 15 minutes), or two
minutes after the datalogger's last connection closes, the live sensors turn
unavailable, and the energy totals and the last reading time keep their last
value. A datalogger silent at night raises no Repairs notice; one silent
through three hours of daylight (added up across days), or for 24 hours,
does, restarts and reloads included.

### When does "Energy today" reset?

At Home Assistant's local midnight, in its time zone, daylight saving
included: it shows 0 from then until the first live reading of the new day.
The value is the inverter's own daily counter, shown as it comes; the moment
the inverter itself resets it (midnight by its clock, or start-up in the
morning) has not been verified, and SunBridge sets the datalogger's clock in
UTC. For the Energy dashboard use **Energy total**, not Energy today.

### I replaced my inverter or datalogger. What happens?

The new one appears as a new device. While the old device is still there,
the new one is named with its serial (for example **Solar Inverter
INVEXAMPLE**) so the two never share a name. Delete the old one from its
device page (**⋮ → Delete**) once it no longer reports: SunBridge refuses to
delete a device that is connected or reporting live values. The allow-list
refuses a new datalogger until its serial is added under **Configure →
Advanced** (switching back to Simple keeps the list). A new inverter has
sensors of its own, so add its **Energy total** to the Energy dashboard
too.

### I upgraded from 0.1.0. Which dataloggers does SunBridge answer?

0.1.0 had no allow-list in Simple mode, so it answered any datalogger. On
upgrade, an entry that knows exactly one datalogger (its device is on the
SunBridge page) puts that serial in its allow-list, and from then on answers
only that one. An entry that knows none, or more than one, keeps answering
any datalogger and raises a notice in **Settings → System → Repairs**: set
the list yourself under **Configure → Advanced**, and the notice goes away.
Advanced entries keep the list they had.

### Can SunBridge change the upload interval?

No. It is set on the datalogger's own web page (Data Transfer Interval). The
Advanced setting only tells SunBridge what to expect, so live values are
marked out of date at the right time.

## Security

### Is the connection secure?

No: the datalogger's protocol has no authentication and its scrambling is a
fixed XOR. Keep the port on your local network, never forwarded from the
internet. See [SECURITY.md](../SECURITY.md).

### How do I remove SunBridge?

Point the datalogger back at the cloud first, then delete the integration,
uninstall it and restart. What stays (history, statistics) is in
[Removing SunBridge](setup.md#removing-sunbridge).
