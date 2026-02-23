#!/usr/bin/env python3
"""
Pecron Battery Monitor — real-time monitoring via Quectel cloud API.
Works with any Pecron power station (E600, E1500LFP, E2000, E3000, etc.)

Usage:
    python pecron_monitor.py --setup     # Interactive setup wizard
    python pecron_monitor.py             # Start monitoring
    python pecron_monitor.py --status    # One-shot status check
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import secrets
import signal
import string
import struct
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Missing dependency: pip install paho-mqtt")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Region configurations — public Quectel IoT platform endpoints
# ---------------------------------------------------------------------------
REGIONS = {
    "na": {
        "name": "North America",
        "base_url": "https://iot-api.landecia.com",
        "mqtt_host": "iot-south.landecia.com",
        "mqtt_port": 8443,
        "mqtt_path": "/ws/v2",
        "user_domain": "U.DM.10351.1",
        "user_domain_secret": "HARsQXfeex8vxyaPRAM8fyjqqVuH2uxAGQ3inJ8XxTiB",
    },
    "eu": {
        "name": "Europe",
        "base_url": "https://iot-api.acceleronix.io",
        "mqtt_host": "iot-south.quecteleu.com",
        "mqtt_port": 8443,
        "mqtt_path": "/ws/v2",
        "user_domain": "C.DM.10351.1",
        "user_domain_secret": "FA5ZHXSka8y9GHvU91Hz1vWvaDSHE2mGW5B7bpn3fXTW",
    },
    "cn": {
        "name": "China",
        "base_url": "https://iot-api.quectelcn.com",
        "mqtt_host": "iot-south.quectelcn.com",
        "mqtt_port": 8443,
        "mqtt_path": "/ws/v2",
        "user_domain": "C.DM.5903.1",
        "user_domain_secret": "EufftRJSuWuVY7c6txzGifV9bJcfXHAFa7hXY5doXSn7",
    },
}

CONFIG_PATH = Path(__file__).parent / "config.yaml"

log = logging.getLogger("pecron")


# ===========================================================================
# Authentication — Quectel cloud login
# ===========================================================================

def _make_auth_params(email: str, password: str, region: dict) -> dict:
    """Build the authentication parameters for Quectel cloud login.

    Algorithm (reverse-engineered from Pecron APK):
      1. random  = 16-char alphanumeric
      2. aesKey  = MD5(random).upper()[8:24]
      3. iv      = aesKey[8:16] + aesKey[0:8]
      4. encPwd  = Base64(AES-CBC-PKCS5(password, aesKey, iv))
      5. sig     = SHA256(email + encPwd + random + userDomainSecret)
    """
    rand = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    md5 = hashlib.md5(rand.encode()).hexdigest().upper()
    aes_key = md5[8:24]
    iv = aes_key[8:16] + aes_key[0:8]

    cipher = AES.new(aes_key.encode(), AES.MODE_CBC, iv.encode())
    enc_pwd = base64.b64encode(cipher.encrypt(pad(password.encode(), 16))).decode()

    sig_input = email + enc_pwd + rand + region["user_domain_secret"]
    signature = hashlib.sha256(sig_input.encode()).hexdigest()

    return {
        "email": email,
        "pwd": enc_pwd,
        "random": rand,
        "userDomain": region["user_domain"],
        "signature": signature,
    }


def login(email: str, password: str, region: dict) -> dict:
    """Login to Quectel cloud and return token data."""
    params = _make_auth_params(email, password, region)
    url = region["base_url"] + "/v2/enduser/enduserapi/emailPwdLogin"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())

    if body.get("code") != 200:
        raise RuntimeError(f"Login failed: {body.get('msg', body)}")

    token_data = body["data"]["accessToken"]
    token = token_data["token"]

    # Extract uid from JWT payload
    jwt_parts = token.split(".")
    payload_b64 = jwt_parts[1] + "=" * (4 - len(jwt_parts[1]) % 4)
    jwt_payload = json.loads(base64.b64decode(payload_b64))

    return {
        "token": token,
        "uid": jwt_payload["uid"],
        "expires_at": jwt_payload.get("exp", 0),
        "refresh_token": body["data"].get("refreshToken", {}).get("token", ""),
    }


def get_product_catalog(token: str, region: dict) -> dict:
    """Fetch Pecron product catalog (productKey → name mapping)."""
    url = region["base_url"] + "/v2/enduser/enduserapi/getProductList?pageNum=1&pageSize=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", token)

    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())

    if body.get("code") != 200:
        return {}

    return {p["productKey"]: p["name"] for p in body.get("data", {}).get("list", [])}


def verify_device(token: str, region: dict, product_key: str, device_key: str) -> dict:
    """Verify a device exists and get binding info."""
    url = (region["base_url"] +
           f"/v2/binding/enduserapi/getDeviceBindingInfo?pk={product_key}&dk={device_key}")
    req = urllib.request.Request(url)
    req.add_header("Authorization", token)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        if body.get("code") == 200:
            return body.get("data", {})
    except Exception:
        pass
    return {}


def resolve_devices(config: dict, token: str, region: dict) -> list:
    """Resolve device list from config. Supports explicit devices or auto-detect."""
    catalog = get_product_catalog(token, region)
    devices = []

    configured = config.get("devices", [])
    if configured:
        for d in configured:
            pk = d.get("product_key", "")
            dk = d.get("device_key", "")
            name = catalog.get(pk, d.get("name", "Unknown"))

            info = verify_device(token, region, pk, dk)
            if info:
                devices.append({
                    "product_key": pk,
                    "device_key": dk,
                    "device_name": info.get("productName", name),
                    "product_name": info.get("productName", name),
                    "online": True,  # We'll know for sure via MQTT
                })
                log.info("  ✅ %s (%s)", name, dk)
            else:
                log.warning("  ❌ %s (%s) — not found or not bound to this account", name, dk)
    else:
        raise RuntimeError(
            "No devices configured. Run --setup or add devices to config.yaml.\n"
            "You can find your device key in the Pecron app:\n"
            "  Device → Settings → Device Info → Device Key (MAC address)"
        )

    return devices


# ===========================================================================
# TTLV protocol
# ===========================================================================

def build_ttlv_read(packet_id: int = 1) -> bytes:
    """Build a TTLV read command (cmd=0x0011) to request device status."""
    cmd = 0x0011
    inner = struct.pack(">HH", packet_id, cmd)
    crc = sum(inner) & 0xFF
    length = len(inner) + 1
    return b"\xaa\xaa" + struct.pack(">H", length) + bytes([crc]) + inner


# ===========================================================================
# MQTT monitor
# ===========================================================================

class PecronMonitor:
    def __init__(self, config: dict):
        self.config = config
        self.region = REGIONS[config["region"]]
        self.token_data = None
        self.mqtt_client = None
        self.devices = []
        self.latest_data = {}  # device_key -> latest kv data
        self.last_alert = {}   # device_key -> timestamp
        self._packet_id = 0
        self._running = False

    def _next_packet_id(self) -> int:
        self._packet_id = (self._packet_id + 1) % 65535
        return self._packet_id

    def authenticate(self):
        """Login and fetch devices."""
        log.info("Logging in to Pecron cloud (%s)...", self.region["name"])
        self.token_data = login(
            self.config["email"],
            self.config["password"],
            self.region,
        )
        log.info("Logged in as %s", self.token_data["uid"])

        log.info("Resolving devices...")
        self.devices = resolve_devices(self.config, self.token_data["token"], self.region)
        if not self.devices:
            raise RuntimeError("No valid devices found. Check your config.")

    def _channel_id(self, device: dict) -> str:
        return f"qd{device['product_key']}{device['device_key']}"

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != mqtt.CONNACK_ACCEPTED:
            log.error("MQTT connection failed: %s", mqtt.connack_string(rc))
            return

        log.info("MQTT connected")
        for device in self.devices:
            cid = self._channel_id(device)
            for suffix in ["bus_", "ack_", "onl_"]:
                topic = f"q/2/d/{cid}/{suffix}"
                client.subscribe(topic, qos=1)
            log.info("Subscribed to %s (%s)", device["device_name"], device["device_key"])

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        topic_suffix = msg.topic.split("/")[-1]
        device_key = payload.get("deviceKey", "")

        if topic_suffix == "bus_" and "data" in payload:
            kv = payload["data"].get("kv", {})
            if kv:
                self.latest_data[device_key] = kv
                self._process_data(device_key, kv)

        elif topic_suffix == "onl_" and "data" in payload:
            online = payload["data"].get("value", 0) == 1
            status = "online" if online else "offline"
            log.info("Device %s is now %s", device_key, status)

    def _process_data(self, device_key: str, kv: dict):
        """Process incoming data and check alert thresholds."""
        battery_info = kv.get("host_packet_data_jdb", {})
        battery_pct = int(battery_info.get("host_packet_electric_percentage", -1))
        voltage = float(battery_info.get("host_packet_voltage", 0))
        temp = int(battery_info.get("host_packet_temp", 0))

        total_in = int(kv.get("total_input_power", 0))
        total_out = int(kv.get("total_output_power", 0))
        remain = int(kv.get("remain_time", 0))

        log.info("🔋 %s%% | %.1fV | %d°C | ⚡ In:%dW Out:%dW | ⏱ %dh%dm",
                 battery_pct, voltage, temp, total_in, total_out,
                 remain // 60, remain % 60)

        # Check alert threshold
        alerts = self.config.get("alerts", {})
        threshold = alerts.get("low_battery_percent", 20)
        cooldown = alerts.get("cooldown_minutes", 30) * 60

        if battery_pct >= 0 and battery_pct <= threshold:
            now = time.time()
            last = self.last_alert.get(device_key, 0)
            if now - last > cooldown:
                self.last_alert[device_key] = now
                self._send_alert(device_key, battery_pct, voltage, remain)

    def _send_alert(self, device_key: str, battery_pct: int, voltage: float, remain_min: int):
        """Send low-battery alert via configured channels."""
        msg = (
            f"⚠️ Pecron Low Battery Alert\n"
            f"Battery: {battery_pct}%\n"
            f"Voltage: {voltage:.1f}V\n"
            f"Remaining: {remain_min // 60}h {remain_min % 60}m\n"
            f"Device: {device_key}"
        )
        log.warning(msg)

        alerts = self.config.get("alerts", {})

        # Telegram
        tg = alerts.get("telegram", {})
        if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
            try:
                url = f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage"
                data = urllib.parse.urlencode({
                    "chat_id": tg["chat_id"],
                    "text": msg,
                }).encode()
                urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
                log.info("Telegram alert sent")
            except Exception as e:
                log.error("Telegram alert failed: %s", e)

        # ntfy
        ntfy = alerts.get("ntfy", {})
        if ntfy.get("enabled") and ntfy.get("url"):
            try:
                req = urllib.request.Request(
                    ntfy["url"],
                    data=msg.encode(),
                    headers={"Title": f"Pecron Battery {battery_pct}%"},
                )
                urllib.request.urlopen(req, timeout=10)
                log.info("ntfy alert sent")
            except Exception as e:
                log.error("ntfy alert failed: %s", e)

        # Webhook
        wh = alerts.get("webhook", {})
        if wh.get("enabled") and wh.get("url"):
            try:
                payload = json.dumps({
                    "battery_percent": battery_pct,
                    "voltage": voltage,
                    "remain_minutes": remain_min,
                    "device_key": device_key,
                    "message": msg,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }).encode()
                req = urllib.request.Request(
                    wh["url"],
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
                log.info("Webhook alert sent")
            except Exception as e:
                log.error("Webhook alert failed: %s", e)

    def _request_status(self):
        """Send a TTLV read command to all devices."""
        for device in self.devices:
            cid = self._channel_id(device)
            topic = f"q/1/d/{cid}/bus"
            pkt = build_ttlv_read(self._next_packet_id())
            self.mqtt_client.publish(topic, pkt, qos=1)
            log.debug("Requested status from %s", device["device_key"])

    def _token_needs_refresh(self) -> bool:
        if not self.token_data:
            return True
        # Refresh 5 minutes before expiry
        return time.time() > (self.token_data["expires_at"] - 300)

    def connect_mqtt(self):
        """Establish MQTT-over-WebSocket connection."""
        client_id = f"qu_{self.token_data['uid']}_{int(time.time() * 1000)}"

        self.mqtt_client = mqtt.Client(
            client_id=client_id,
            transport="websockets",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self.mqtt_client.ws_set_options(path=self.region["mqtt_path"])
        self.mqtt_client.tls_set()
        self.mqtt_client.username_pw_set(username="", password=self.token_data["token"])
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)

        log.info("Connecting to MQTT broker %s:%d...",
                 self.region["mqtt_host"], self.region["mqtt_port"])
        self.mqtt_client.connect(
            self.region["mqtt_host"],
            self.region["mqtt_port"],
        )
        self.mqtt_client.loop_start()

    def run(self):
        """Main monitoring loop."""
        self._running = True
        self.authenticate()
        self.connect_mqtt()

        poll_interval = self.config.get("poll_interval", 60)
        log.info("Monitoring started (polling every %ds)", poll_interval)

        # Initial status request after connection settles
        time.sleep(3)
        self._request_status()

        try:
            while self._running:
                time.sleep(poll_interval)

                # Refresh token if needed
                if self._token_needs_refresh():
                    log.info("Refreshing authentication token...")
                    try:
                        self.mqtt_client.loop_stop()
                        self.mqtt_client.disconnect()
                    except Exception:
                        pass
                    self.authenticate()
                    self.connect_mqtt()
                    time.sleep(3)

                self._request_status()

        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._running = False
            if self.mqtt_client:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()

    def status_once(self):
        """One-shot status check, prints and exits."""
        self.authenticate()
        self.connect_mqtt()
        time.sleep(3)
        self._request_status()
        time.sleep(5)

        for dk, kv in self.latest_data.items():
            bp = kv.get("host_packet_data_jdb", {})
            ac_out = kv.get("ac_data_output_hm", {})
            dc_out = kv.get("dc_data_output_hm", {})
            dc_in = kv.get("dc_data_input_hm", {})
            ac_in = kv.get("ac_data_input_hm", {})
            remain = int(kv.get("remain_time", 0))

            print(f"\n{'=' * 50}")
            print(f"Device: {dk}")
            print(f"{'=' * 50}")
            print(f"Battery:       {bp.get('host_packet_electric_percentage', '?')}%")
            print(f"Voltage:       {float(bp.get('host_packet_voltage', 0)):.1f}V")
            print(f"Temperature:   {bp.get('host_packet_temp', '?')}°C")
            print(f"Status:        {'Charging' if bp.get('host_packet_status') == '1' else 'Normal'}")
            print(f"Remaining:     {remain // 60}h {remain % 60}m")
            print(f"Total Input:   {kv.get('total_input_power', 0)}W")
            print(f"Total Output:  {kv.get('total_output_power', 0)}W")
            print(f"AC Output:     {ac_out.get('ac_output_power', 0)}W @ {ac_out.get('ac_output_voltage', '?')}V")
            print(f"DC Output:     {dc_out.get('dc_output_power', 0)}W")
            print(f"AC Input:      {ac_in.get('ac_power', 0)}W")
            print(f"DC Input:      {dc_in.get('dc_input_power', 0)}W")

            # Charging packs (battery expansion / solar)
            packs = kv.get("charging_pack_data_jdb", [])
            for i, pack in enumerate(packs):
                if int(pack.get("charging_pack_status", 4)) != 4:  # 4 = not connected
                    print(f"Pack {i}:        {pack.get('charging_pack_battery', '?')}% "
                          f"{float(pack.get('charging_pack_voltage', 0)):.1f}V "
                          f"{float(pack.get('charging_pack_current', 0)):.1f}A")

        if not self.latest_data:
            print("No data received — device may be offline.")

        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()

    def stop(self):
        self._running = False


# ===========================================================================
# Setup wizard
# ===========================================================================

def setup_wizard():
    """Interactive setup — creates config.yaml."""
    print("\n🔋 Pecron Monitor Setup\n")

    email = input("Pecron account email: ").strip()
    password = input("Pecron account password: ").strip()

    print("\nRegions:")
    print("  na — North America")
    print("  eu — Europe")
    print("  cn — China")
    region = input("Region [na]: ").strip().lower() or "na"
    if region not in REGIONS:
        print(f"Invalid region '{region}', using 'na'")
        region = "na"

    # Test login
    print("\nTesting login...")
    try:
        token_data = login(email, password, REGIONS[region])
        print(f"✅ Login successful (uid: {token_data['uid']})")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        print("Check your email/password and try again.")
        return

    # Get product catalog for model lookup
    catalog = get_product_catalog(token_data["token"], REGIONS[region])

    # Device setup
    print("\n--- Device Setup ---")
    print("You need your device key (MAC address). Find it in the Pecron app:")
    print("  Device → Settings (⚙️) → Device Info → Device Key")
    print("  It looks like: AABBCCDDEEFF")
    print()

    devices = []
    while True:
        dk = input("Device Key (or press Enter to finish): ").strip().upper()
        if not dk:
            break

        # Try to find the product key by checking binding
        found_pk = None
        print("  Looking up device...", end="", flush=True)
        for pk, name in catalog.items():
            info = verify_device(token_data["token"], REGIONS[region], pk, dk)
            if info:
                found_pk = pk
                print(f"\r  ✅ Found: {info.get('productName', name)} ({dk})")
                devices.append({"product_key": pk, "device_key": dk, "name": name})
                break
        if not found_pk:
            print(f"\r  ❌ Device {dk} not found. Check the key and try again.")

    if not devices:
        print("⚠️  No devices added. You can add them manually to config.yaml later.")

    poll = input("\nPoll interval in seconds [60]: ").strip() or "60"
    threshold = input("Low battery alert threshold % [20]: ").strip() or "20"

    # Telegram alerts
    print("\n--- Telegram Alerts (optional) ---")
    tg_enabled = input("Enable Telegram alerts? [y/N]: ").strip().lower() == "y"
    tg_token = ""
    tg_chat = ""
    if tg_enabled:
        tg_token = input("Bot token: ").strip()
        tg_chat = input("Chat ID: ").strip()

    # ntfy alerts
    print("\n--- ntfy Alerts (optional) ---")
    ntfy_enabled = input("Enable ntfy alerts? [y/N]: ").strip().lower() == "y"
    ntfy_url = ""
    if ntfy_enabled:
        ntfy_url = input("ntfy URL [https://ntfy.sh/pecron]: ").strip() or "https://ntfy.sh/pecron"

    config = {
        "email": email,
        "password": password,
        "region": region,
        "devices": devices,
        "poll_interval": int(poll),
        "alerts": {
            "low_battery_percent": int(threshold),
            "cooldown_minutes": 30,
            "telegram": {
                "enabled": tg_enabled,
                "bot_token": tg_token,
                "chat_id": tg_chat,
            },
            "ntfy": {
                "enabled": ntfy_enabled,
                "url": ntfy_url,
            },
            "webhook": {
                "enabled": False,
                "url": "",
            },
        },
    }

    config_path = CONFIG_PATH
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"\n✅ Config saved to {config_path}")
    print("Run 'python pecron_monitor.py' to start monitoring!")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Pecron Battery Monitor")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--status", action="store_true", help="One-shot status check")
    parser.add_argument("--config", type=str, default=str(CONFIG_PATH), help="Config file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.setup:
        setup_wizard()
        return

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found at {config_path}")
        print("Run 'python pecron_monitor.py --setup' to create it.")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    monitor = PecronMonitor(config)

    # Handle graceful shutdown
    def _signal_handler(sig, frame):
        log.info("Received signal %s, stopping...", sig)
        monitor.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.status:
        monitor.status_once()
    else:
        monitor.run()


if __name__ == "__main__":
    main()
