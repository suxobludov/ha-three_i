"""Автор интеграции 3i Robot Vacuum (ha-three_i) — suxobludov. Этот модуль — дополнение к ней.

Хранилище карты: периодически и после уборки скачивает карту из облака 3i."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import device_id_from_config
from .const import DOMAIN, MAP_REFRESH_INTERVAL, MAP_REFRESH_DELAY_AFTER_DOCK, SIGNAL_MAP_REFRESH
from .map import RobotMap, ThreeICloudMap, render

_LOGGER = logging.getLogger(__name__)


class ThreeIMapStore:
    """Последняя карта (PNG + описание) и подписчики на её обновление."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._cloud = ThreeICloudMap(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD],
                                     device_id_from_config(entry.data))
        self.png: bytes | None = None
        self.info: dict = {}
        self.rooms: list[dict] = []
        self.updated = None
        self.error: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._busy = False
        self._pending: CALLBACK_TYPE | None = None

    @callback
    def add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)

        @callback
        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)
        return _remove

    def room_name(self, room_id: int) -> str | None:
        return next((r["name"] for r in self.rooms if r["id"] == room_id), None)

    async def async_refresh(self, *_args) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            # Как приложение: просим робота выгрузить сохранённую карту, ждём, забираем.
            vacuum = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {}).get("vacuum")
            if vacuum is not None:
                await self.hass.async_add_executor_job(vacuum.request_map_upload)
                await asyncio.sleep(5)
            raw, index = await self.hass.async_add_executor_job(self._cloud.fetch_latest)
            robot_map = await self.hass.async_add_executor_job(RobotMap, raw)
            png, info = await self.hass.async_add_executor_job(render, robot_map)
            info["map_file"] = index
            if self._cloud.fallback_reason:
                info["map_file_note"] = self._cloud.fallback_reason
            self.png, self.info, self.rooms = png, info, info.get("rooms", [])
            self.updated = dt_util.utcnow()
            self.error = None
            _LOGGER.debug("3i: карта обновлена (%s байт, комнат: %s)", len(png), len(self.rooms))
        except Exception as err:  # noqa: BLE001 — карта не должна ронять интеграцию
            self.error = str(err)
            _LOGGER.warning("3i: не удалось обновить карту: %s", err)
        finally:
            self._busy = False
            for cb in list(self._listeners):
                cb()

    @callback
    def _on_dock(self) -> None:
        """Робот вернулся на базу — карта зальётся в облако не сразу, подождём."""
        if self._pending:
            self._pending()
        self._pending = async_call_later(self.hass, MAP_REFRESH_DELAY_AFTER_DOCK, self._delayed)

    async def _delayed(self, _now) -> None:
        self._pending = None
        await self.async_refresh()

    @callback
    def async_start(self) -> None:
        self._unsubs.append(async_track_time_interval(
            self.hass, self.async_refresh, timedelta(minutes=MAP_REFRESH_INTERVAL)))
        self._unsubs.append(async_dispatcher_connect(
            self.hass, SIGNAL_MAP_REFRESH.format(self.entry.entry_id), self._on_dock))
        self.entry.async_create_background_task(self.hass, self.async_refresh(), "three_i_map_first")

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._pending:
            self._pending()
            self._pending = None
