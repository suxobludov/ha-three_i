"""Пылесос 3i P10 Ultra: облачный вход + MQTT."""

from __future__ import annotations

import json
import logging
import random
import ssl
import threading
import time

import paho.mqtt.client as mqtt
from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ThreeIAuthError, envelope, login_from_config, topic
from .const import (
    CONSUMABLE_MAX_HOURS,
    CONSUMABLE_PROPERTIES,
    DEFAULT_NAME,
    DOMAIN,
    IGNORED_KEYS,
    MQTT_HOST,
    MQTT_PORT,
    POLL_INTERVAL,
    PRODUCT_ID,
    SIGNAL_MAP_REFRESH,
    STATUS_PROPERTIES,
    STATUS_TEXT,
    TENANT_ID,
)
from . import device_id_for

STATE_CLEANING = "cleaning"
STATE_DOCKED = "docked"
STATE_RETURNING = "returning"
STATE_IDLE = "idle"
STATE_ERROR = "error"
STATE_PAUSED = "paused"

_LOGGER = logging.getLogger(__name__)

SPEED_NAMES = {1: "Тихий", 2: "Стандартный", 3: "Мощный", 4: "Максимум"}
NAME_TO_SPEED = {name: value for value, name in SPEED_NAMES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    config = dict(entry.data)
    config["_entry_id"] = entry.entry_id
    config["_room_ids"] = entry.options.get("room_ids") or entry.data.get("room_ids") or []

    try:
        user_id, token = await hass.async_add_executor_job(login_from_config, config)
    except ThreeIAuthError as err:
        raise ConfigEntryNotReady(f"3i: облако недоступно — повторю автоматически ({err})") from err

    async_add_entities([ThreeIVacuum(entry, user_id, token, config)])


class ThreeIVacuum(StateVacuumEntity):
    """Одно устройство = один робот."""

    _attr_should_poll = False
    should_poll = False
    _attr_has_entity_name = True
    _attr_name = None  # имя берётся из устройства, сущность — сам пылесос

    def __init__(
        self,
        entry: ConfigEntry,
        user_id: str,
        token: str,
        config: dict,
    ) -> None:
        self._entry = entry
        self._user_id = str(user_id)
        self._token = str(token)
        self._config = config

        self._device_id = device_id_for(entry)
        self._product_id = config.get("product_id") or PRODUCT_ID
        self._tenant_id = config.get("tenant_id") or TENANT_ID

        self._attr_unique_id = self._device_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=self._device_id,
        )

        self._state = STATE_IDLE
        self._extra_attrs: dict = {}
        self._stopping = False
        self._refreshing = False

        self._fan_speed_list = list(SPEED_NAMES.values())
        self._fan_speed = SPEED_NAMES[2]
        self._attr_fan_speed_list = self._fan_speed_list
        self._attr_fan_speed = self._fan_speed
        self._attr_supported_features = (
            VacuumEntityFeature.START
            | VacuumEntityFeature.PAUSE
            | VacuumEntityFeature.RETURN_HOME
            | VacuumEntityFeature.STOP
            | VacuumEntityFeature.FAN_SPEED
            | VacuumEntityFeature.STATE
            | VacuumEntityFeature.SEND_COMMAND
        )

        self._client: mqtt.Client | None = None
        self._threads = [
            threading.Thread(target=self._start_mqtt, daemon=True, name="3i-mqtt"),
            threading.Thread(target=self._periodic_poll_loop, daemon=True, name="3i-poll"),
        ]
        for thr in self._threads:
            thr.start()

    # --- MQTT ---------------------------------------------------------------

    def _start_mqtt(self) -> None:
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"{self._user_id}_{random.getrandbits(64):016x}",
                clean_session=True,
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            client.tls_set_context(ctx)
            client.username_pw_set(self._user_id, self._token)
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_disconnect = self._on_disconnect
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            self._client = client
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_forever()
        except Exception:  # noqa: BLE001
            if not self._stopping:
                _LOGGER.exception("3i: ошибка MQTT-соединения")

    def _periodic_poll_loop(self) -> None:
        while not self._stopping:
            time.sleep(POLL_INTERVAL)
            if not self._stopping and self._client and self._client.is_connected():
                self._request_full_status()

    def _publish(self, tail: str, method: str, params: dict) -> None:
        client = self._client
        if not client or not client.is_connected():
            _LOGGER.warning("3i: нет связи с брокером: %s", method)
            return
        client.publish(
            topic(self._product_id, self._device_id, tail),
            envelope(self._user_id, self._tenant_id, method, params),
            qos=1,
        )

    def _send_prop_get(self, prop_list: list[str]) -> None:
        self._publish("thing/service/property/get", "prop.get", {"property": prop_list})

    def _request_full_status(self) -> None:
        self._send_prop_get(STATUS_PROPERTIES)
        time.sleep(0.5)
        self._send_prop_get(CONSUMABLE_PROPERTIES)

    def _invoke_service(self, service_name: str, params: dict | None = None) -> None:
        self._publish(
            f"thing/service_invoke/{service_name}",
            f"service.{service_name}",
            params or {},
        )

    def _set_property(self, cmd_data: dict) -> None:
        self._publish("thing/service/property/set", "prop.set", cmd_data)

    # --- callbacks ----------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc) -> None:  # noqa: ANN001
        if rc != 0:
            _LOGGER.error("3i MQTT: отказ в подключении, rc=%s", rc)
            return
        _LOGGER.info("3i MQTT: подключено")
        client.subscribe(topic(self._product_id, self._device_id, "thing/#"))
        threading.Thread(target=self._request_full_status, daemon=True).start()

    def _on_disconnect(self, client, userdata, rc) -> None:  # noqa: ANN001
        if self._stopping or rc == 0:
            return
        _LOGGER.warning("3i MQTT: связь потеряна, rc=%s", rc)
        threading.Thread(target=self._refresh_token, daemon=True).start()

    def _refresh_token(self) -> None:
        if self._refreshing or self._stopping:
            return
        self._refreshing = True
        try:
            delay = 5
            while not self._stopping:
                try:
                    user_id, new_token = login_from_config(self._config)
                except ThreeIAuthError:
                    user_id, new_token = None, None
                if user_id and new_token:
                    self._user_id = user_id
                    self._token = new_token
                    if self._client:
                        self._client.username_pw_set(user_id, new_token)
                    _LOGGER.info("3i: токен обновлён")
                    return
                time.sleep(delay)
                delay = min(delay * 2, 300)
        finally:
            self._refreshing = False

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        try:
            parts = msg.topic.split("/")
            if len(parts) > 4 and parts[3] != self._device_id:
                return

            skip_topics = (
                "service_invoke/",
                "property/set",
                "property/get",
                "upload_by_map",
                "get_preference",
            )
            if any(x in msg.topic for x in skip_topics) and not msg.topic.endswith(
                "property/get_reply"
            ):
                return

            payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
            params = payload.get("params") if "params" in payload else payload.get("data")
            if not isinstance(params, dict) or not params:
                return

            changed = False
            for key, value in params.items():
                if key in IGNORED_KEYS:
                    continue
                if self._extra_attrs.get(key) != value:
                    changed = True
                self._extra_attrs[key] = value

            if "status" in params:
                new_state = self._map_state(params["status"], params)
                if new_state != self._state:
                    if (self.hass and new_state == STATE_DOCKED
                            and self._state in (STATE_CLEANING, STATE_RETURNING, STATE_PAUSED)):
                        # уборка закончилась — робот зальёт новую карту в облако
                        dispatcher_send(self.hass, SIGNAL_MAP_REFRESH.format(self._entry.entry_id))
                    self._state = new_state
                    changed = True

            if "wind" in params:
                name = SPEED_NAMES.get(params["wind"])
                if name and name != self._attr_fan_speed:
                    self._fan_speed = name
                    self._attr_fan_speed = name
                    changed = True

            if changed:
                self._schedule_state_update()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("3i: ошибка разбора MQTT-пакета")

    # --- state --------------------------------------------------------------

    def _map_state(self, st, params) -> str:
        if st in (3, 5, 6):
            return STATE_CLEANING
        if st == 2:
            return STATE_PAUSED
        if st in (8, 9, 11):
            return STATE_RETURNING
        if (
            st in (4, 7, 10, 14, 15, 16)
            or params.get("charge_state") == 1
            or params.get("station_act") == 1
        ):
            return STATE_DOCKED
        if st == 12:
            return STATE_ERROR
        return STATE_IDLE

    def _schedule_state_update(self) -> None:
        if self.hass:
            self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)

    @property
    def state(self) -> str:
        return self._state

    @property
    def fan_speed(self) -> str:
        return self._fan_speed

    @property
    def fan_speed_list(self) -> list[str]:
        return self._fan_speed_list

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {
            "device_id": self._device_id,
            "product_id": self._product_id,
        }
        attrs.update(self._extra_attrs)

        raw_area = self._extra_attrs.get("cleaning_area")
        if isinstance(raw_area, (int, float)):
            attrs["cleaning_area_m2"] = round(raw_area / 100, 2) if raw_area > 100 else raw_area

        for key, max_hours in CONSUMABLE_MAX_HOURS.items():
            used = self._extra_attrs.get(key)
            if isinstance(used, (int, float)):
                remaining = max(0, max_hours - used)
                attrs[f"{key}_pct"] = int(remaining / max_hours * 100)

        status = self._extra_attrs.get("status")
        if status is not None:
            attrs["station_status"] = STATUS_TEXT.get(status, f"Код {status}")
        if self._extra_attrs.get("dust_action") == 1:
            attrs["station_status"] = "Самоочистка пылесборника"
        elif self._extra_attrs.get("back_to_wash") == 1:
            attrs["station_status"] = "Промывка мопов"
        elif self._extra_attrs.get("station_act") == 1:
            attrs["station_status"] = "База активна (мойка / сушка)"
        elif self._extra_attrs.get("charge_state") == 1:
            attrs["station_status"] = "На базе, зарядка"
        return attrs

    # --- команды ------------------------------------------------------------

    def set_fan_speed(self, fan_speed: str, **kwargs) -> None:
        self._fan_speed = fan_speed
        self._attr_fan_speed = fan_speed
        self._set_property({"wind": NAME_TO_SPEED.get(fan_speed, 2)})
        self._schedule_state_update()

    def start(self, **kwargs) -> None:
        self._invoke_service("set_global_clean", {"ctrl_value": 1, "clean_type": 0, "room_ids": []})

    def pause(self, **kwargs) -> None:
        self._invoke_service("set_global_clean", {"ctrl_value": 2, "clean_type": 0, "room_ids": []})

    def stop(self, **kwargs) -> None:
        self._invoke_service("set_global_clean", {"ctrl_value": 0, "clean_type": 0, "room_ids": []})

    def return_to_base(self, **kwargs) -> None:
        self._invoke_service("start_recharge", {})

    def send_command(self, command, params=None, **kwargs) -> None:
        if command in ("clean_rooms", "set_room_clean", "app_segment_clean"):
            room_ids: list[int] = []
            clean_type = 0
            if isinstance(params, list):
                room_ids = [int(x) for x in params]
            elif isinstance(params, dict):
                raw_ids = params.get("room_ids") or params.get("segments") or []
                room_ids = [int(x) for x in raw_ids]
                clean_type = int(params.get("clean_type", 0))
            if room_ids:
                self._invoke_service(
                    "set_room_clean",
                    {"ctrl_value": 1, "clean_type": clean_type, "room_ids": room_ids},
                )
        elif command == "set_property" and isinstance(params, dict) and params:
            # Любое свойство робота, как его ставит приложение: wind, water, section, repeat_state...
            self._set_property(dict(params))
            for key, value in params.items():
                self._extra_attrs[key] = value
            self._schedule_state_update()
        elif command == "set_water_level" and isinstance(params, dict) and "water" in params:
            value = int(params["water"])
            self._set_property({"water": value})
            self._extra_attrs["water"] = value
            self._schedule_state_update()
        else:
            self._invoke_service(command, params if isinstance(params, dict) else {})

    async def async_will_remove_from_hass(self) -> None:
        self._stopping = True
        if self._client:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
