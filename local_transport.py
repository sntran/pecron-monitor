"""
LocalTransport for Pecron Monitor — TCP/6607 with AES-CBC encryption.

Connects to Pecron device on LAN, performs WiFi handshake (random exchange + SHA-256 login),
then sends/receives encrypted TTLV commands. Produces the same kv dict structure as MQTT
so existing _process_data() works unchanged.
"""

import base64
import hashlib
import logging
import socket
import struct
import threading
import time
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

log = logging.getLogger("pecron")


# ===========================================================================
# TTLV codec (local TCP variant — AES-CBC encrypted payloads)
# ===========================================================================

def _ttlv_crc(data: bytes) -> int:
    return sum(data) & 0xFF


def _ttlv_byte_stuff(raw: bytes) -> bytes:
    out = bytearray(raw[:2])
    i = 2
    while i < len(raw):
        out.append(raw[i])
        if i < len(raw) - 1 and raw[i] == 0xAA and raw[i + 1] in (0x55, 0xAA):
            out.append(0x55)
        i += 1
    return bytes(out)


def _ttlv_byte_unstuff(raw: bytes) -> bytes:
    out = bytearray(raw[:2])
    i = 2
    while i < len(raw):
        if i < len(raw) - 1 and raw[i] == 0xAA and raw[i + 1] == 0x55:
            out.append(0xAA)
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def _ttlv_build_packet(cmd: int, payload: bytes = b"", packet_id: int = 1) -> bytes:
    inner = struct.pack(">HH", packet_id, cmd) + payload
    crc = _ttlv_crc(inner)
    length = len(inner) + 1
    return _ttlv_byte_stuff(
        b"\xaa\xaa" + struct.pack(">H", length) + bytes([crc]) + inner
    )


def _ttlv_build_bytes_field(tag_id: int, data: bytes) -> bytes:
    tag_word = ((tag_id << 3) & 0xFFF8) | 3
    return struct.pack(">H", tag_word) + struct.pack(">H", len(data)) + data


def _ttlv_parse_packet(data: bytes) -> dict:
    data = _ttlv_byte_unstuff(data)
    if len(data) < 9 or data[0] != 0xAA or data[1] != 0xAA:
        return {"error": "bad packet", "raw": data.hex()}
    pkt_len = struct.unpack(">H", data[2:4])[0]
    pid = struct.unpack(">H", data[5:7])[0]
    cmd = struct.unpack(">H", data[7:9])[0]
    payload = data[9:4 + pkt_len] if len(data) >= 4 + pkt_len else data[9:]
    return {"cmd": cmd, "packet_id": pid, "payload": payload}


def _ttlv_parse_fields(payload: bytes) -> list:
    """Parse TTLV fields from decrypted payload. Returns list of (id, type, value)."""
    fields = []
    i = 0
    while i < len(payload) - 1:
        tag_word = struct.unpack(">H", payload[i:i + 2])[0]
        tag_id = (tag_word >> 3) & 0x1FFF
        tag_type = tag_word & 0x07
        i += 2

        if tag_type in (0, 1):  # Boolean
            fields.append((tag_id, "BOOL", tag_type == 1))
        elif tag_type == 2:  # Number
            if i >= len(payload):
                break
            meta = payload[i]
            i += 1
            sign = (meta >> 7) & 1
            decimals = (meta >> 3) & 0x0F
            byte_count = (meta & 0x07) + 1
            if i + byte_count > len(payload):
                break
            val = int.from_bytes(payload[i:i + byte_count], "big")
            i += byte_count
            if sign:
                val = -val
            if decimals > 0:
                val = val / (10 ** decimals)
            fields.append((tag_id, "NUM", val))
        elif tag_type in (3, 5):  # Bytes
            if i + 2 > len(payload):
                break
            dlen = struct.unpack(">H", payload[i:i + 2])[0]
            i += 2
            fields.append((tag_id, "BYTES", payload[i:i + dlen]))
            i += dlen
        elif tag_type == 4:  # Struct/Array header
            if i + 2 > len(payload):
                break
            count = struct.unpack(">H", payload[i:i + 2])[0]
            i += 2
            fields.append((tag_id, "STRUCT", count))
        else:
            break

    return fields


