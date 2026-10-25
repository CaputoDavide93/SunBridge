# 🛠️ Troubleshooting

Find the symptom, then the cause. Most problems are the datalogger's settings
or the time of day.

## The datalogger's light

The ShineLan-X has one status light. What it says:

| Light | Meaning | What to do |
|---|---|---|
| 🔵 Blue, flashing | Talking to the inverter, the router and the server | Nothing: all good |
| 🟢 Green, flashing | Reaches the router, but no server answers | Check Server IP and port on its web page, ResolvDomain OFF, and that SunBridge is set up on that port |
| 🟢 Green, steady | No network address, or no cable | Check the Ethernet cable and that the router hands out addresses (DHCP) |
| 🔴 Red, flashing | Talking to the inverter only, no network | Check the cable and the router |
| Off | No power | Normal at night: the inverter powers the datalogger |

## Symptoms

<details>
<summary><b>Setup says "No Datalogger Yet"</b></summary>

Nothing SunBridge could read arrived on the port while it waited.

- **At night:** normal. The datalogger is off. Choose **Finish now**: it
  appears in the morning.
- **Server IP** on the datalogger's Network Setting must be the address the
  setup showed, **Server port** the same port, and **ResolvDomain** OFF.
- The datalogger only uses new settings after **Datalogger Setting → Reboot**.
- Its light should go from green flashing to blue flashing within a few
  minutes of the reboot. Then choose **Try again**.

</details>

<details>
<summary><b>Setup says the port is in use</b></summary>

"Something else is already listening": another program on Home Assistant's
host holds the port. Pick another port, and set the same one on the
datalogger. "SunBridge is already listening on port …": an existing SunBridge
entry has it. Change that entry with **Configure** instead of adding a second
one.

</details>

<details>
<summary><b>Connected stays off</b></summary>

The datalogger is not reaching Home Assistant. Check its light (above), then
on its web page that the server IP is Home Assistant's address and the port
matches the integration's. A firewall between them, or another program on the
same port, also stops it: setup refuses a port it cannot bind, but a program
that takes the port later makes the entry retry. In Advanced mode, a serial
missing from the allow-list is closed at once: diagnostics count it as
`rejected_by_allow_list`.

</details>

<details>
<summary><b>Connected is on, but the inverter's sensors are unavailable</b></summary>

Pings and announces arrive, live records do not. At night that is normal: the
inverter is off. In daylight, check `records.by_type` in diagnostics: `0x50`
counting up with no `0x04` means the datalogger is still replaying its buffer,
which it does after time without a server.

</details>

<details>
<summary><b>Nothing at all at night</b></summary>

Normal. The inverter stops when the sun goes down and the datalogger with it.
Live sensors turn unavailable after the stale window; the energy totals and
the last reading time keep their value until morning.

</details>

<details>
<summary><b>The inverter's screen counts down "Connect in …s"</b></summary>

Normal in weak light (morning, evening, heavy cloud): the inverter is waiting
for enough power from the panels before it connects to the grid. No records
with output power arrive until it does.

</details>

<details>
<summary><b>Energy total did not move with a new reading</b></summary>

The reading was lower than the stored total, so it was ignored:
`lifetime_decreases_ignored` in diagnostics counts each one. It moves again
once the inverter reports more than the stored value.

</details>

<details>
<summary><b>CRC failures or resyncs in diagnostics</b></summary>

A frame was damaged or the stream lost step. SunBridge drops that frame
without acknowledging it and carries on with the next one. A steady count
points at the network, or at something other than a ShineLan-X talking to the
port.

</details>

## Debug logging

```yaml
logger:
  logs:
    custom_components.sunbridge: debug
```
