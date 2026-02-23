# Pecron Battery Monitor

Real-time monitoring for Pecron portable power stations (E600, E1500LFP, E2000, E3000, etc.) via the Quectel cloud API.

**No phone required.** Connects directly to Pecron's cloud — works on any Raspberry Pi, Linux server, or Mac.

## Features

- 🔋 Real-time battery percentage, voltage, temperature
- ⚡ Input/output power monitoring (solar, AC, DC)
- 🔌 AC/DC output control, UPS toggle, screen brightness
- 🤖 Automation rules (battery-range triggers, time schedules)
- 🏠 Home Assistant MQTT bridge (auto-discovery)
- 🚨 Configurable alerts (Telegram, ntfy, or webhook)
- 📊 On-demand status reads
- 🔄 Auto-reconnect and token refresh
- 🌍 Multi-region support (North America, Europe, China)
- 🔧 Multi-model support — auto-fetches your model's TSL (data schema)

## Requirements

- Python 3.9+
- Pecron account (same email/password you use in the Pecron app)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run setup wizard (creates config.yaml)
python pecron_monitor.py --setup

# Start monitoring
python pecron_monitor.py

# One-shot status
python pecron_monitor.py --status

# Control AC/DC
python pecron_monitor.py --ac on
python pecron_monitor.py --dc off

# List all controls your model supports
python pecron_monitor.py --controls

# Set any control by its TSL code
python pecron_monitor.py --control ac_switch_hm on
python pecron_monitor.py --control machine_screen_light_as 3

# Dump raw JSON (useful for debugging new models)
python pecron_monitor.py --raw

# Start with Home Assistant MQTT bridge
python pecron_monitor.py --homeassistant
```

## Configuration

The setup wizard creates `config.yaml`. You can also create it manually:

```yaml
# Pecron account credentials
email: "your-pecron-email@example.com"
password: "your-pecron-password"

# Region: na (North America), eu (Europe), cn (China)
region: "na"

# Your devices (device_key = MAC address, found in Pecron app → Device Info)
devices:
  - product_key: "p11u2b"    # Found automatically during setup
    device_key: "XXXXXXXXXXXX"  # Your device's MAC (e.g., AABBCCDDEEFF)

# Polling interval in seconds (how often to request fresh data)
poll_interval: 60

# Alert settings
alerts:
  # Battery percentage threshold to trigger alert
  low_battery_percent: 20
  
  # Alert cooldown in minutes (avoid spam)
  cooldown_minutes: 30

  # Telegram alerts (optional)
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""
  
  # ntfy alerts (optional) — self-hosted or ntfy.sh
  ntfy:
    enabled: false
    url: "https://ntfy.sh/your-topic"
  
  # Generic webhook (optional)
  webhook:
    enabled: false
    url: ""
```

## Automation Rules

Add rules to `config.yaml` to automate actions based on battery level or time:

```yaml
rules:
  - name: "Low battery — turn off AC"
    condition:
      battery_below: 10
    action:
      set_ac: false
    cooldown_minutes: 30

  - name: "Full charge — enable AC"
    condition:
      battery_above: 95
    action:
      set_ac: true
    cooldown_minutes: 30

  - name: "No solar — turn off DC"
    condition:
      input_power_below: 5
    action:
      set_dc: false
    cooldown_minutes: 60

  - name: "Midnight shutoff"
    condition:
      schedule: "00:00"
    action:
      set_ac: false
      set_dc: false
    cooldown_minutes: 1440
```

## Home Assistant Integration

Enable the MQTT bridge to auto-discover your Pecron as a Home Assistant device:

```yaml
homeassistant:
  enabled: true
  mqtt_host: "192.168.1.100"  # Your HA MQTT broker
  mqtt_port: 1883
  mqtt_user: "ha_user"
  mqtt_password: "ha_pass"
```

This creates sensors (battery, voltage, temp, power in/out, remaining time) and switches (AC, DC, UPS) in HA automatically.

## Running as a Service (Raspberry Pi / Linux)

```bash
# Copy the service file
sudo cp pecron-monitor.service /etc/systemd/system/
# Edit it to set your install path
sudo systemctl daemon-reload
sudo systemctl enable pecron-monitor
sudo systemctl start pecron-monitor
```

## How It Works

1. Logs into Pecron's Quectel cloud API with your account
2. Discovers your devices automatically
3. Connects via MQTT-over-WebSocket for real-time data
4. Sends a TTLV read command to request battery status
5. Parses the JSON response and checks alert thresholds
6. Repeats on your configured interval

## Security

- Credentials stored locally in `config.yaml` only
- No data sent anywhere except Pecron's own cloud and your configured alert endpoints
- Token refreshed automatically (2-hour expiry)

## Supported Models

Any Pecron power station that works with the Pecron app. The setup wizard auto-discovers your model. Controls and sensors are fetched dynamically from the device's TSL (Thing Specification Language), so new models work automatically.

Known models: E300LFP, C300LFP Mini, E500LFP, E600LFP, E800LFP, E1000LFP, E1500LFP, E2000LFP, E2200LFP, E2400LFP, E3600, E3600LFP, E3800LFP, F1000LFP, F3000LFP, F5000LFP, WB12200.

Use `--controls` to see what your specific model supports, and `--raw` to inspect the full data payload.

## License

MIT — do whatever you want with it.