# ===========================================================================
# TSL ID → kv dict translation
# Maps local TTLV numeric IDs back to the same nested dict keys that the
# cloud MQTT path uses, so _process_data() works unchanged.
# ===========================================================================

# Top-level property ID → TSL code
TSL_TOP = {
    1: "battery_percentage",
    2: "remain_time",
    3: "remain_charging_time",
    4: "total_input_power",
    5: "total_output_power",
    27: "ups_status_hm",
    28: "dc_data_input_hm",
    29: "ac_data_input_hm",
    30: "dc_data_output_hm",
    31: "ac_data_output_hm",
    32: "ac_output_voltage_io",
    33: "ac_output_frequency_io",
    34: "noastime_io",
    35: "host_packet_data_jdb",
    36: "charging_pack_data_jdb",
    37: "device_status_hm",
    38: "dc_switch_hm",
    39: "add_bat_status_hm",
    40: "ac_switch_hm",
    43: "auto_light_flag_as",
    45: "machine_screen_light_as",
    52: "device_manual",
    100: "high_frequency_reporting",
}

# Struct sub-field mappings: parent_code → {sub_id: sub_code}
TSL_STRUCT = {
    "host_packet_data_jdb": {
        1: "host_packet_electric_percentage",
        2: "host_packet_voltage",
        3: "host_packet_current",
        4: "host_packet_temp",
        5: "host_packet_status",
    },
    "ac_data_output_hm": {
        1: "ac_output_hz",
        2: "ac_output_voltage",
        3: "ac_output_pf",
        4: "ac_output_power",
    },
    "dc_data_output_hm": {
        1: "dc_output_power",
    },
    "ac_data_input_hm": {
        1: "ac_power",
    },
    "dc_data_input_hm": {
        1: "dc_input_power",
    },
    "charging_pack_data_jdb": {
        # Array element struct fields
        1: "charging_pack_num",
        2: "charging_pack_battery",
        3: "charging_pack_voltage",
        4: "charging_pack_current",
        5: "charging_pack_temp",
        6: "charging_pack_status",
    },
}

# SENSOR_FIELDS expects these nested paths for the cloud MQTT format:
#   battery_percent → ("host_packet_data_jdb", "host_packet_electric_percentage")
#   voltage → ("host_packet_data_jdb", "host_packet_voltage")
# etc. So we rebuild that same nested dict structure.


def _fields_to_kv(fields: list) -> dict:
    """Convert parsed TTLV fields into the nested kv dict matching MQTT format."""
    kv = {}
    i = 0
    while i < len(fields):
        fid, ftype, fval = fields[i]
        code = TSL_TOP.get(fid)

        if code is None:
            i += 1
            continue

        if ftype == "STRUCT":
            # fval is the count of sub-fields
            sub_map = TSL_STRUCT.get(code, {})
            sub_dict = {}
            count = fval
            j = i + 1
            consumed = 0
            while j < len(fields) and consumed < count:
                sid, stype, sval = fields[j]
                sub_code = sub_map.get(sid, f"field_{sid}")
                if stype == "STRUCT":
                    # Nested struct (e.g., array elements in charging_pack)
                    # For arrays, collect into a list
                    if code == "charging_pack_data_jdb":
                        # Array of pack structs
                        packs = kv.get(code, [])
                        pack = {}
                        elem_count = sval
                        k = j + 1
                        ec = 0
                        while k < len(fields) and ec < elem_count:
                            eid, etype, eval_ = fields[k]
                            elem_code = sub_map.get(eid, f"field_{eid}")
                            if etype not in ("STRUCT",):
                                pack[elem_code] = eval_
                                ec += 1
                            k += 1
                        packs.append(pack)
                        kv[code] = packs
                        j = k
                        consumed += 1
                        continue
                    j += 1
                    consumed += 1
                    continue
                sub_dict[sub_code] = sval
                j += 1
                consumed += 1
            kv[code] = sub_dict
            i = j
        elif ftype == "BOOL":
            kv[code] = fval
        elif ftype == "NUM":
            kv[code] = fval
        elif ftype == "BYTES":
            try:
                kv[code] = fval.decode("utf-8")
            except Exception:
                kv[code] = fval.hex()
        else:
            kv[code] = fval

        i += 1

    return kv


