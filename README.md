# Pecron Battery Monitor

Real-time monitoring for Pecron portable power stations (E600, E1500LFP, E2000, E3000, etc.) via the Quectel cloud API.

**No phone required.** Connects directly to Pecron's cloud — works on any Raspberry Pi, Linux server, or Mac.

## Features

- 🔋 Real-time battery percentage, voltage, temperature
- ⚡ Input/output power monitoring (solar, AC, DC)
- 🚨 Configurable low-battery alerts (Telegram, ntfy, or webhook)
- 📊 On-demand status reads
- 🔄 Auto-reconnect and token refresh
- 🌍 Multi-region support (North America, Europe, China)

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

Any Pecron power station that works with the Pecron app, including:
- E600LFP
- E1500LFP
- E2000LFP
- E3000LFP
- And others using the Quectel IoT platform

## License

MIT — do whatever you want with it.
