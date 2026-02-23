#!/usr/bin/env python3
"""
Pecron Battery Monitor & Controller — real-time monitoring and control
via Quectel cloud API. Works with any Pecron power station.

Usage:
    python pecron_monitor.py --setup        # Interactive setup wizard
    python pecron_monitor.py                # Start monitoring
    python pecron_monitor.py --status       # One-shot status check
    python pecron_monitor.py --ac on        # Turn AC output on
    python pecron_monitor.py --ac off       # Turn AC output off
    python pecron_monitor.py --dc on        # Turn DC output on
    python pecron_monitor.py --dc off       # Turn DC output off
    python pecron_monitor.py --homeassistant # Start with Home Assistant MQTT bridge
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
import threading
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
# Region configurations
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

# ---------------------------------------------------------------------------
# Data point IDs (from Quectel TSL — Thing Specification Language)
# These are universal for each Pecron product model.
# The TSL is fetched dynamically; these are E1500LFP defaults as fallback.
# ---------------------------------------------------------------------------
DEFAULT_CONTROLS = {
    "ac_switch_hm":           {"id": 40, "type": "BOOL", "desc": "AC output"},
    "dc_switch_hm":           {"id": 38, "type": "BOOL", "desc": "DC output"},
    "ups_status_hm":          {"id": 27, "type": "BOOL", "desc": "UPS mode"},
    "auto_light_flag_as":     {"id": 43, "type": "BOOL", "desc": "Auto screen light"},
    "machine_screen_light_as":{"id": 45, "type": "ENUM", "desc": "Screen brightness"},
}

CONFIG_PATH = Path(__file__).parent / "config.yaml"
log = logging.getLogger("pecron")


# ===========================================================================
# Authentication
# ===========================================================================

def _make_auth_params(email: str, password: str, region: dict) -> dict:
    rand = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    md5 = hashlib.md5(rand.encode()).hexdigest().upper()
    aes_key = md5[8:24]
    iv = aes_key[8:16] + aes_key[0:8]
    cipher = AES.new(aes_key.encode(), AES.MODE_CBC, iv.encode())
    enc_pwd = base64.b64encode(cipher.encrypt(pad(password.encode(), 16))).decode()
    sig_input = email + enc_pwd + rand + region["user_domain_secret"]
    signature = hashlib.sha256(sig_input.encode()).hexdigest()
    return {
        "email": email, "pwd": enc_pwd, "random": rand,
        "userDomain": region["user_domain"], "signature": signature,
    }


def login(email: str, password: str, region: dict) -> dict:
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
    jwt_parts = token.split(".")
    payload_b64 = jwt_parts[1] + "=" * (4 - len(jwt_parts[1]) % 4)
    jwt_payload = json.loads(base64.b64decode(payload_b64))
    return {
        "token": token, "uid": jwt_payload["uid"],
        "expires_at": jwt_payload.get("exp", 0),
    }


# ===========================================================================
# Device discovery
# ===========================================================================

def get_product_catalog(token: str, region: dict) -> dict:
    url = region["base_url"] + "/v2/enduser/enduserapi/getProductList?pageNum=1&pageSize=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", token)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    if body.get("code") != 200:
        return {}
    return {p["productKey"]: p["name"] for p in body.get("data", {}).get("list", [])}


def get_product_tsl(token: str, region: dict, product_key: str) -> dict:
    """Fetch TSL (data model) for a product — gives us data point IDs."""
    url = region["base_url"] + f"/v2/binding/enduserapi/productTSL?pk={product_key}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        if body.get("code") == 200:
            tsl = body["data"]
            controls = {}
            for prop in tsl.get("properties", []):
                dt = prop.get("dataType", {})
                dtype = dt.get("type", dt) if isinstance(dt, dict) else str(dt)
                controls[prop["code"]] = {
                    "id": prop["id"], "type": dtype, "desc": prop.get("name", prop["code"]),
                }
            return controls
    except Exception as e:
        log.debug("TSL fetch failed: %s", e)
    return {}


def verify_device(token: str, region: dict, product_key: str, device_key: str) -> dict:
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
    catalog = get_product_catalog(token, region)
    devices = []
    configured = config.get("devices", [])
    if not configured:
        raise RuntimeError(
            "No devices configured. Run --setup or add devices to config.yaml.\n"
            "Find your device key in the Pecron app: Device → Settings → Device Info"
        )
    for d in configured:
        pk = d.get("product_key", "")
        dk = d.get("device_key", "")
        name = catalog.get(pk, d.get("name", "Unknown"))
        info = verify_device(token, region, pk, dk)
        if info:
            # Fetch TSL for this product
            tsl = get_product_tsl(token, region, pk)
            devices.append({
                "product_key": pk, "device_key": dk,
                "device_name": info.get("productName", name),
                "product_name": info.get("productName", name),
                "controls": tsl or DEFAULT_CONTROLS,
            })
            log.info("  ✅ %s (%s)", name, dk)
        else:
            log.warning("  ❌ %s (%s) — not found or not bound", name, dk)
    return devices


# ===========================================================================
# TTLV protocol
# ===========================================================================

def _encode_varint(val: int) -> bytes:
    if val == 0:
        return b'\x00'
    result = []
    while val > 0:
        result.append(val & 0xFF)
        val >>= 8
    return bytes(reversed(result))


def _build_packet(packet_id: int, cmd: int, payload: bytes = b'') -> bytes:
    inner = struct.pack(">HH", packet_id, cmd) + payload
    crc = sum(inner) & 0xFF
    length = len(inner) + 1
    return b"\xaa\xaa" + struct.pack(">H", length) + bytes([crc]) + inner


def build_ttlv_read(packet_id: int = 1) -> bytes:
    """cmd=0x0011: request device status."""
    return _build_packet(packet_id, 0x0011)


def build_ttlv_write_bool(packet_id: int, data_point_id: int, value: bool) -> bytes:
    """cmd=0x0013: write a boolean data point."""
    tag = (data_point_id << 3) | (1 if value else 0)
    payload = _encode_varint(tag)
    return _build_packet(packet_id, 0x0013, payload)


def build_ttlv_write_enum(packet_id: int, data_point_id: int, value: int) -> bytes:
    """cmd=0x0013: write an enum/int data point."""
    tag = (data_point_id << 3) | 2  # type 2 = number
    payload = _encode_varint(tag) + _encode_varint(value)
    return _build_packet(packet_id, 0x0013, payload)


# ===========================================================================
# Home Assistant MQTT Discovery
# ===========================================================================

class HomeAssistantBridge:
    """Publishes Home Assistant MQTT auto-discovery config and state updates."""

    def __init__(self, ha_config: dict, devices: list):
        self.ha_config = ha_config
        self.devices = devices
        self.client = None
        self.discovery_prefix = ha_config.get("discovery_prefix", "homeassistant")
        self._connected = False

    def connect(self):
        host = self.ha_config.get("mqtt_host", "localhost")
        port = self.ha_config.get("mqtt_port", 1883)
        user = self.ha_config.get("mqtt_user", "")
        pw = self.ha_config.get("mqtt_password", "")

        self.client = mqtt.Client(
            client_id="pecron_ha_bridge",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if user:
            self.client.username_pw_set(user, pw)

        def on_connect(client, ud, flags, rc, props=None):
            if rc == mqtt.CONNACK_ACCEPTED:
                self._connected = True
                log.info("Home Assistant MQTT bridge connected to %s:%d", host, port)
                self._publish_discovery()
                # Subscribe to command topics
                for device in self.devices:
                    dk = device["device_key"]
                    for ctrl in ["ac", "dc", "ups"]:
                        client.subscribe(f"pecron/{dk}/{ctrl}/set", qos=1)

        def on_message(client, ud, msg):
            # Handle HA commands
            parts = msg.topic.split("/")
            if len(parts) == 4 and parts[3] == "set":
                dk = parts[1]
                ctrl = parts[2]
                payload = msg.payload.decode().upper()
                self._handle_command(dk, ctrl, payload)

        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.client.connect(host, port)
        self.client.loop_start()

    def _handle_command(self, device_key: str, control: str, payload: str):
        """Called when HA sends a command. Delegates to the monitor."""
        # This will be wired up by PecronMonitor
        if hasattr(self, 'command_callback'):
            self.command_callback(device_key, control, payload == "ON")

    def _publish_discovery(self):
        """Publish HA MQTT auto-discovery messages."""
        for device in self.devices:
            dk = device["device_key"]
            name = device["device_name"]
            dev_info = {
                "identifiers": [f"pecron_{dk}"],
                "name": f"Pecron {name}",
                "manufacturer": "Pecron",
                "model": name,
            }

            # Battery sensor
            self._pub_config("sensor", dk, "battery", {
                "name": "Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
                "state_topic": f"pecron/{dk}/state",
                "value_template": "{{ value_json.battery_percent }}",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_battery",
            })

            # Voltage sensor
            self._pub_config("sensor", dk, "voltage", {
                "name": "Voltage",
                "device_class": "voltage",
                "unit_of_measurement": "V",
                "state_topic": f"pecron/{dk}/state",
                "value_template": "{{ value_json.voltage }}",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_voltage",
            })

            # Temperature sensor
            self._pub_config("sensor", dk, "temperature", {
                "name": "Temperature",
                "device_class": "temperature",
                "unit_of_measurement": "°C",
                "state_topic": f"pecron/{dk}/state",
                "value_template": "{{ value_json.temperature }}",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_temperature",
            })

            # Power in/out sensors
            for key, label in [("total_input", "Input Power"), ("total_output", "Output Power")]:
                self._pub_config("sensor", dk, key, {
                    "name": label,
                    "device_class": "power",
                    "unit_of_measurement": "W",
                    "state_topic": f"pecron/{dk}/state",
                    "value_template": f"{{{{ value_json.{key}_power }}}}",
                    "device": dev_info,
                    "unique_id": f"pecron_{dk}_{key}",
                })

            # Remaining time sensor
            self._pub_config("sensor", dk, "remaining", {
                "name": "Remaining Time",
                "icon": "mdi:timer-outline",
                "unit_of_measurement": "min",
                "state_topic": f"pecron/{dk}/state",
                "value_template": "{{ value_json.remain_minutes }}",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_remaining",
            })

            # AC switch
            self._pub_config("switch", dk, "ac", {
                "name": "AC Output",
                "icon": "mdi:power-plug",
                "state_topic": f"pecron/{dk}/state",
                "command_topic": f"pecron/{dk}/ac/set",
                "value_template": "{{ value_json.ac_switch }}",
                "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_ac",
            })

            # DC switch
            self._pub_config("switch", dk, "dc", {
                "name": "DC Output",
                "icon": "mdi:usb-port",
                "state_topic": f"pecron/{dk}/state",
                "command_topic": f"pecron/{dk}/dc/set",
                "value_template": "{{ value_json.dc_switch }}",
                "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_dc",
            })

            # UPS switch
            self._pub_config("switch", dk, "ups", {
                "name": "UPS Mode",
                "icon": "mdi:shield-battery",
                "state_topic": f"pecron/{dk}/state",
                "command_topic": f"pecron/{dk}/ups/set",
                "value_template": "{{ value_json.ups_mode }}",
                "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF",
                "device": dev_info,
                "unique_id": f"pecron_{dk}_ups",
            })

        log.info("Published Home Assistant discovery configs")

    def _pub_config(self, component: str, dk: str, key: str, config: dict):
        topic = f"{self.discovery_prefix}/{component}/pecron_{dk}/{key}/config"
        self.client.publish(topic, json.dumps(config), qos=1, retain=True)

    def publish_state(self, device_key: str, kv: dict):
        """Publish current state to HA."""
        if not self._connected:
            return

        bp = kv.get("host_packet_data_jdb", {})
        ac_out = kv.get("ac_data_output_hm", {})

        state = {
            "battery_percent": int(bp.get("host_packet_electric_percentage", 0)),
            "voltage": round(float(bp.get("host_packet_voltage", 0)), 1),
            "temperature": int(bp.get("host_packet_temp", 0)),
            "total_input_power": int(kv.get("total_input_power", 0)),
            "total_output_power": int(kv.get("total_output_power", 0)),
            "remain_minutes": int(kv.get("remain_time", 0)),
            "ac_switch": "ON" if kv.get("ac_switch_hm") else "OFF",
            "dc_switch": "ON" if kv.get("dc_switch_hm") else "OFF",
            "ups_mode": "ON" if kv.get("ups_status_hm") else "OFF",
            "ac_output_power": int(ac_out.get("ac_output_power", 0)),
            "ac_output_voltage": int(ac_out.get("ac_output_voltage", 0)),
        }

        self.client.publish(f"pecron/{device_key}/state", json.dumps(state), qos=1, retain=True)

    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()


# ===========================================================================
# Main monitor
# ===========================================================================

class PecronMonitor:
    def __init__(self, config: dict):
        self.config = config
        self.region = REGIONS[config["region"]]
        self.token_data = None
        self.mqtt_client = None
        self.devices = []
        self.latest_data = {}
        self.last_alert = {}
        self._packet_id = 0
        self._running = False
        self.ha_bridge = None

        # Automation rules
        self.rules = config.get("rules", [])

    def _next_packet_id(self) -> int:
        self._packet_id = (self._packet_id + 1) % 65535
        return self._packet_id

    def authenticate(self):
        log.info("Logging in to Pecron cloud (%s)...", self.region["name"])
        self.token_data = login(self.config["email"], self.config["password"], self.region)
        log.info("Logged in as %s", self.token_data["uid"])
        log.info("Resolving devices...")
        self.devices = resolve_devices(self.config, self.token_data["token"], self.region)
        if not self.devices:
            raise RuntimeError("No valid devices found.")

    def _channel_id(self, device: dict) -> str:
        return f"qd{device['product_key']}{device['device_key']}"

    def _find_device(self, device_key: str) -> dict:
        for d in self.devices:
            if d["device_key"] == device_key:
                return d
        return {}

    # --- MQTT callbacks ---

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != mqtt.CONNACK_ACCEPTED:
            log.error("MQTT connection failed: %s", mqtt.connack_string(rc))
            return
        log.info("MQTT connected")
        for device in self.devices:
            cid = self._channel_id(device)
            for suffix in ["bus_", "ack_", "onl_"]:
                client.subscribe(f"q/2/d/{cid}/{suffix}", qos=1)
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
            log.info("Device %s is now %s", device_key, "online" if online else "offline")

    # --- Data processing ---

    def _process_data(self, device_key: str, kv: dict):
        bp = kv.get("host_packet_data_jdb", {})
        battery_pct = int(bp.get("host_packet_electric_percentage", -1))
        voltage = float(bp.get("host_packet_voltage", 0))
        temp = int(bp.get("host_packet_temp", 0))
        total_in = int(kv.get("total_input_power", 0))
        total_out = int(kv.get("total_output_power", 0))
        remain = int(kv.get("remain_time", 0))

        log.info("🔋 %s%% | %.1fV | %d°C | ⚡ In:%dW Out:%dW | ⏱ %dh%dm",
                 battery_pct, voltage, temp, total_in, total_out,
                 remain // 60, remain % 60)

        # Publish to Home Assistant
        if self.ha_bridge:
            self.ha_bridge.publish_state(device_key, kv)

        # Check alert thresholds
        self._check_alerts(device_key, battery_pct, voltage, remain)

        # Evaluate automation rules
        self._evaluate_rules(device_key, kv, battery_pct)

    def _check_alerts(self, device_key, battery_pct, voltage, remain):
        alerts = self.config.get("alerts", {})
        threshold = alerts.get("low_battery_percent", 20)
        cooldown = alerts.get("cooldown_minutes", 30) * 60
        if battery_pct >= 0 and battery_pct <= threshold:
            now = time.time()
            last = self.last_alert.get(device_key, 0)
            if now - last > cooldown:
                self.last_alert[device_key] = now
                self._send_alert(device_key, battery_pct, voltage, remain)

    def _send_alert(self, device_key, battery_pct, voltage, remain_min):
        msg = (f"⚠️ Pecron Low Battery Alert\n"
               f"Battery: {battery_pct}%\nVoltage: {voltage:.1f}V\n"
               f"Remaining: {remain_min // 60}h {remain_min % 60}m\n"
               f"Device: {device_key}")
        log.warning(msg)
        alerts = self.config.get("alerts", {})

        tg = alerts.get("telegram", {})
        if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
            try:
                url = f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage"
                data = urllib.parse.urlencode({"chat_id": tg["chat_id"], "text": msg}).encode()
                urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
            except Exception as e:
                log.error("Telegram alert failed: %s", e)

        ntfy = alerts.get("ntfy", {})
        if ntfy.get("enabled") and ntfy.get("url"):
            try:
                req = urllib.request.Request(ntfy["url"], data=msg.encode(),
                                             headers={"Title": f"Pecron Battery {battery_pct}%"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                log.error("ntfy alert failed: %s", e)

        wh = alerts.get("webhook", {})
        if wh.get("enabled") and wh.get("url"):
            try:
                payload = json.dumps({"battery_percent": battery_pct, "voltage": voltage,
                                       "remain_minutes": remain_min, "device_key": device_key,
                                       "message": msg}).encode()
                req = urllib.request.Request(wh["url"], data=payload,
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                log.error("Webhook alert failed: %s", e)

    # --- Control commands ---

    def send_bool_control(self, device_key: str, control_code: str, value: bool):
        """Send a boolean control command (e.g., ac_switch_hm, dc_switch_hm)."""
        device = self._find_device(device_key)
        if not device:
            log.error("Device %s not found", device_key)
            return False

        controls = device.get("controls", DEFAULT_CONTROLS)
        ctrl = controls.get(control_code)
        if not ctrl:
            log.error("Control %s not found for device %s", control_code, device_key)
            return False

        cid = self._channel_id(device)
        pid = self._next_packet_id()
        pkt = build_ttlv_write_bool(pid, ctrl["id"], value)
        self.mqtt_client.publish(f"q/1/d/{cid}/bus", pkt, qos=1)
        log.info("Sent %s=%s to %s (packet=%d)", control_code, value, device_key, pid)
        return True

    def set_ac(self, device_key: str, on: bool):
        return self.send_bool_control(device_key, "ac_switch_hm", on)

    def set_dc(self, device_key: str, on: bool):
        return self.send_bool_control(device_key, "dc_switch_hm", on)

    def set_ups(self, device_key: str, on: bool):
        return self.send_bool_control(device_key, "ups_status_hm", on)

    # --- Automation rules ---

    def _evaluate_rules(self, device_key: str, kv: dict, battery_pct: int):
        """Evaluate automation rules against current state."""
        for rule in self.rules:
            if rule.get("device_key") and rule["device_key"] != device_key:
                continue

            try:
                condition = rule.get("condition", {})
                action = rule.get("action", {})

                # Check condition
                triggered = False
                if "battery_below" in condition:
                    triggered = battery_pct <= condition["battery_below"]
                elif "battery_above" in condition:
                    triggered = battery_pct >= condition["battery_above"]
                elif "input_power_below" in condition:
                    triggered = int(kv.get("total_input_power", 0)) <= condition["input_power_below"]
                elif "input_power_above" in condition:
                    triggered = int(kv.get("total_input_power", 0)) >= condition["input_power_above"]
                elif "schedule" in condition:
                    # Time-based: "HH:MM" format
                    now = datetime.now().strftime("%H:%M")
                    triggered = now == condition["schedule"]

                if not triggered:
                    continue

                # Check cooldown
                rule_id = rule.get("name", str(rule))
                cooldown = rule.get("cooldown_minutes", 5) * 60
                now_ts = time.time()
                last = self.last_alert.get(f"rule_{rule_id}", 0)
                if now_ts - last < cooldown:
                    continue
                self.last_alert[f"rule_{rule_id}"] = now_ts

                # Execute action
                target_dk = action.get("device_key", device_key)
                if "set_ac" in action:
                    self.set_ac(target_dk, action["set_ac"])
                    log.info("Rule '%s': set AC=%s on %s", rule.get("name"), action["set_ac"], target_dk)
                if "set_dc" in action:
                    self.set_dc(target_dk, action["set_dc"])
                    log.info("Rule '%s': set DC=%s on %s", rule.get("name"), action["set_dc"], target_dk)
                if "set_ups" in action:
                    self.set_ups(target_dk, action["set_ups"])
                    log.info("Rule '%s': set UPS=%s on %s", rule.get("name"), action["set_ups"], target_dk)

            except Exception as e:
                log.error("Rule evaluation error: %s", e)

    # --- Status request ---

    def _request_status(self):
        for device in self.devices:
            cid = self._channel_id(device)
            pkt = build_ttlv_read(self._next_packet_id())
            self.mqtt_client.publish(f"q/1/d/{cid}/bus", pkt, qos=1)

    def _token_needs_refresh(self) -> bool:
        if not self.token_data:
            return True
        return time.time() > (self.token_data["expires_at"] - 300)

    # --- MQTT connection ---

    def connect_mqtt(self):
        client_id = f"qu_{self.token_data['uid']}_{int(time.time() * 1000)}"
        self.mqtt_client = mqtt.Client(
            client_id=client_id, transport="websockets",
            protocol=mqtt.MQTTv311, callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self.mqtt_client.ws_set_options(path=self.region["mqtt_path"])
        self.mqtt_client.tls_set()
        self.mqtt_client.username_pw_set(username="", password=self.token_data["token"])
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)
        log.info("Connecting to MQTT broker %s:%d...",
                 self.region["mqtt_host"], self.region["mqtt_port"])
        self.mqtt_client.connect(self.region["mqtt_host"], self.region["mqtt_port"])
        self.mqtt_client.loop_start()

    # --- Main loop ---

    def run(self, enable_ha=False):
        self._running = True
        self.authenticate()
        self.connect_mqtt()

        if enable_ha:
            ha_config = self.config.get("homeassistant", {})
            if ha_config.get("enabled") or enable_ha:
                self.ha_bridge = HomeAssistantBridge(ha_config, self.devices)
                self.ha_bridge.command_callback = self._ha_command
                self.ha_bridge.connect()

        poll_interval = self.config.get("poll_interval", 60)
        log.info("Monitoring started (polling every %ds)", poll_interval)

        time.sleep(3)
        self._request_status()

        try:
            while self._running:
                time.sleep(poll_interval)
                if self._token_needs_refresh():
                    log.info("Refreshing token...")
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
            if self.ha_bridge:
                self.ha_bridge.disconnect()

    def _ha_command(self, device_key: str, control: str, on: bool):
        """Handle commands from Home Assistant."""
        ctrl_map = {"ac": "ac_switch_hm", "dc": "dc_switch_hm", "ups": "ups_status_hm"}
        code = ctrl_map.get(control)
        if code:
            self.send_bool_control(device_key, code, on)

    def one_shot_command(self, ac=None, dc=None):
        """Connect, send a command, verify, and exit."""
        self.authenticate()
        self.connect_mqtt()
        time.sleep(3)

        for device in self.devices:
            dk = device["device_key"]
            if ac is not None:
                self.set_ac(dk, ac)
            if dc is not None:
                self.set_dc(dk, dc)

        time.sleep(3)
        # Read back state to confirm
        self._request_status()
        time.sleep(5)

        for dk, kv in self.latest_data.items():
            ac_state = "ON" if kv.get("ac_switch_hm") else "OFF"
            dc_state = "ON" if kv.get("dc_switch_hm") else "OFF"
            print(f"Device {dk}: AC={ac_state} DC={dc_state}")

        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()

    def status_once(self):
        self.authenticate()
        self.connect_mqtt()
        time.sleep(3)
        self._request_status()
        time.sleep(5)

        for dk, kv in self.latest_data.items():
            bp = kv.get("host_packet_data_jdb", {})
            ac_out = kv.get("ac_data_output_hm", {})
            dc_out = kv.get("dc_data_output_hm", {})
            ac_in = kv.get("ac_data_input_hm", {})
            dc_in = kv.get("dc_data_input_hm", {})
            remain = int(kv.get("remain_time", 0))
            packs = kv.get("charging_pack_data_jdb", [])

            print(f"\n{'=' * 50}")
            print(f"Device: {dk}")
            print(f"{'=' * 50}")
            print(f"Battery:       {bp.get('host_packet_electric_percentage', '?')}%")
            print(f"Voltage:       {float(bp.get('host_packet_voltage', 0)):.1f}V")
            print(f"Temperature:   {bp.get('host_packet_temp', '?')}°C")
            print(f"Remaining:     {remain // 60}h {remain % 60}m")
            print(f"Total Input:   {kv.get('total_input_power', 0)}W")
            print(f"Total Output:  {kv.get('total_output_power', 0)}W")
            print(f"AC Output:     {ac_out.get('ac_output_power', 0)}W @ {ac_out.get('ac_output_voltage', '?')}V")
            print(f"DC Output:     {dc_out.get('dc_output_power', 0)}W")
            print(f"AC Input:      {ac_in.get('ac_power', 0)}W")
            print(f"DC Input:      {dc_in.get('dc_input_power', 0)}W")
            print(f"AC Switch:     {'ON' if kv.get('ac_switch_hm') else 'OFF'}")
            print(f"DC Switch:     {'ON' if kv.get('dc_switch_hm') else 'OFF'}")
            print(f"UPS Mode:      {'ON' if kv.get('ups_status_hm') else 'OFF'}")

            for i, pack in enumerate(packs):
                if int(pack.get("charging_pack_status", 4)) != 4:
                    print(f"Pack {i}:        {pack.get('charging_pack_battery', '?')}% "
                          f"{float(pack.get('charging_pack_voltage', 0)):.1f}V")

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

    print("\nTesting login...")
    try:
        token_data = login(email, password, REGIONS[region])
        print(f"✅ Login successful (uid: {token_data['uid']})")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return

    catalog = get_product_catalog(token_data["token"], REGIONS[region])

    print("\n--- Device Setup ---")
    print("You need your device key (MAC address). Find it in the Pecron app:")
    print("  Device → Settings (⚙️) → Device Info → Device Key")
    print("  It looks like: AABBCCDDEEFF\n")

    devices = []
    while True:
        dk = input("Device Key (or press Enter to finish): ").strip().upper()
        if not dk:
            break
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

    print("\n--- Telegram Alerts (optional) ---")
    tg_enabled = input("Enable Telegram alerts? [y/N]: ").strip().lower() == "y"
    tg_token = tg_chat = ""
    if tg_enabled:
        tg_token = input("Bot token: ").strip()
        tg_chat = input("Chat ID: ").strip()

    print("\n--- Home Assistant (optional) ---")
    ha_enabled = input("Enable Home Assistant MQTT bridge? [y/N]: ").strip().lower() == "y"
    ha_host = ha_user = ha_pw = ""
    if ha_enabled:
        ha_host = input("HA MQTT broker host [localhost]: ").strip() or "localhost"
        ha_user = input("HA MQTT username (optional): ").strip()
        ha_pw = input("HA MQTT password (optional): ").strip()

    config = {
        "email": email, "password": password, "region": region,
        "devices": devices, "poll_interval": int(poll),
        "alerts": {
            "low_battery_percent": int(threshold), "cooldown_minutes": 30,
            "telegram": {"enabled": tg_enabled, "bot_token": tg_token, "chat_id": tg_chat},
            "ntfy": {"enabled": False, "url": ""},
            "webhook": {"enabled": False, "url": ""},
        },
        "homeassistant": {
            "enabled": ha_enabled, "mqtt_host": ha_host or "localhost",
            "mqtt_port": 1883, "mqtt_user": ha_user, "mqtt_password": ha_pw,
            "discovery_prefix": "homeassistant",
        },
        "rules": [
            {
                "name": "Low battery — turn off AC",
                "condition": {"battery_below": 10},
                "action": {"set_ac": False},
                "cooldown_minutes": 30,
                "_comment": "Example rule. Edit or remove as needed.",
            },
        ],
    }

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"\n✅ Config saved to {CONFIG_PATH}")
    print("Run 'python pecron_monitor.py' to start monitoring!")
    print("Run 'python pecron_monitor.py --ac on' to test AC control!")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Pecron Battery Monitor & Controller")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--status", action="store_true", help="One-shot status check")
    parser.add_argument("--ac", choices=["on", "off"], help="Turn AC output on/off")
    parser.add_argument("--dc", choices=["on", "off"], help="Turn DC output on/off")
    parser.add_argument("--homeassistant", action="store_true", help="Enable HA MQTT bridge")
    parser.add_argument("--config", type=str, default=str(CONFIG_PATH), help="Config file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
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

    def _signal_handler(sig, frame):
        monitor.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.ac is not None or args.dc is not None:
        monitor.one_shot_command(
            ac=(args.ac == "on") if args.ac else None,
            dc=(args.dc == "on") if args.dc else None,
        )
    elif args.status:
        monitor.status_once()
    else:
        monitor.run(enable_ha=args.homeassistant or config.get("homeassistant", {}).get("enabled", False))


if __name__ == "__main__":
    main()