# ===========================================================================
# LocalTransport
# ===========================================================================

class LocalTransport:
    """TCP transport for Pecron devices on LAN (port 6607)."""

    def __init__(self, device_ip: str, auth_key_b64: str, timeout: float = 10.0):
        self.device_ip = device_ip
        self.device_port = 6607
        self.auth_key = base64.b64decode(auth_key_b64)
        self.auth_key_b64 = auth_key_b64
        self.timeout = timeout

        self._sock = None
        self._iv = None  # Set after handshake
        self._encrypted = False
        self._packet_id = 0
        self._lock = threading.Lock()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._encrypted

    def _next_pid(self) -> int:
        self._packet_id = (self._packet_id + 1) % 65535
        return self._packet_id

    def connect(self) -> bool:
        """Perform TCP connect + WiFi handshake (random exchange + login)."""
        try:
            self.disconnect()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.device_ip, self.device_port))
            self._connected = True
            log.info("Local TCP connected to %s:%d", self.device_ip, self.device_port)

            # Step 1: Request random (IV)
            pkt = _ttlv_build_packet(0x7032, b"", self._next_pid())
            self._sock.sendall(pkt)

            resp = self._recv_packet()
            parsed = _ttlv_parse_packet(resp)
            if parsed.get("cmd") != 0x7033:
                log.error("Expected cmd 0x7033, got 0x%04x", parsed.get("cmd", 0))
                self.disconnect()
                return False

            # Extract random string from TTLV field id=1
            fields = _ttlv_parse_fields(parsed["payload"])
            random_str = None
            for fid, ftype, fval in fields:
                if fid == 1 and isinstance(fval, bytes):
                    random_str = fval.decode("utf-8")
                    break
            if not random_str:
                log.error("No random/IV in 0x7033 response")
                self.disconnect()
                return False

            log.debug("Got random/IV: %s", random_str)

            # Step 2: Login with SHA-256 hash
            auth_hex = self.auth_key.hex()
            login_hash = hashlib.sha256(
                f"{auth_hex};{random_str}".encode("utf-8")
            ).hexdigest()
            login_payload = _ttlv_build_bytes_field(2, login_hash.encode("utf-8"))
            pkt = _ttlv_build_packet(0x7034, login_payload, self._next_pid())
            self._sock.sendall(pkt)

            resp = self._recv_packet()
            parsed = _ttlv_parse_packet(resp)
            if parsed.get("cmd") != 0x7035:
                log.error("Login failed — expected 0x7035, got 0x%04x", parsed.get("cmd", 0))
                self.disconnect()
                return False

            # Check login result (field id=3, value=0 means success)
            fields = _ttlv_parse_fields(parsed["payload"])
            for fid, ftype, fval in fields:
                if ftype == "NUM" and fval != 0:
                    log.error("Login rejected (result=%s)", fval)
                    self.disconnect()
                    return False

            # Set up encryption
            iv_bytes = random_str.encode("utf-8")
            if len(iv_bytes) < 16:
                iv_bytes = iv_bytes.ljust(16, b"\x00")
            elif len(iv_bytes) > 16:
                iv_bytes = iv_bytes[:16]
            self._iv = iv_bytes
            self._encrypted = True
            log.info("Local TCP handshake complete — encryption active")
            return True

        except Exception as e:
            log.error("Local connect failed: %s", e)
            self.disconnect()
            return False

    def disconnect(self):
        self._connected = False
        self._encrypted = False
        self._iv = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _recv_packet(self) -> bytes:
        """Read one TTLV packet from socket."""
        buf = b""
        # Sync to 0xAA 0xAA
        while True:
            b = self._sock.recv(1)
            if not b:
                raise ConnectionError("Connection closed")
            buf += b
            if len(buf) >= 2 and buf[-2:] == b"\xaa\xaa":
                buf = b"\xaa\xaa"
                break
            if len(buf) > 200:
                raise ValueError("No sync found")

        # Read length (2 bytes) — careful with byte stuffing
        len_raw = b""
        while len(len_raw) < 2:
            b = self._sock.recv(1)
            if not b:
                raise ConnectionError("Connection closed")
            buf += b
            if buf[-2] == 0xAA and b[0] == 0x55:
                continue
            len_raw += b

        pkt_len = struct.unpack(">H", len_raw)[0]
        remaining = pkt_len
        while remaining > 0:
            chunk = self._sock.recv(min(remaining, 4096))
            if not chunk:
                raise ConnectionError("Connection closed")
            buf += chunk
            remaining -= len(chunk)

        return buf

    def _decrypt(self, data: bytes) -> bytes:
        cipher = AES.new(self.auth_key, AES.MODE_CBC, self._iv)
        return unpad(cipher.decrypt(data), 16)

    def _encrypt(self, data: bytes) -> bytes:
        cipher = AES.new(self.auth_key, AES.MODE_CBC, self._iv)
        return cipher.encrypt(pad(data, 16))

    def read_status(self) -> dict:
        """Send read command and return kv dict matching MQTT format."""
        if not self.connected:
            return {}

        with self._lock:
            try:
                # Send cmd 0x0011 (read)
                pkt = _ttlv_build_packet(0x0011, b"", self._next_pid())
                self._sock.sendall(pkt)

                # Read ACK (0x0012) — typically empty encrypted payload
                resp = self._recv_packet()
                parsed = _ttlv_parse_packet(resp)

                # Read the actual data response (0x0014)
                resp2 = self._recv_packet()
                parsed2 = _ttlv_parse_packet(resp2)

                payload = parsed2.get("payload", b"")
                if not payload:
                    # Maybe the first response was the data
                    payload = parsed.get("payload", b"")

                if not payload:
                    log.warning("No payload in local read response")
                    return {}

                decrypted = self._decrypt(payload)
                fields = _ttlv_parse_fields(decrypted)
                kv = _fields_to_kv(fields)
                return kv

            except Exception as e:
                log.error("Local read failed: %s", e)
                self._connected = False
                return {}

    def send_control(self, data_point_id: int, value, ctrl_type: str = "BOOL") -> bool:
        """Send a control command over local TCP."""
        if not self.connected:
            return False

        with self._lock:
            try:
                ctrl_type = ctrl_type.upper()
                if ctrl_type == "BOOL":
                    tag = (data_point_id << 3) | (1 if value else 0)
                    raw_payload = struct.pack(">H", tag)
                else:
                    tag = (data_point_id << 3) | 2
                    raw_payload = struct.pack(">H", tag) + bytes([int(value)])

                enc_payload = self._encrypt(raw_payload)
                pkt = _ttlv_build_packet(0x0013, enc_payload, self._next_pid())
                self._sock.sendall(pkt)

                resp = self._recv_packet()
                parsed = _ttlv_parse_packet(resp)
                log.info("Local control response: cmd=0x%04x", parsed.get("cmd", 0))
                return True

            except Exception as e:
                log.error("Local control failed: %s", e)
                self._connected = False
                return False


def get_auth_key(token: str, region: dict, pk: str, dk: str) -> str:
    """Fetch the device authKey from Quectel cloud (one-time, can be cached)."""
    import urllib.parse
    import urllib.request
    import json

    url = region["base_url"] + "/v2/binding/enduserapi/regenerateAuthKey"
    data = urllib.parse.urlencode({"pk": pk, "dk": dk}).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Authorization", token)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    if body.get("code") == 200:
        return body["data"]["authKey"]
    raise RuntimeError(f"Failed to get authKey: {body.get('msg', body)}")
