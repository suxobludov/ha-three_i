"""Кнопки 3i P10 Ultra: уборка отдельных комнат и обновление карты.

Комнаты берутся из карты робота (облако 3i): кнопки создаются сами, с названиями
из приложения. Если в настройках интеграции задан список ID комнат
(Настройки → Устройства → 3i → Настройки) — используется он.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_NAME, DOMAIN
from . import device_id_for, room_ids_for

_LOGGER = logging.getLogger(__name__)

ICONS = ["mdi:robot-vacuum", "mdi:bed-empty", "mdi:silverware-fork-knife",
         "mdi:sofa", "mdi:shower", "mdi:toilet", "mdi:washer", "mdi:home"]


def _find_vacuum_entity_id(hass: HomeAssistant, entry_id: str, device_id: str) -> str | None:
    ent_reg = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry_id):
        if entity_entry.domain == "vacuum":
            return entity_entry.entity_id

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, device_id)})
    if device:
        for entity_entry in er.async_entries_for_device(ent_reg, device.id):
            if entity_entry.domain == "vacuum":
                return entity_entry.entity_id

    for state in hass.states.async_all("vacuum"):
        if state.attributes.get("device_id") == device_id:
            return state.entity_id

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][entry.entry_id]["map"]
    async_add_entities([ThreeIMapRefreshButton(entry, store)])

    rooms = room_ids_for(entry)
    if rooms:
        async_add_entities(ThreeIRoomButton(entry, room_id, store) for room_id in rooms)
        return

    # Комнаты из карты: добавляем кнопки по мере того, как карта приходит из облака.
    known: set[int] = set()

    @callback
    def _sync_rooms() -> None:
        new = [r["id"] for r in store.rooms if r["id"] not in known]
        if new:
            known.update(new)
            async_add_entities(ThreeIRoomButton(entry, room_id, store) for room_id in new)

    entry.async_on_unload(store.add_listener(_sync_rooms))
    _sync_rooms()


class ThreeIMapRefreshButton(ButtonEntity):
    """Скачать карту из облака прямо сейчас."""

    _attr_has_entity_name = True
    _attr_name = "Обновить карту"
    _attr_icon = "mdi:map-search"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, store) -> None:
        self._store = store
        device_id = device_id_for(entry)
        self._attr_unique_id = f"{device_id}_map_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=device_id,
        )

    async def async_press(self) -> None:
        await self._store.async_refresh()


class ThreeIRoomButton(ButtonEntity):
    """Запуск уборки одной комнаты."""

    _attr_should_poll = False
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, entry: ConfigEntry, room_id: int, store=None) -> None:
        self._entry = entry
        self._room_id = room_id
        self._store = store
        self._device_id = device_id_for(entry)

        self._attr_has_entity_name = True
        self._attr_unique_id = f"{self._device_id}_room_{room_id}"
        self._attr_icon = ICONS[room_id % len(ICONS)]

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=self._device_id,
        )

    @property
    def name(self) -> str:
        title = self._store.room_name(self._room_id) if self._store else None
        return f"Уборка: {title}" if title else f"Уборка комнаты {self._room_id}"

    @property
    def extra_state_attributes(self) -> dict:
        return {"room_id": self._room_id}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._store:
            self.async_on_remove(self._store.add_listener(self.async_write_ha_state))

    async def async_press(self) -> None:
        vac_id = _find_vacuum_entity_id(self.hass, self._entry.entry_id, self._device_id)
        if not vac_id:
            _LOGGER.warning("3i: пылесос не найден, кнопка комнаты %s пропущена", self._room_id)
            return
        await self.hass.services.async_call(
            "vacuum",
            "send_command",
            {
                "entity_id": vac_id,
                "command": "clean_rooms",
                "params": {"room_ids": [self._room_id]},
            },
            blocking=True,
        )
