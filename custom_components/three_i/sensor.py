"""Сенсоры 3i P10 Ultra."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONSUMABLE_MAX_HOURS,
    DEFAULT_NAME,
    DOMAIN,
    STATUS_TEXT,
)
from . import device_id_for


@dataclass(frozen=True)
class ThreeISensorDescription(SensorEntityDescription):
    """Описание сенсора 3i P10 Ultra на базе штатного SensorEntityDescription."""


SENSORS: tuple[ThreeISensorDescription, ...] = (
    ThreeISensorDescription(
        key="quantity",
        name="Батарея",
        icon="mdi:battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="station_status",
        name="Статус станции",
        icon="mdi:ev-station",
    ),
    ThreeISensorDescription(
        key="fault",
        name="Код уведомления",
        icon="mdi:alert-circle-outline",
    ),
    ThreeISensorDescription(
        key="cleaning_area",
        name="Площадь уборки",
        icon="mdi:texture-box",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="cleaning_time",
        name="Время уборки",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="wifi_rssi",
        name="Сигнал Wi-Fi",
        icon="mdi:wifi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="main_brush",
        name="Основная щётка",
        icon="mdi:brush",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="side_brush",
        name="Боковая щётка",
        icon="mdi:brush-variant",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="hypa",
        name="HEPA-фильтр",
        icon="mdi:air-filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="mop_life",
        name="Ресурс мопов",
        icon="mdi:water-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ThreeISensorDescription(
        key="firmware",
        name="Прошивка",
        icon="mdi:chip",
    ),
)


def _find_vacuum_entity_id(hass: HomeAssistant, entry_id: str, device_id: str) -> str | None:
    """Находит реальный entity_id пылесоса этой интеграции или устройства."""
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
    async_add_entities(ThreeISensor(entry, desc) for desc in SENSORS)


class ThreeISensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        description: ThreeISensorDescription,
    ) -> None:
        self._entry = entry
        self.entity_description = description
        self._key = description.key
        self._device_id = device_id_for(entry)
        self._tracked_vacuum_id: str | None = None
        self._unsub_track = None
        self._extra_attrs: dict[str, Any] = {}

        self._attr_has_entity_name = True
        self._attr_unique_id = f"{self._device_id}_{description.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=DEFAULT_NAME,
            manufacturer="3i",
            model="P10 Ultra",
            serial_number=self._device_id,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra_attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            if self._key in (
                "quantity",
                "cleaning_area",
                "cleaning_time",
                "wifi_rssi",
                "main_brush",
                "side_brush",
                "hypa",
                "mop_life",
            ):
                try:
                    self._attr_native_value = float(last.state)
                except ValueError:
                    pass
            else:
                self._attr_native_value = last.state

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
            self._update_from_attributes(current_vac.attributes)
            self.async_write_ha_state()

        @callback
        def _handle_state_change(event) -> None:
            new_state = event.data.get("new_state")
            if new_state is not None:
                self._update_from_attributes(new_state.attributes)
                self.async_write_ha_state()

        self._unsub_track = async_track_state_change_event(
            self.hass, [vac_id], _handle_state_change
        )
        self.async_on_remove(self._unsub_track)

    def _update_from_attributes(self, attrs: dict[str, Any]) -> None:
        if self._key == "cleaning_area":
            val = attrs.get("cleaning_area")
            if isinstance(val, (int, float)):
                # Робот передаёт площадь в 0.01 м² (521 -> 5.21 м²)
                self._attr_native_value = round(val / 100, 2) if val > 100 else val

        elif self._key in ("quantity", "fault", "cleaning_time", "firmware"):
            if self._key in attrs and attrs[self._key] is not None:
                self._attr_native_value = attrs[self._key]

        elif self._key in CONSUMABLE_MAX_HOURS:
            val = attrs.get(self._key)
            max_h = CONSUMABLE_MAX_HOURS[self._key]
            if isinstance(val, (int, float)):
                remaining_h = max(0, max_h - val)
                self._attr_native_value = int((remaining_h / max_h) * 100)
                self._extra_attrs = {
                    "Отработано часов": val,
                    "Осталось часов": remaining_h,
                    "Полный ресурс (ч)": max_h,
                }

        elif self._key == "station_status":
            if attrs.get("dust_action") == 1:
                self._attr_native_value = "Самоочистка пылесборника"
            elif attrs.get("back_to_wash") == 1:
                self._attr_native_value = "Промывка мопов"
            elif attrs.get("station_act") == 1:
                self._attr_native_value = "База активна (мойка / сушка)"
            elif attrs.get("charge_state") == 1:
                self._attr_native_value = "На базе, зарядка"
            elif "status" in attrs:
                st = attrs["status"]
                self._attr_native_value = STATUS_TEXT.get(st, f"Код {st}")

        elif self._key == "wifi_rssi":
            net = attrs.get("net_status")
            if isinstance(net, dict) and "rssi" in net:
                try:
                    self._attr_native_value = -abs(int(net["rssi"]))
                except (ValueError, TypeError):
                    pass