# 🚀 Setup

A walkthrough from nothing to live solar readings, screen by screen. It takes
about fifteen minutes, most of it waiting for the datalogger to restart.

The screenshots come from a test Home Assistant with demo data
(`make screenshots`): its address is the documentation address 192.0.2.10,
and after the port screen it listens on 15279, so the later screens show
that port. Yours show your own address and the port you chose (5279 unless
you changed it).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/setup-flow-dark.svg">
  <img src="assets/setup-flow-light.svg" width="100%" alt="The setup asks Simple or Advanced, then the port and Home Assistant's address, shows the steps for the datalogger's web page, and listens on the port until the datalogger's first record arrives; it then shows both serials and creates the entry, or offers to finish anyway or listen again.">
</picture>

## ✅ Before you start

- [ ] A Growatt inverter with a **ShineLan-X** datalogger (the only one tested).
      Other Growatt dataloggers whose web page has the same **Server IP**
      setting, such as ShineWiFi models, may work but are untested.
- [ ] The datalogger and Home Assistant on the **same local network**.
- [ ] **Daylight.** The datalogger is powered by the inverter, so it is off
      when there is no sun, and you cannot reach its web page at night.
- [ ] The datalogger's **IP address**, from your router's list of connected
      devices (it often shows up with a name starting with its serial).
- [ ] The **user name and password** printed on the datalogger's label
      (often `admin` / `admin`).
- [ ] Home Assistant 2026.9 or newer.

## 1. Install SunBridge

