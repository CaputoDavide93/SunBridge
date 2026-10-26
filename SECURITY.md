# 🔒 Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/CaputoDavide93/SunBridge/security/advisories/new)
rather than opening a public issue. You should get a response within a week.

Include the SunBridge version, the Home Assistant version, and the steps or
bytes that show the problem. Replace datalogger and inverter serials with
`DLGEXAMPLE` and `INVEXAMPLE`, and LAN addresses with documentation ones
(192.0.2.x), before you send anything. Bugs that are not about security go
to the [issue forms](https://github.com/CaputoDavide93/SunBridge/issues/new/choose).

## Data handling

- The integration opens one TCP port (5279 by default) on every interface of
  the Home Assistant host. The datalogger's protocol has **no authentication**:
  anything on the network that can reach the port can send records. Keep the
  port on the LAN (never forward it from the internet), and keep the
  datalogger serial allow-list set: it closes any connection from another
  serial before it is answered or decoded. Setup fills it with the datalogger
  it found, in Simple and Advanced alike, and Advanced lets you edit it. A
  Simple entry with an empty list (a setup finished before any datalogger
  connected, or an entry saved by 0.1.0, which had no list) answers any
  datalogger, and **Settings → System → Repairs** says so. Each time such an
  entry starts (a restart, a reload, the upgrade itself), it adopts the
  datalogger it knows if it knows exactly one; if it knows none or several,
  it keeps answering any and the notice stays until you set the list in
  **Configure → Advanced**. An empty list in Advanced is a choice made on
  its form, and raises no notice. The serial is
  sent in the clear, so the allow-list is a filter against mistakes, not
  authentication.
- A connection belongs to the first datalogger serial it identifies. A
  record with a different serial on the same connection closes it
  unanswered, and a connection that does not identify itself within 120
  seconds is closed; diagnostics count both.
- The records' "scrambling" is a fixed XOR, not encryption. Treat the link as
  plain text on the LAN.
- Nothing is sent anywhere except the replies to the datalogger: the
  acknowledgements, ping echoes and a clock command after each announce.
  No cloud, no MQTT.
- Diagnostics leave out every datalogger and inverter serial: devices are
  counted, the allow-list is reported by its size, and the last frame per
  record type has both serial fields zeroed and every serial seen blanked
  wherever it repeats, the current frame's serials always, however many
  serials came before. For a record type SunBridge does not know, only the
  header is kept, never the payload. Connections are listed
  without their address.

## Supported versions

Only the latest release is supported.
