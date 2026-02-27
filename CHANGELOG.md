# Changelog

All notable changes to pecron-monitor are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project uses [Semantic Versioning](https://semver.org/).

## [0.5.4] — 2026-02-27

### Fixed
- **Local TCP returns zeros for aggregate fields** — device firmware doesn't compute `battery_percentage` locally (server-side only); monitor now falls back to `host_packet_electric_percentage` when top-level value is 0
- **`remain_time` unreliable from local TCP** — shows suspiciously low values (e.g., 4 minutes when battery is 96%); monitor now detects and marks these as "N/A (unreliable from local)" in status display
- **Local/BLE data sources misidentified** — when both local and cloud transports are active, cloud MQTT could overwrite the source label; now preserves local source designation when local data arrives first
- Log output formatting improved: remain time shows "N/A" for invalid values instead of attempting to format negative numbers

## [0.5.3] — 2026-07-27

### Fixed
- **Local TCP connection drops every 60s** — Pecron devices close TCP after each response; monitor now reconnects cleanly on each poll cycle instead of logging errors (#6)
- **`--status` shows "CLOUD MQTT" when local TCP data was received** — local transport source is now preserved when async MQTT data arrives afterward (#6)
- **Local TCP shows 0W input power on some models (F3000LFP)** — total input/output power now computed from AC+DC components as fallback when top-level values are missing (#6)
- Reduced log noise: repeated TCP connect/handshake messages on each poll cycle downgraded to DEBUG level

## [0.5.2] — 2026-02-27

### Fixed
- Local TCP transport never initialized when running `--status` or default monitoring with `lan_ip` configured — only worked with `--local` flag (#6)
- `--local` (offline) mode triggered spurious cloud login every poll cycle due to token refresh check, causing OFFLINE warnings and dropping the local connection (#6)
- `force_offline` flag not preserved during token refresh in `run()` loop, allowing `--local` sessions to switch to cloud mode (#6)
- Potential crash when `mqtt_client` is `None` during token refresh cleanup

### Added
- Unit tests for local transport setup and offline mode behavior

## [0.5.1] — 2026-02-25

### Added
- `--no-ble` flag to disable Bluetooth transport entirely
- Per-device `ble: false` config option to disable BLE for specific devices
- Log message when BLE is disabled

### Fixed
- E300LFP AC output being toggled off intermittently when BLE is enabled (#3) — BLE connection appears to cause firmware side effects on some models; `--no-ble` or `ble: false` provides a workaround

## [0.5.0] — 2026-02-25

### Added
- **Offline/local-only mode** (`--local`) — run without any internet after initial setup
- Automatic offline fallback when cloud login fails but local credentials are cached
- TSL (controls metadata) caching in config.yaml during setup
- Manual LAN IP entry in setup wizard (always offered, not just during LAN scan)
- Data source logging — every reading shows `[via LOCAL TCP]`, `[via BLE]`, `[via CLOUD MQTT]`, or `[via REST API]`
- Status display shows `Connection:` method per device
- `--version` flag
- "Offline / Local-Only Mode" section in README

### Fixed
- `lan_ip` not saved to config.yaml during setup (#1)
- Script always requiring cloud login even with local credentials (#1)

## [0.4.0] — 2026-02-24

### Added
- REST API fallback for device data (same method as ha-pecron HACS addon)
- Device online status check at startup
- `--diagnose` flag for troubleshooting connectivity
- `--controls` flag to list all available controls from TSL
- `--control CODE VALUE` for setting any control by code name
- Manual product selection in setup wizard (option 2)
- `getAuthKey` tried before `regenerateAuthKey` (fixes permission errors on some models)

### Fixed
- E300LFP sensor data not displaying — battery, voltage, temperature (#3)
- Duplicate product keys causing "device is not bound" (4007) errors
- Automation rules firing on invalid battery data (-1%)
- Device Code vs Device Key confusion in docs and setup

## [0.3.0] — 2026-02-22

### Added
- **Bluetooth Low Energy (BLE) transport** — monitor with zero network infrastructure
- BLE scanning in setup wizard
- BLE auto-detection by device key suffix

## [0.2.0] — 2026-02-21

### Added
- **Local WiFi TCP transport** (port 6607, AES-CBC encrypted)
- LAN device scanning in setup wizard
- Auth key caching for offline TCP operation
- Automatic fallback: BLE → WiFi TCP → Cloud MQTT

## [0.1.0] — 2026-02-20

### Added
- Initial release
- Cloud MQTT monitoring via Quectel IoT platform
- AC/DC output control
- Automation rules (battery level, input power, schedule)
- Home Assistant MQTT bridge with auto-discovery
- Telegram, ntfy, and webhook alerts
- Multi-device support
- Auto-detect device model from product catalog
- Systemd service file for 24/7 operation
- Comprehensive README with FAQ and use cases
