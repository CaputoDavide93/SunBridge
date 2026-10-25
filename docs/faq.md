# ❓ FAQ

<details>
<summary><b>Can I still use the Growatt app?</b></summary>

Not for new data. The datalogger uploads to one server: while it points at
Home Assistant, Growatt's cloud receives nothing new. To go back, see
[Going back to Growatt's cloud](setup.md#going-back-to-growatts-cloud).

</details>

<details>
<summary><b>How does the datalogger get firmware updates?</b></summary>

From Growatt's cloud only. SunBridge never sends firmware or settings. To
update, point the datalogger back at the cloud for a while (ResolvDomain ON,
the original server values, reboot), let it update, then set it to Home
Assistant again. A new firmware may change the data layout: the
[data layout](data-layout.md) was checked on firmware 3.6.0.2.

</details>

<details>
<summary><b>What are buffered records?</b></summary>

When the datalogger cannot reach its server it stores records, and replays
them (record type `0x50`) once it can. They carry old values, so SunBridge
acknowledges and counts them but never shows them as current readings. They
appear under `records.by_type` in diagnostics.

</details>

<details>
<summary><b>How do I add it to the Energy dashboard?</b></summary>

**Settings → Dashboards → Energy → Solar panels → Add solar production** and
pick `sensor.solar_inverter_energy_total`. It never goes down, even across
restarts, so the dashboard cannot count the same energy twice. Remove any
other source for the same inverter first.

</details>

<details>
<summary><b>When does "Energy today" reset?</b></summary>

When the inverter resets it: SunBridge shows the inverter's own counter. The
exact moment (midnight by the inverter's clock, or start-up in the morning)
has not been verified.

</details>

<details>
<summary><b>Can SunBridge change the upload interval?</b></summary>

No. It is set on the datalogger's own web page (Data Transfer Interval). The
Advanced setting only tells SunBridge what to expect, so live values are
marked out of date at the right time.

</details>

<details>
<summary><b>Is the connection secure?</b></summary>

No: the datalogger's protocol has no authentication and its scrambling is a
fixed XOR. Keep the port on your local network, never forwarded from the
internet. See [SECURITY.md](../SECURITY.md).

</details>
