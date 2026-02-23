# Pecron Battery Monitor

Real-time monitoring and control for Pecron portable power stations — no phone required.

Works with **any** Pecron power station that uses the Pecron app: E300LFP, E500LFP, E600LFP, E800LFP, E1000LFP, E1500LFP, E2000LFP, E2200LFP, E2400LFP, E3600, E3600LFP, E3800LFP, F1000LFP, F3000LFP, F5000LFP, C300LFP Mini, WB12200, and future models.

Runs on a Raspberry Pi, Linux server, Mac, or any computer with Python.

---

## What Does This Do?

The Pecron app on your phone lets you check your battery and control it. This tool does the **same thing**, but from a computer — without needing your phone at all.

That means you can:
- **Monitor your battery 24/7** from a Raspberry Pi sitting in your closet
- **Get alerts** on Telegram or your phone when the battery is low
- **Turn AC/DC outputs on and off** from the command line
- **Set up automations** like "turn off AC when battery drops below 10%"
- **Integrate with Home Assistant** so your Pecron shows up as a smart home device

---

## Before You Start

You need **three things**:

### 1. Python 3.9 or newer

Check if you have it:
```bash
python3 --version
```
If you see `Python 3.9` or higher, you're good. If not:
- **Mac:** `brew install python3`
- **Raspberry Pi / Ubuntu / Debian:** `sudo apt install python3 python3-pip`
- **Windows:** Download from [python.org](https://www.python.org/downloads/)

### 2. Your Pecron account login

The **email and password** you used to create your account in the Pecron app. If you log in with Google/Apple, you'll need to set a password in the app first (go to Profile → Account Settings).

### 3. Your device key (MAC address)

This is a 12-character code that identifies your specific battery. To find it:

1. Open the **Pecron app** on your phone
2. Tap on your device
3. Tap the **⚙️ Settings** icon (top right)
4. Tap **Device Info**
5. Look for **Device Key** — it looks like `AABBCCDDEEFF`

Write this down or copy it. You'll need it during setup.

---

## Step-by-Step Installation

### Step 1: Download the app

Open a terminal (Terminal on Mac, or SSH into your Pi) and run:

```bash
git clone https://github.com/attractify-logan/pecron-monitor.git
cd pecron-monitor
```

> **Don't have git?** You can also download the ZIP from GitHub and unzip it.

### Step 2: Install the dependencies

```bash
pip3 install -r requirements.txt
```

This installs three small Python packages the app needs. If you get a permissions error, try:
```bash
pip3 install --user -r requirements.txt
```

### Step 3: Run the setup wizard

```bash
python3 pecron_monitor.py --setup
```

The wizard will walk you through everything:

```
🔋 Pecron Monitor Setup

Pecron account email: you@email.com
Pecron account password: your-password

Regions:
  na — North America
  eu — Europe
  cn — China
Region [na]: na

Testing login...
✅ Login successful (uid: U12345)

--- Device Setup ---
You need your device key (MAC address). Find it in the Pecron app:
  Device → Settings (⚙️) → Device Info → Device Key
  It looks like: AABBCCDDEEFF

Device Key (or press Enter to finish): AABBCCDDEEFF
  ✅ Found: E1500LFP (AABBCCDDEEFF)
Device Key (or press Enter to finish): [press Enter]

Poll interval in seconds [60]: 60
Low battery alert threshold % [20]: 20

--- Telegram Alerts (optional) ---
Enable Telegram alerts? [y/N]: n

--- Home Assistant (optional) ---
Enable Home Assistant MQTT bridge? [y/N]: n

✅ Config saved to config.yaml
```

That's it — you're set up!

---

## Using the App

### Check your battery right now

```bash
python3 pecron_monitor.py --status
```

This connects to your battery, grabs the current data, and shows you everything:

```
==================================================
Device: AABBCCDDEEFF
==================================================
Battery:       73%
Voltage:       51.8V
Temperature:   24°C
Remaining:     8h 42m
Total Input:   145W
Total Output:  0W
AC Output:     0W @ 120V
DC Output:     0W
AC Input:      0W
DC Input:      145W
AC Switch:     OFF
DC Switch:     ON
UPS Mode:      OFF
```

### Start continuous monitoring

```bash
python3 pecron_monitor.py
```

This runs forever, checking your battery every 60 seconds (or whatever interval you set). It logs the status and sends alerts if the battery drops below your threshold. Press `Ctrl+C` to stop.

### Turn AC output on or off

```bash
python3 pecron_monitor.py --ac on
python3 pecron_monitor.py --ac off
```

### Turn DC output on or off

```bash
python3 pecron_monitor.py --dc on
python3 pecron_monitor.py --dc off
```

### See what controls your model supports

Different Pecron models have different features. Run this to see everything yours can do:

```bash
python3 pecron_monitor.py --controls
```

Example output:
```
E1500LFP (AABBCCDDEEFF):
  id=  1  RO  INT     battery_percentage  — battery power
  id= 27  RO  BOOL    ups_status_hm  — UPS status
  id= 32  RW  ENUM    ac_output_voltage_io  — AC output voltage
  id= 38  RW  BOOL    dc_switch_hm  — DC OUTPUT
  id= 40  RW  BOOL    ac_switch_hm  — AC OUTPUT
  id= 45  RW  ENUM    machine_screen_light_as  — Brightness of the screen
  ...
```

- **RW** = you can read AND write (control it)
- **RO** = read-only (sensor data, can't change it)
- **BOOL** = on/off toggle
- **ENUM** = numbered setting (like brightness levels)

### Set any control by its code name

Use `--control` followed by the code name and value:

```bash
# Turn AC on
python3 pecron_monitor.py --control ac_switch_hm on

# Turn DC off
python3 pecron_monitor.py --control dc_switch_hm off

# Set screen brightness to level 3
python3 pecron_monitor.py --control machine_screen_light_as 3
```

For BOOL controls, use `on`/`off`/`true`/`false`. For ENUM controls, use the number.

### Dump raw data (for debugging)

If something isn't working or you want to see exactly what your battery reports:

```bash
python3 pecron_monitor.py --raw
```

This prints the full JSON payload from your device. Useful if you have a newer model and want to see what data points it sends.

---

## Setting Up Alerts

Alerts notify you when your battery drops below a certain level. You configure them in `config.yaml`.

### Telegram Alerts

This is the easiest way to get push notifications on your phone.

**Step 1: Create a Telegram bot**
1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. Pick a name and username for your bot
4. BotFather gives you a **bot token** — copy it

**Step 2: Get your chat ID**
1. Send any message to your new bot
2. Open this URL in your browser (replace YOUR_TOKEN with your bot token):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
3. Look for `"chat":{"id":` — that number is your **chat ID**

**Step 3: Add to config.yaml**
```yaml
alerts:
  low_battery_percent: 20
  cooldown_minutes: 30
  telegram:
    enabled: true
    bot_token: "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
    chat_id: "987654321"
```

### ntfy Alerts

[ntfy](https://ntfy.sh) is a simple push notification service. You can use the free public server or self-host.

```yaml
alerts:
  ntfy:
    enabled: true
    url: "https://ntfy.sh/my-pecron-alerts"
```

Then subscribe to `my-pecron-alerts` in the ntfy app on your phone.

### Webhook Alerts

Send alerts to any URL (Slack, Discord, custom server, etc.):

```yaml
alerts:
  webhook:
    enabled: true
    url: "https://your-webhook-url.com/endpoint"
```

The webhook receives a JSON payload with `battery_percent`, `voltage`, `remain_minutes`, `device_key`, and `message`.

---

## Automation Rules

Rules let the app automatically control your battery based on conditions. Add them to the `rules` section of `config.yaml`.

### Example: Turn off AC when battery is low

```yaml
rules:
  - name: "Low battery — turn off AC"
    condition:
      battery_below: 10
    action:
      set_ac: false
    cooldown_minutes: 30
```

### Example: Turn on AC when fully charged

```yaml
rules:
  - name: "Full charge — enable AC"
    condition:
      battery_above: 95
    action:
      set_ac: true
    cooldown_minutes: 30
```

### Example: Turn off DC when there's no solar input

```yaml
rules:
  - name: "No solar — turn off DC"
    condition:
      input_power_below: 5
    action:
      set_dc: false
    cooldown_minutes: 60
```

### Example: Shut everything off at midnight

```yaml
rules:
  - name: "Midnight shutoff"
    condition:
      schedule: "00:00"
    action:
      set_ac: false
      set_dc: false
    cooldown_minutes: 1440
```

### Available conditions

| Condition | What it checks |
|-----------|---------------|
| `battery_below: 10` | Battery percentage is at or below 10% |
| `battery_above: 95` | Battery percentage is at or above 95% |
| `input_power_below: 5` | Total input (solar/AC) is at or below 5W |
| `input_power_above: 100` | Total input is at or above 100W |
| `schedule: "08:00"` | Current time matches (24-hour format, checked every poll) |

### Available actions

| Action | What it does |
|--------|-------------|
| `set_ac: true/false` | Turn AC output on or off |
| `set_dc: true/false` | Turn DC output on or off |
| `set_ups: true/false` | Enable or disable UPS mode |

### Cooldown

`cooldown_minutes` prevents the rule from firing repeatedly. If a rule fires, it won't fire again until the cooldown expires. This prevents the app from toggling your AC on and off every 60 seconds when the battery is hovering around a threshold.

---

## Home Assistant Integration

If you use [Home Assistant](https://www.home-assistant.io/), the app can publish your Pecron as a fully auto-discovered device with sensors and switches.

### Requirements

- Home Assistant with the MQTT integration enabled
- An MQTT broker (Mosquitto is the most common — HA has an add-on for it)

### Setup

Add this to your `config.yaml`:

```yaml
homeassistant:
  enabled: true
  mqtt_host: "192.168.1.100"   # IP of your HA/MQTT broker
  mqtt_port: 1883
  mqtt_user: "mqtt_username"    # Leave empty if no auth
  mqtt_password: "mqtt_password"
```

Then start the monitor with the HA flag:

```bash
python3 pecron_monitor.py --homeassistant
```

Or just run `python3 pecron_monitor.py` — if `homeassistant.enabled` is `true` in your config, it starts automatically.

### What shows up in Home Assistant

Your Pecron appears as a device with:

**Sensors:**
- Battery percentage (%)
- Voltage (V)
- Temperature (°C)
- Input power (W)
- Output power (W)
- Remaining time (minutes)

**Switches:**
- AC output (on/off)
- DC output (on/off)
- UPS mode (on/off)

You can use these in HA automations, dashboards, and scripts just like any other smart home device.

---

## Running 24/7 on a Raspberry Pi

If you want the monitor running all the time (recommended), set it up as a system service.

### Step 1: Edit the service file

Open `pecron-monitor.service` and update the paths to match where you installed it:

```ini
[Unit]
Description=Pecron Battery Monitor
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/pecron-monitor
ExecStart=/usr/bin/python3 /home/pi/pecron-monitor/pecron_monitor.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Change `User=pi` and the paths if your username or install location is different.

### Step 2: Install and start the service

```bash
sudo cp pecron-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pecron-monitor
sudo systemctl start pecron-monitor
```

### Step 3: Check it's running

```bash
sudo systemctl status pecron-monitor
```

You should see `active (running)`. To see the logs:

```bash
journalctl -u pecron-monitor -f
```

The service will:
- Start automatically on boot
- Restart if it crashes
- Reconnect if the internet drops
- Refresh authentication tokens automatically

---

## Multiple Devices

You can monitor more than one Pecron battery. Just add them during setup, or add them to `config.yaml`:

```yaml
devices:
  - product_key: "p11u2b"
    device_key: "AABBCCDDEEFF"
  - product_key: "p11usc"
    device_key: "112233445566"
```

Don't know your product key? No problem — the setup wizard finds it automatically by checking your device key against all known models.

---

## Troubleshooting

### "Login failed"
- Double-check your email and password
- Make sure you can log into the Pecron app with the same credentials
- If you use Google/Apple sign-in, you need to set a password in the app first

### "No data received — device may be offline"
- Your battery needs to be connected to WiFi
- Make sure it shows as online in the Pecron app
- The battery's WiFi module may go to sleep — open the Pecron app briefly to wake it up, then try again

### "Device not found" during setup
- Make sure you're entering the **Device Key** (MAC address), not the serial number
- The device key is 12 characters, all uppercase letters and numbers (e.g., `AABBCCDDEEFF`)
- Check that you selected the right region (NA, EU, or CN)

### pip install errors
- Try `pip3 install --user -r requirements.txt`
- On Raspberry Pi, you may need: `pip3 install --break-system-packages -r requirements.txt`
- Make sure you have Python 3.9+: `python3 --version`

---

## How It Works (Technical)

The Pecron app communicates with your battery through Pecron's cloud infrastructure, which is built on the **Quectel IoT platform**. This app uses the same cloud API:

1. **Authentication** — Logs in with your Pecron email/password using an encrypted challenge-response flow (AES-CBC + SHA-256 signature)
2. **Device discovery** — Queries the product catalog to match your device key to a product model
3. **TSL fetch** — Downloads the Thing Specification Language for your model, which describes all available sensors and controls with their data types
4. **MQTT connection** — Connects to the Quectel MQTT broker over secure WebSocket (WSS on port 8443)
5. **TTLV protocol** — Sends binary TTLV (Tag-Type-Length-Value) commands to read status or write controls
6. **Data parsing** — Receives JSON responses with all sensor data (battery, voltage, power, etc.)
7. **Token refresh** — Automatically re-authenticates before the 2-hour JWT token expires

Your credentials are stored only in `config.yaml` on your local machine. The app communicates only with Pecron's own cloud servers.

---

## Security

- **Credentials** are stored locally in `config.yaml` — never uploaded anywhere
- **No telemetry** — the app doesn't phone home or track anything
- **Cloud-only communication** — data goes between your machine and Pecron's Quectel cloud servers (the same servers the official app uses)
- **Token-based auth** — your password is encrypted before transmission, and access tokens expire every 2 hours

**Tip:** Set permissions on your config file so only you can read it:
```bash
chmod 600 config.yaml
```

---

## Supported Models

The app works with any Pecron power station that connects to the Pecron app. It auto-detects your model during setup and fetches the correct data schema, so new models are supported automatically.

Currently known models:

| Model | Product Key |
|-------|------------|
| E300LFP | p11u2Q |
| C300LFP Mini | p11uXh |
| E500LFP | p11uFC |
| E600LFP | p11umP |
| E800LFP | p11uXR |
| E1000LFP | p11vxg |
| E1500LFP | p11u2b |
| E2000LFP | p11usc |
| E2200LFP | p11t8R |
| E2400LFP | p11tf9 |
| E2400LFP ADJ | p11vB4 |
| E3600 | p11tUC |
| E3600LFP | p11wV4 |
| E3800LFP | p11uJn |
| F1000LFP | p11vWw |
| F3000LFP | p11uAG |
| F5000LFP | p11vwW |
| WB12200 | p11vGo |

Don't see your model? It probably still works — run `--setup` and try it. The app checks all known product keys automatically.

---

## License

MIT — do whatever you want with it.
