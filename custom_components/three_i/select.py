"""Селектор уровня подачи воды для 3i P10 Ultra."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DEFAULT_NAME, DOMAIN
from . import device_id_for

WATER_TO_NAME = {
    1: "Низкий",
    2: "Средний",
    3: "Высокий",
}
NAME_TO_WATER = {v: k for k, v in WATER_TO_NAME.items()}


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
    async_add_entities([ThreeIWaterLevelSelect(entry)])


class ThreeIWaterLevelSelect(SelectEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:water"
    _attr_options = ["Низкий", "Средний", "Высокий"]

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._device_id = device_id_for(entry)
        self._tracked_vacuum_id: str | None = None
        self._unsub_track = None
        self._attr_current_option: str | None = None

        self._attr_has_entity_name = True
        self._attr_name = "Уровень воды"
        self._attr_unique_id = f"{self._device_id}_water_level"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=self._device_id,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._bind_vacuum_listener()

        @callback
        def _on_registry_update(event) -> None:
            if not self._tracked_vacuum_id:
                self._bind_vacuum_listener()

        self.async_on_remove(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_update)
        )

    @callback
    def _bind_vacuum_listener(self) -> None:
        vac_id = _find_vacuum_entity_id(self.hass, self._entry.entry_id, self._device_id)
        if not vac_id or vac_id == self._tracked_vacuum_id:
            return

        self._tracked_vacuum_id = vac_id
        if self._unsub_track:
            self._unsub_track()

        current_vac = self.hass.states.get(vac_id)
        if current_vac is not None:
            self._sync_from_attrs(current_vac.attributes)
            self.async_write_ha_state()

        @callback
        def _handle_state_change(event) -> None:
            new_state = event.data.get("new_state")
            if new_state is not None:
                self._sync_from_attrs(new_state.attributes)
                self.async_write_ha_state()

        self._unsub_track = async_track_state_change_event(
            self.hass, [vac_id], _handle_state_change
        )
        self.async_on_remove(self._unsub_track)

    def _sync_from_attrs(self, attrs: dict) -> None:
        raw = attrs.get("water")
        if isinstance(raw, int) and raw in WATER_TO_NAME:
            self._attr_current_option = WATER_TO_NAME[raw]

    async def async_select_option(self, option: str) -> None:
        vac_id = _find_vacuum_entity_id(self.hass, self._entry.entry_id, self._device_id)
        val = NAME_TO_WATER.get(option)
        if not vac_id or val is None:
            return
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.hass.services.async_call(
            "vacuum",
            "send_command",
            {
                "entity_id": vac_id,
                "command": "set_water_level",
                "params": {"water": val},
            },
            blocking=True,
        )