With [HACS](https://hacs.xyz):

1. Open **HACS**, then the **⋮** menu (top right) → **Custom repositories**.
2. **Repository:** `https://github.com/CaputoDavide93/SunBridge`,
   **Type:** **Integration**, then **Add**.
3. Search HACS for **SunBridge**, open it and click **Download**.
4. Restart Home Assistant (**Settings → System → ⏻ → Restart**).

Without HACS: copy `custom_components/sunbridge/` from the
[latest release](https://github.com/CaputoDavide93/SunBridge/releases) into
Home Assistant's `config/custom_components/`, then restart.

## 2. Add the integration

**Settings → Devices & services → Add integration → SunBridge.** The setup
has up to six screens.

### Welcome to SunBridge

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-welcome-dark.png">
  <img src="assets/screenshots/setup-welcome-light.png" width="560" alt="The Welcome to SunBridge screen: what SunBridge does, and the Setup mode choice, Simple (selected) or Advanced.">
</picture>

What SunBridge does and what the next screens will ask, and one choice:

| Mode | What you get |
|---|---|
| **Simple** | The core sensors with the defaults. Pick this if unsure |
| **Advanced** | Extra sensors, a datalogger allow-list and the upload interval (a last screen asks for them) |

You can switch later from **Configure**.

### Choose the Port

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-port-dark.png">
  <img src="assets/screenshots/setup-port-light.png" width="560" alt="The Choose the Port screen: Port 5279, and Home Assistant's address detected as 192.0.2.10.">
</picture>

| Field | Default | What it is |
|---|---|---|
| Port | 5279 | The TCP port SunBridge listens on. 5279 is the port Growatt dataloggers use. Setup refuses a port that something else holds, and explains when another SunBridge entry already listens on it |
| Home Assistant's address | detected | The IPv4 address Home Assistant has on your network, found by Home Assistant's own network settings. Correct it if Home Assistant has more than one network connection. It is only used to fill in the next screen |

### Point Your Datalogger Here

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-point-dark.png">
  <img src="assets/screenshots/setup-point-light.png" width="560" alt="The Point Your Datalogger Here screen: five numbered steps, with Server IP 192.0.2.10 and the server port filled in.">
</picture>

Numbered steps with your address and port already filled in. On the
datalogger's own web page:

1. Open `http://` followed by the datalogger's IP address in a browser.
2. Log in with the user name and password from its label.
3. **Network Setting:** note down the values there, so you can go back to
   Growatt's cloud later.
4. Set **ResolvDomain** to **OFF**, **Server IP** to
   Home Assistant's address and **Server port** to the port. Click **Save**.
5. **Datalogger Setting:** set **Reboot** to **yes** and click **Save**.

Then press **Next** on the setup screen.

### Waiting for Your Datalogger

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-wait-dark.png">
  <img src="assets/screenshots/setup-wait-light.png" width="560" alt="The waiting screen: a spinner and Waiting for your datalogger on 192.0.2.10, with the port, for up to 10 minutes.">
</picture>

SunBridge now listens on the port and answers the datalogger exactly as it
will from then on. The screen moves on by itself as soon as the datalogger
sends a record SunBridge can read (pings alone do not count). After a reboot
that is usually two or three minutes.

<!-- AUTOGEN:setup-wait -->
It listens for up to **10 minutes**.
<!-- /AUTOGEN:setup-wait -->

If you close the dialog, SunBridge stops listening and the port is free
again.

### Datalogger Found

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-found-dark.png">
  <img src="assets/screenshots/setup-found-light.png" width="560" alt="The Datalogger Found screen: datalogger serial DLGEXAMPLE, inverter serial INVEXAMPLE.">
</picture>

The datalogger's serial and the inverter's serial, as the datalogger sent
them. Continue, and the entry is created (Advanced shows one more screen).
Home Assistant then offers to name the two new devices and put them in an
area; **Skip and finish** keeps the names SunBridge gives them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-created-dark.png">
  <img src="assets/screenshots/setup-created-light.png" width="560" alt="Home Assistant's Name and assign screen with the two devices, Solar Datalogger and Solar Inverter, each with an Area field.">
</picture>

### No Datalogger Yet

Shown when nothing arrived in time. Two choices:

- **Finish now:** the entry is created anyway, and the datalogger appears as
  soon as it connects. The right choice at night.
- **Try again:** listen once more. Use it after fixing a setting.

### Advanced Settings (Advanced only)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/setup-advanced-dark.png">
  <img src="assets/screenshots/setup-advanced-light.png" width="560" alt="The Advanced Settings screen: Allowed datalogger serials prefilled with DLGEXAMPLE, and the upload interval of 5 minutes.">
</picture>

| Field | Default | What it does |
|---|---|---|
| Allowed datalogger serials | the datalogger just found | Comma-separated. A connection from any other serial is closed before anything is answered. Empty accepts any datalogger |
| Datalogger upload interval | 5 min (1 to 10) | What the datalogger is set to on its own web page. SunBridge does **not** change the datalogger; it uses this to decide when live values are out of date (three intervals, at least 15 minutes) |

## 3. Check it worked

- `binary_sensor.solar_datalogger_connected` is on.
- `sensor.solar_inverter_power` shows a number after the first live record
  (every upload interval, five minutes by default).
- **Settings → Devices & services → SunBridge → ⋮ → Download diagnostics**:
  `records.by_type` counts what arrived, `crc_failures` should be 0.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/device-inverter-dark.png">
  <img src="assets/screenshots/device-inverter-light.png" width="100%" alt="The Solar Inverter device page: energy today and total, grid and PV readings, power, status and temperature, with last reading and working time under Diagnostic.">
</picture>

The **Solar Inverter** device (Settings → Devices & services → SunBridge),
here in Advanced mode; Simple shows the core sensors only. Its datalogger,
**Solar Datalogger**, holds the Connected sensor:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/device-datalogger-dark.png">
  <img src="assets/screenshots/device-datalogger-light.png" width="100%" alt="The Solar Datalogger device page: serial DLGEXAMPLE, the Connected sensor reading Connected, and Solar Inverter listed under Connected devices.">
</picture>

Clicking **Power** in the device page shows its history:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/power-history-dark.png">
  <img src="assets/screenshots/power-history-light.png" width="560" alt="The Power sensor's dialog: 87.5 W now, and a history graph of an afternoon falling from about 320 W.">
</picture>

## 4. Energy dashboard

**Settings → Dashboards → Energy → Solar panels → Add solar production**, and
pick `sensor.solar_inverter_energy_total`. If another integration already
feeds the same inverter (Growatt's cloud integration, for example), remove it
from the dashboard first, or the energy is counted twice. The same dialog can
also take `sensor.solar_inverter_power` as the solar production power.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/energy-solar-dark.png">
  <img src="assets/screenshots/energy-solar-light.png" width="560" alt="Home Assistant's Configure solar panels dialog with Energy total from Solar Inverter as solar production energy and Power as solar production power.">
</picture>

## Changing settings later

**Settings → Devices & services → SunBridge → Configure** changes the port and
the mode, and the Advanced settings. Saving reloads the listener, and the
datalogger reconnects. A new port must also be set on the datalogger. Going
back from Advanced to Simple removes the Advanced sensors.

## Going back to Growatt's cloud

On the datalogger's web page, **Network Setting**: set **ResolvDomain** to
**ON** and put back the server values you noted in step 3, **Save**, then
**Datalogger Setting → Reboot → yes → Save**. The datalogger uploads to
Growatt again, and the Growatt app shows new data. SunBridge's Connected
sensor turns off; remove the integration if you no longer want it.
