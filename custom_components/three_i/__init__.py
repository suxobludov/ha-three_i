"""Интеграция робота-пылесоса 3i (Picea / 3irobotix) в Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import ThreeIAuthError, device_id_from_config
from .const import CONF_DEVICE_ID, DOMAIN
from .mapstore import ThreeIMapStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["vacuum", "sensor", "button", "select", "image"]

__all__ = ["async_setup_entry", "async_unload_entry", "device_id_for", "room_ids_for"]


def device_id_for(entry: ConfigEntry) -> str:
    """Серийный номер робота из настроек записи."""
    return device_id_from_config(entry.data)


def room_ids_for(entry: ConfigEntry) -> list[int]:
    """ID комнат для кнопок уборки (настраивается в UI интеграции)."""
    raw = entry.options.get("room_ids") or entry.data.get("room_ids") or ""
    if isinstance(raw, list):
        return [int(x) for x in raw]
    result: list[int] = []
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    try:
        device_id_for(entry)
    except ThreeIAuthError as err:
        _LOGGER.error("3i: запись настроек некорректна: %s", err)
        return False

    store = ThreeIMapStore(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = {**entry.data, "map": store}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    store.async_start()
    entry.async_on_unload(store.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузить интеграцию после смены пароля или списка комнат."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
