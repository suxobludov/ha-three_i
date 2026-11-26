"""Кнопки уборки отдельных комнат для 3i P10 Ultra.

Список комнат НЕ захардкожен: он берётся из настроек интеграции
(Настройки → Устройства → 3i → Настройки). ID комнат можно подсмотреть
в атрибуте room_ids ответа сервиса get_map_list / в дампе MQTT-карты.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    rooms = room_ids_for(entry)
    if not rooms:
        _LOGGER.info(
            "3i: комнаты не заданы — кнопки уборки по комнатам не созданы. "
            "Укажи их в настройках интеграции (Настройки → Устройства → 3i → Настройки)"
        )
        return
    async_add_entities(ThreeIRoomButton(entry, room_id) for room_id in rooms)


class ThreeIRoomButton(ButtonEntity):
    """Запуск уборки одной комнаты."""

    _attr_should_poll = False
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, entry: ConfigEntry, room_id: int) -> None:
        self._entry = entry
        self._room_id = room_id
        self._device_id = device_id_for(entry)

        self._attr_has_entity_name = True
        self._attr_name = f"Уборка комнаты {room_id}"
        self._attr_unique_id = f"{self._device_id}_room_{room_id}"
        self._attr_icon = ICONS[room_id % len(ICONS)]

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=self._device_id,
        )

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
