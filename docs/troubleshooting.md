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
- **Green flashing for good:** the datalogger cannot reach the port. Run the
  [port check](setup.md#check-the-port-from-another-device) from another
  device on the datalogger's network while the waiting screen shows.

</details>

<details>
<summary><b>Setup says the port is in use</b></summary>

"Something else is already listening": another program on Home Assistant's
host holds the port. Pick another port, and set the same one on the
datalogger. "SunBridge is already listening on port …": an existing SunBridge
entry has it. Change that entry's port with **Reconfigure** (**⋮ →
Reconfigure** on the SunBridge entry) instead of adding a second one. In
Reconfigure, "Another SunBridge entry already listens on this port" means the
same: pick a port no other entry uses.

</details>

<details>
<summary><b>A Repairs notice says SunBridge is not listening</b></summary>

Reconfigure could not move SunBridge to the new port, and going back to the
old one failed too, so nothing is listening. The entry's settings are back on
the old port, which the notice names. Free that port (stop whatever else
took it), then reload SunBridge from its integration page; or use
**Reconfigure** to pick another free port, and set the same **Server port**
on the datalogger. The notice clears itself as soon as SunBridge listens
again.

</details>

<details>
<summary><b>Connected stays off</b></summary>

The datalogger is not reaching Home Assistant. Check its light (above), then
on its web page that the server IP is Home Assistant's address and the port
matches the integration's. Then test the port from another device on the
datalogger's network: `nc -vz <home-assistant-address> 5279` (macOS, Linux)
or `Test-NetConnection -ComputerName <home-assistant-address> -Port 5279`
(Windows PowerShell). A timeout is a firewall or VLAN rule; "refused" is a
wrong address, or a Docker install without host networking or a published
port ([Networking](setup.md#-networking)). If Home Assistant's address
changed (no DHCP reservation), set the new one on the datalogger, and
reserve it in your router. A firewall between them, or another program on the
same port, also stops it: setup refuses a port it cannot bind, but a program
that takes the port later makes the entry retry. A serial missing from the
allow-list is closed at once, in Simple too (its list is the datalogger
found at setup): diagnostics count it as `rejected_by_allow_list`. To accept
a new datalogger, add its serial under **Configure → Advanced**.

</details>

<details>
<summary><b>A Repairs notice says the datalogger has stopped sending data</b></summary>

SunBridge raises a notice under **Settings → System → Repairs** when a
datalogger it knows has had no connection through three hours of daylight
at Home Assistant's location, added up from when it went quiet (an evening
and the next morning count together), so a quiet night cannot raise it
alone. 24 hours of silence raise it whatever the sun does. The time of the
last connection is saved, so a restart or a reload does not restart the
count, and does not clear the notice either. Check the datalogger's light
and **Connected stays off** above. The notice clears itself as soon as the
datalogger connects again. If the datalogger was replaced or removed, delete
its device instead (below), which clears it too.

</details>

<details>
<summary><b>An old datalogger or inverter device is left over</b></summary>

After replacing a datalogger or an inverter, the old device stays until you
remove it: open it under **Settings → Devices & services → SunBridge**, then
**⋮ → Delete**. SunBridge refuses to delete a datalogger that is connected
or an inverter whose live sensors are current, so the one in use cannot be
removed by mistake. A deleted device that reports again comes back. The replacement gets the plain name if the old one is gone, and its
serial in its name if the old one is still there.

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

The reading was one the inverter could not have made: lower than the stored
total, or higher than 50 kW could have added since the last one. It was
ignored, and diagnostics count it (`lifetime_decreases_ignored`,
`lifetime_jumps_ignored`). If the counter really was reset, or the inverter
replaced, three live readings in a row that agree are accepted (at the
default upload interval the third arrives ten minutes after the first): the total restarts from the new
value (`lifetime_resets_accepted`, logged at info) and the Energy dashboard
starts a new cycle without counting a fall.

</details>

<details>
<summary><b>Energy today shows 0</b></summary>

From Home Assistant's local midnight until the first live reading of the new
day, Energy today shows 0: all night the inverter's last record still holds
yesterday's total. It follows the inverter's own counter again with the first
morning record.

</details>

<details>
<summary><b>The Advanced sensors are disabled after switching to Simple</b></summary>

Simple disables the Advanced sensors rather than deleting them, so their
history and settings stay. Switching back to Advanced turns them on again;
a sensor you disabled yourself stays disabled.

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
