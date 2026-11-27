"""Карта помещения как сущность-изображение (PNG с прозрачным фоном)."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import device_id_for
from .const import DEFAULT_NAME, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][entry.entry_id]["map"]
    async_add_entities([ThreeIMapImage(hass, entry, store)])


class ThreeIMapImage(ImageEntity):
    """Последняя карта робота из облака 3i."""

    _attr_has_entity_name = True
    _attr_name = "Карта"
    _attr_icon = "mdi:floor-plan"
    _attr_content_type = "image/png"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store) -> None:
        super().__init__(hass)
        self._store = store
        device_id = device_id_for(entry)
        self._attr_unique_id = f"{device_id}_map"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=device_id,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._store.add_listener(self._handle_update))
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        if self._store.updated:
            self._attr_image_last_updated = self._store.updated
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._store.png is not None

    async def async_image(self) -> bytes | None:
        return self._store.png

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(self._store.info)
        if self._store.error:
            attrs["last_error"] = self._store.error
        return attrs
