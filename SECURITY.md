# 🔒 Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/CaputoDavide93/SunBridge/security/advisories/new)
rather than opening a public issue. You should get a response within a week.

## Data handling

- The integration opens one TCP port (5279 by default) on every interface of
  the Home Assistant host. The datalogger's protocol has **no authentication**:
  anything on the network that can reach the port can send records. Keep the
  port on the LAN (never forward it from the internet), and in Advanced mode
  set the datalogger serial allow-list, which closes any connection from
  another serial before it is answered or decoded. The serial is sent in the
  clear, so the allow-list is a filter against mistakes, not authentication.
- The records' "scrambling" is a fixed XOR, not encryption. Treat the link as
  plain text on the LAN.
- Nothing is sent anywhere except the replies to the datalogger: the
  acknowledgements, ping echoes and a clock command after each announce.
  No cloud, no MQTT.
- Diagnostics leave out every datalogger and inverter serial: devices are
  counted, the allow-list is reported by its size, and the last frame per
  record type has both serial fields zeroed and every serial seen blanked
  wherever it repeats. For a record type SunBridge does not know, only the
  header is kept, never the payload. Connections are listed
  without their address.

## Supported versions

Only the latest release is supported.
