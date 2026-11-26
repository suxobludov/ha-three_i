"""Общие HTTP/MQTT механизмы облака 3i (Picea / 3irobotix).

Здесь только криптография и авторизация, без привязки к платформе HA,
чтобы config_flow и платформы использовали один и тот же код.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import ssl
import string
import time
import urllib.request

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    APP_HOST,
    APP_VERSION_CODE,
    APP_VERSION_NAME,
    CONF_DEVICE_ID,
    CONF_PROJECT,
    CONF_TENANT_ID,
    LOGIN_PATH,
    PHONE_BRAND,
    PROJECT,
    PROTOCOL_VERSION,
    TENANT_ID,
)

_LOGGER = logging.getLogger(__name__)


class ThreeIAuthError(Exception):
    """Неверный логин/пароль или недоступно облако 3i."""


def _aes_key(tenant_id: str) -> bytes:
    return hashlib.md5(tenant_id.encode()).hexdigest()[8:24].encode()


def encrypt_aes(tenant_id: str, text: str) -> str:
    """AES-ECB с PKCS7-дополнением, результат в base64."""
    from cryptography.hazmat.primitives.ciphers import (  # noqa: PLC0415
        Cipher,
        algorithms,
        modes,
    )

    raw = text.encode()
    pad = 16 - len(raw) % 16
    enc = Cipher(algorithms.AES(_aes_key(tenant_id)), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(raw + bytes([pad]) * pad) + enc.finalize()).decode()


def sign_data(auth: str, ts: str, nonce: str, body: dict) -> str:
    """Подпись запроса: md5(auth + ts + nonce + конкатенация ключей и значений)."""
    data = ""
    for key, value in body.items():
        data += key
        if value is None:
            data += "null"
        elif isinstance(value, str):
            data += value
        elif isinstance(value, (dict, list, bool)):
            data += json.dumps(value, separators=(",", ":"))
        else:
            data += str(value)
    return hashlib.md5((auth + ts + nonce + data).encode()).hexdigest()


def _insecure_context() -> ssl.SSLContext:
    """Сертификат брокера/облака самоподписанный — проверку приходится отключать."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def login(username: str, password: str, tenant_id: str = TENANT_ID,
          project: str = PROJECT) -> tuple[str, str]:
    """Авторизация в облаке 3i.

    Возвращает (user_id, emq_token). Бросает ThreeIAuthError при отказе.
    """
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    ts = str(int(time.time()))
    body = {
        "tenantId": tenant_id,
        "lang": "en",
        "token": None,
        "userId": None,
        "password": encrypt_aes(tenant_id, password),
        "username": encrypt_aes(tenant_id, username),
        "authcode": None,
        "projectType": project,
        "versionCode": APP_VERSION_CODE,
        "versionName": APP_VERSION_NAME,
        "phoneBrand": encrypt_aes(tenant_id, PHONE_BRAND),
        "phoneSys": 1,
        "noticeSetting": {"andIpad": nonce[:19], "android": nonce[:19]},
    }
    headers = {
        "User-Agent": f"Android_{tenant_id}",
        "tenantId": tenant_id,
        "ts": ts,
        "nonce": nonce,
        "sign": sign_data("", ts, nonce, body),
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        f"https://{APP_HOST}{LOGIN_PATH}",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_insecure_context(), timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as err:  # сеть, TLS, не-JSON ответ
        raise ThreeIAuthError(f"cloud_unreachable: {err}") from err

    if payload.get("code") != 0:
        _LOGGER.error("3i: вход отклонён, code=%s", payload.get("code"))
        raise ThreeIAuthError(f"login_rejected: code={payload.get('code')}")

    result = payload.get("result") or {}
    data = result.get("data") or {}
    user_id = result.get("id") or data.get("id")
    token = (
        data.get("emqToken") or data.get("EMQ_TOKEN")
        or result.get("emqToken") or result.get("EMQ_TOKEN")
    )
    if not user_id or not token:
        raise ThreeIAuthError("no_token: облако не вернуло EMQ-токен")
    return str(user_id), str(token)


def login_from_config(config: dict) -> tuple[str, str]:
    """То же, но берёт значения из data config entry."""
    return login(
        config[CONF_USERNAME],
        config[CONF_PASSWORD],
        config.get(CONF_TENANT_ID) or TENANT_ID,
        config.get(CONF_PROJECT) or PROJECT,
    )


def envelope(user_id: str, tenant_id: str, method: str, params: dict) -> str:
    """Оболочка пакета протокола 3i версии 1.0.1."""
    now_s = int(time.time())
    now_us = int(time.time() * 1_000_000)
    return json.dumps(
        {
            "msgId": str(now_us),
            "version": PROTOCOL_VERSION,
            "tenantId": f"Android_{tenant_id}_{user_id}",
            "method": method,
            "params": params,
            "gtid": f"{user_id}-{now_s}",
        }
    )


def topic(product_id: str, device_id: str, tail: str) -> str:
    """Полный MQTT-топик устройства."""
    return f"/mqtt/{product_id}/{device_id}/{tail}"


def device_id_from_config(config: dict) -> str:
    """Серийный номер робота из настроек (обязателен)."""
    device_id = str(config.get(CONF_DEVICE_ID) or "").strip()
    if not device_id:
        raise ThreeIAuthError("device_id не задан")
    return device_id
