"""Config flow для интеграции 3i Robot Vacuum."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import ThreeIAuthError, login
from .const import (
    CONF_DEVICE_ID,
    CONF_PRODUCT_ID,
    CONF_PROJECT,
    CONF_TENANT_ID,
    DEFAULT_NAME,
    DOMAIN,
    PRODUCT_ID,
    PROJECT,
    TENANT_ID,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_DEVICE_ID): str,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        # ID комнат с карты робота через запятую: "10,11,16"
        # Кнопки «Уборка: комната N» создаются по этому списку.
        vol.Optional("room_ids", default=""): str,
    }
)


def _parse_room_ids(raw: str) -> list[int]:
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


class ThreeIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Настройка интеграции: логин, пароль и серийный номер робота."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            device_id = user_input[CONF_DEVICE_ID].strip().upper()

            try:
                await self.hass.async_add_executor_job(
                    login, username, password, TENANT_ID, PROJECT
                )
            except ThreeIAuthError as err:
                _LOGGER.debug("3i: проверка учётных данных не прошла: %s", err)
                errors["base"] = (
                    "cannot_connect"
                    if "cloud_unreachable" in str(err)
                    else "invalid_auth"
                )
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{DEFAULT_NAME} ({device_id})",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_DEVICE_ID: device_id,
                        # Глобальные константы экосистемы 3i — сохраняем,
                        # чтобы старые записи не сломались, но в форме их нет.
                        CONF_TENANT_ID: TENANT_ID,
                        CONF_PRODUCT_ID: PRODUCT_ID,
                        CONF_PROJECT: PROJECT,
                    },
                    options={"room_ids": ""},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ThreeIOptionsFlow":
        return ThreeIOptionsFlow(config_entry)


class ThreeIOptionsFlow(config_entries.OptionsFlow):
    """Настройки после установки: список ID комнат для кнопок уборки."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="", data={"room_ids": _parse_room_ids(user_input.get("room_ids", ""))}
            )

        current = self._config_entry.options.get("room_ids", "")
        if isinstance(current, list):
            current = ",".join(str(x) for x in current)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Optional("room_ids", default=current): str}),
        )
