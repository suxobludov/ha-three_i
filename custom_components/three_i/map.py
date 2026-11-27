"""Карта помещения из облака 3i (Picea / 3irobotix AIoT).

Робот после построения/уборки заливает карту файлом в облачное хранилище.
Схема та же, что у роботов Kärcher на этом же облаке (см. lafriks/python-karcher):

  1. REST-вход (/user-center/auth/login) -> userId + AUTH-токен;
  2. список устройств -> mac, productId, productModeCode робота;
  3. /storage-management/storage/aws/getAccessUrl для файла
     <tenantId>/<productModeCode>/<sn>/01-01-2022/map/temp/0046690461_<sn>_1
     -> временная ссылка на скачивание;
  4. файл = base64(AES-128-ECB(hex(zlib(protobuf RobotMap)))),
     ключ выводится из sn, mac и productId.

Protobuf разбирается вручную (без зависимостей), PNG тоже собирается вручную.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import string
import struct
import time
import urllib.request
import zlib

from .api import _insecure_context, encrypt_aes, sign_data
from .const import APP_HOST, APP_VERSION_CODE, APP_VERSION_NAME, PROJECT, TENANT_ID

_LOGGER = logging.getLogger(__name__)

# Значения клеток сетки mapData
CELL_UNKNOWN = 0
CELL_FLOOR = 196
CELL_WALL = 255
CELL_CHARGER = 253

COUNTRY_CODES = ("RUS", "CHN", "")  # код страны для getAccessUrl: подбирается и запоминается

# Палитра (RGBA). Фон прозрачный — картинка ложится и на светлую, и на тёмную тему.
WALL_RGBA = (142, 148, 158, 255)
ROOM_RGBA = [
    (111, 202, 185, 150),  # бирюзовый
    (255, 181, 71, 150),   # янтарный
    (142, 168, 245, 150),  # голубой
    (255, 143, 102, 150),  # коралловый
    (190, 150, 240, 150),  # сиреневый
    (140, 200, 110, 150),  # зелёный
    (240, 130, 170, 150),  # розовый
    (110, 180, 230, 150),  # синий
]
FLOOR_RGBA = (111, 202, 185, 110)
CHARGER_RGBA = (63, 185, 120, 255)
ROBOT_RGBA = (255, 181, 71, 255)
ROBOT_RING_RGBA = (26, 20, 12, 255)

MAX_SIDE_PX = 760


class ThreeIMapError(Exception):
    """Карту получить не удалось."""


# --------------------------------------------------------------------------- облако

def _nonce(n: int = 32) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def _request(method: str, path: str, auth: str = "", uid: str = "", body: dict | None = None) -> dict:
    nonce = _nonce()
    ts = str(int(time.time()))
    headers = {"User-Agent": f"Android_{TENANT_ID}", "tenantId": TENANT_ID, "ts": ts, "nonce": nonce}
    if auth:
        headers["authorization"] = auth
    if uid:
        headers["id"] = uid
    data = None
    if method == "GET":
        headers["sign"] = hashlib.md5((auth + ts + nonce).encode()).hexdigest()
    else:
        headers["sign"] = sign_data(auth, ts, nonce, body or {})
        headers["Content-Type"] = "application/json"
        data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"https://{APP_HOST}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, context=_insecure_context(), timeout=20) as resp:
        return json.loads(resp.read().decode())


def _map_key(sn: str, mac: str, product_id: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: PLC0415

    sub = (mac.replace(":", "").lower() + str(product_id))[:16].encode()
    buf = (sn + "+" + str(product_id) + "+" + sn).encode()
    pad = 16 - len(buf) % 16
    enc = Cipher(algorithms.AES(sub), modes.ECB()).encryptor()
    key = base64.b64encode(enc.update(buf + bytes([pad]) * pad) + enc.finalize()).decode()
    return hashlib.md5(key.encode()).hexdigest()[8:24].encode()


def decrypt_map(sn: str, mac: str, product_id: str, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: PLC0415

    dec = Cipher(algorithms.AES(_map_key(sn, mac, product_id)), modes.ECB()).decryptor()
    buf = dec.update(base64.b64decode(blob)) + dec.finalize()
    raw = bytes.fromhex(buf[: -buf[-1]].decode())
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return raw


class ThreeICloudMap:
    """Скачивание карты из облака (блокирующий код — вызывать в executor)."""

    def __init__(self, username: str, password: str, device_id: str) -> None:
        self._username = username
        self._password = password
        self._sn = device_id.upper()
        self._uid = ""
        self._auth = ""
        self._dev: dict = {}
        self._country: str | None = None

    def _login(self) -> None:
        reg = _nonce(19)
        body = {
            "tenantId": TENANT_ID, "lang": "en", "token": None, "userId": None,
            "password": encrypt_aes(TENANT_ID, self._password),
            "username": encrypt_aes(TENANT_ID, self._username),
            "authcode": None, "projectType": PROJECT,
            "versionCode": APP_VERSION_CODE, "versionName": APP_VERSION_NAME,
            "phoneBrand": encrypt_aes(TENANT_ID, "android"), "phoneSys": 1,
            "noticeSetting": {"andIpad": reg, "android": reg},
        }
        res = _request("POST", "/user-center/auth/login", body=body)
        if res.get("code") != 0:
            raise ThreeIMapError(f"вход отклонён, code={res.get('code')}")
        result = res.get("result") or {}
        data = result.get("data") or {}
        self._uid = str(result.get("id") or data.get("id") or "")
        self._auth = data.get("AUTH") or data.get("auth") or ""
        if not self._uid or not self._auth:
            raise ThreeIMapError("облако не вернуло AUTH-токен")

    def _load_device(self) -> None:
        res = _request("GET", "/smart-home-service/smartHome/user/getDeviceInfoByUserId/" + self._uid,
                       auth=self._auth, uid=self._uid)
        if res.get("code") != 0:
            raise ThreeIMapError(f"список устройств: code={res.get('code')}")
        devices = res.get("result") or []
        dev = next((d for d in devices if str(d.get("sn", "")).upper() == self._sn), None)
        if not dev:
            raise ThreeIMapError("робот с этим серийным номером не найден в аккаунте")
        if not dev.get("mac") or not dev.get("productId") or not dev.get("productModeCode"):
            raise ThreeIMapError("у робота нет mac/productId/productModeCode")
        self._dev = dev

    def _access_url(self, index: int) -> str:
        path = (f"{TENANT_ID}/{self._dev['productModeCode']}/{self._sn}"
                f"/01-01-2022/map/temp/0046690461_{self._sn}_{index}")
        codes = [self._country] if self._country is not None else list(COUNTRY_CODES)
        last = None
        for code in codes:
            res = _request("POST", "/storage-management/storage/aws/getAccessUrl", auth=self._auth, uid=self._uid,
                           body={"dir": path, "countryCode": code, "serviceType": 2, "tenantId": TENANT_ID})
            last = res
            result = res.get("result") or {}
            url = result.get("url")
            if result.get("cdnDomain"):
                url = "https://" + result["cdnDomain"] + "/" + result.get("dir", "")
            if res.get("code") == 0 and url:
                self._country = code
                return url
        raise ThreeIMapError(f"нет ссылки на карту: code={(last or {}).get('code')}")

    def fetch(self, index: int = 1) -> bytes:
        """Вернуть расшифрованный protobuf карты."""
        for attempt in (1, 2):
            try:
                if not self._auth:
                    self._login()
                if not self._dev:
                    self._load_device()
                url = self._access_url(index)
                req = urllib.request.Request(url, headers={"User-Agent": f"Android_{TENANT_ID}"})
                with urllib.request.urlopen(req, context=_insecure_context(), timeout=30) as resp:
                    blob = resp.read()
                return decrypt_map(self._sn, self._dev["mac"], str(self._dev["productId"]), blob)
            except ThreeIMapError:
                if attempt == 2:
                    raise
                self._auth = ""  # токен мог протухнуть — войдём заново
        raise ThreeIMapError("не удалось")


# --------------------------------------------------------------------------- protobuf

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def pb_parse(buf: bytes) -> dict[int, list]:
    """{номер поля: [значения]}; int — varint, bytes — длина+данные или fixed32/64."""
    i, out = 0, {}
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wtype = key >> 3, key & 7
        if wtype == 0:
            val, i = _varint(buf, i)
        elif wtype == 1:
            val = buf[i:i + 8]
            i += 8
        elif wtype == 2:
            ln, i = _varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wtype == 5:
            val = buf[i:i + 4]
            i += 4
        else:
            raise ThreeIMapError(f"protobuf: тип {wtype}")
        out.setdefault(num, []).append(val)
    return out


def _flt(msg: dict, num: int, default: float = 0.0) -> float:
    vals = msg.get(num)
    if not vals or not isinstance(vals[0], (bytes, bytearray)) or len(vals[0]) != 4:
        return default
    return struct.unpack("<f", vals[0])[0]


def _int(msg: dict, num: int, default: int = 0) -> int:
    vals = msg.get(num)
    if not vals or not isinstance(vals[0], int):
        return default
    val = vals[0]
    return val - (1 << 64) if val >= 1 << 63 else val


def _str(msg: dict, num: int) -> str:
    vals = msg.get(num)
    return vals[0].decode("utf-8", "ignore") if vals and isinstance(vals[0], (bytes, bytearray)) else ""


class RobotMap:
    """Разобранная карта (поля по схеме RobotMap)."""

    def __init__(self, raw: bytes) -> None:
        top = pb_parse(raw)
        if 3 not in top or 4 not in top:
            raise ThreeIMapError("в файле нет заголовка или сетки карты")
        head = pb_parse(top[3][0])
        self.map_id = _int(head, 1)
        self.width = _int(head, 2)
        self.height = _int(head, 3)
        self.min_x = _flt(head, 4)
        self.min_y = _flt(head, 5)
        self.resolution = _flt(head, 8, 0.05) or 0.05
        self.grid = pb_parse(top[4][0]).get(1, [b""])[0]
        if len(self.grid) < self.width * self.height:
            raise ThreeIMapError("сетка карты короче, чем заявлено")
        self.name = ""
        for info in top.get(5, []):
            msg = pb_parse(info)
            if _int(msg, 1) == self.map_id:
                self.name = _str(msg, 2)
        self.rooms = []
        for blob in top.get(12, []):
            msg = pb_parse(blob)
            label = pb_parse(msg[8][0]) if 8 in msg else {}
            self.rooms.append({
                "id": _int(msg, 1),
                "name": _str(msg, 2) or f"Комната {_int(msg, 1)}",
                "label": (_flt(label, 1), _flt(label, 2)) if label else None,
            })
        self.chains: dict[int, list[tuple[int, int]]] = {}
        for blob in top.get(14, []):
            msg = pb_parse(blob)
            pts = []
            for pblob in msg.get(2, []):
                pmsg = pb_parse(pblob)
                pts.append((_int(pmsg, 1), _int(pmsg, 2)))
            self.chains[_int(msg, 1)] = pts
        self.charger = self._pose(top, 7, 1, 2)
        self.robot = self._pose(top, 8, 3, 4)

    def _pose(self, top: dict, num: int, fx: int, fy: int) -> tuple[float, float] | None:
        if num not in top:
            return None
        msg = pb_parse(top[num][0])
        x, y = _flt(msg, fx), _flt(msg, fy)
        # 1100 и подобные — признак «не задано»: координата вне поля карты
        if not (self.min_x <= x <= self.min_x + self.width * self.resolution
                and self.min_y <= y <= self.min_y + self.height * self.resolution):
            return None
        return x, y

    def to_cell(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.min_x) / self.resolution, (y - self.min_y) / self.resolution

    @property
    def floor_area_m2(self) -> float:
        cells = sum(1 for v in self.grid if v not in (CELL_UNKNOWN, CELL_WALL))
        return round(cells * self.resolution * self.resolution, 1)


# --------------------------------------------------------------------------- рисование

def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
            + chunk(b"IEND", b""))


def _room_fill(robot_map: RobotMap, x0: int, x1: int, y0: int, y1: int) -> dict[tuple[int, int], int]:
    """Клетка -> id комнаты по контурам комнат (заливка «чёт-нечет» по строкам)."""
    owner: dict[tuple[int, int], int] = {}
    for room_id, pts in robot_map.chains.items():
        if len(pts) < 3:
            continue
        for cy in range(y0, y1):
            yc = cy + 0.5
            xs = []
            for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
                if (ay <= yc < by) or (by <= yc < ay):
                    xs.append(ax + (yc - ay) * (bx - ax) / (by - ay))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                for cx in range(max(x0, int(a + 0.5)), min(x1, int(b + 0.5) + 1)):
                    owner.setdefault((cx, cy), room_id)
    return owner


def render(robot_map: RobotMap, flip_y: bool = True) -> tuple[bytes, dict]:
    """PNG с прозрачным фоном + описание (размеры, комнаты в % от картинки)."""
    w, h, grid = robot_map.width, robot_map.height, robot_map.grid
    xs, ys = [], []
    for idx, val in enumerate(grid):
        if val != CELL_UNKNOWN:
            xs.append(idx % w)
            ys.append(idx // w)
    if not xs:
        raise ThreeIMapError("карта пустая")
    margin = 4
    x0, x1 = max(0, min(xs) - margin), min(w, max(xs) + margin + 1)
    y0, y1 = max(0, min(ys) - margin), min(h, max(ys) + margin + 1)
    scale = max(2, min(16, MAX_SIDE_PX // max(x1 - x0, y1 - y0)))

    room_ids = [r["id"] for r in robot_map.rooms]
    color_of = {rid: ROOM_RGBA[i % len(ROOM_RGBA)] for i, rid in enumerate(room_ids)}
    owner = _room_fill(robot_map, x0, x1, y0, y1) if len(room_ids) > 1 else {}
    single = color_of[room_ids[0]] if len(room_ids) == 1 else FLOOR_RGBA
    clear = bytes(4)
    chargers = []

    rows: list[bytes] = []
    row_order = range(y1 - 1, y0 - 1, -1) if flip_y else range(y0, y1)
    for cy in row_order:
        line = bytearray()
        for cx in range(x0, x1):
            val = grid[cy * w + cx]
            if val == CELL_UNKNOWN:
                px = clear
            elif val == CELL_WALL:
                px = bytes(WALL_RGBA)
            else:
                if val == CELL_CHARGER:
                    chargers.append((cx, cy))
                if val in color_of:
                    rgba = color_of[val]
                elif owner:
                    rgba = color_of.get(owner.get((cx, cy)), FLOOR_RGBA)
                else:
                    rgba = single
                px = bytes(rgba)
            line += px * scale
        row = b"\x00" + bytes(line)
        rows.extend([row] * scale)

    img_w, img_h = (x1 - x0) * scale, (y1 - y0) * scale

    def to_px(cx: float, cy: float) -> tuple[float, float]:
        px = (cx - x0) * scale
        py = ((y1 - cy) if flip_y else (cy - y0)) * scale
        return px, py

    def dot(cx: float, cy: float, radius: float, rgba, ring=None) -> None:
        px, py = to_px(cx, cy)
        r_out = radius + (max(1.5, scale * 0.35) if ring else 0)
        for yy in range(max(0, int(py - r_out)), min(img_h, int(py + r_out) + 1)):
            row = bytearray(rows[yy])
            for xx in range(max(0, int(px - r_out)), min(img_w, int(px + r_out) + 1)):
                d2 = (xx + 0.5 - px) ** 2 + (yy + 0.5 - py) ** 2
                if d2 <= radius * radius:
                    color = rgba
                elif ring and d2 <= r_out * r_out:
                    color = ring
                else:
                    continue
                row[1 + xx * 4:1 + xx * 4 + 4] = bytes(color)
            rows[yy] = bytes(row)

    marker = max(4.0, scale * 1.4)
    if robot_map.charger:
        dot(*robot_map.to_cell(*robot_map.charger), marker, CHARGER_RGBA, ROBOT_RING_RGBA)
    elif chargers:
        cx, cy = chargers[0]
        dot(cx + 0.5, cy + 0.5, marker, CHARGER_RGBA, ROBOT_RING_RGBA)
    if robot_map.robot:
        dot(*robot_map.to_cell(*robot_map.robot), marker, ROBOT_RGBA, ROBOT_RING_RGBA)

    rooms = []
    for room in robot_map.rooms:
        item = {"id": room["id"], "name": room["name"]}
        if room["label"]:
            px, py = to_px(*robot_map.to_cell(*room["label"]))
            item["x_pct"] = round(100 * px / img_w, 1)
            item["y_pct"] = round(100 * py / img_h, 1)
        rooms.append(item)
    info = {
        "map_id": robot_map.map_id,
        "map_name": robot_map.name,
        "rooms": rooms,
        "floor_area_m2": robot_map.floor_area_m2,
        "width_m": round((x1 - x0) * robot_map.resolution, 1),
        "height_m": round((y1 - y0) * robot_map.resolution, 1),
        "image_px": [img_w, img_h],
    }
    return _png(img_w, img_h, rows), info
