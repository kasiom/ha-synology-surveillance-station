"""Config flow for Synology Surveillance Station."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    SynologyApiError,
    SynologyAuthError,
    SynologyConnectionError,
    SynologyPermissionError,
    SynologySurveillanceApi,
    SynologyUnsupportedError,
    normalize_host,
)
from .const import (
    CONF_CAMERA_IDS,
    CONF_POLL_INTERVAL,
    CONF_POLLING_ENABLED,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT_HTTPS,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)
from .models import Camera, SurveillanceInfo

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=values.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_PORT, default=values.get(CONF_PORT, DEFAULT_PORT_HTTPS)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_USE_SSL, default=values.get(CONF_USE_SSL, True)): bool,
            vol.Required(
                CONF_VERIFY_SSL, default=values.get(CONF_VERIFY_SSL, True)
            ): bool,
        }
    )


class SynologySurveillanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up and reconfigure a local Surveillance Station."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> SynologySurveillanceOptionsFlow:
        """Configure API event polling."""
        return SynologySurveillanceOptionsFlow()

    async def _async_validate(self, data: Mapping[str, Any]) -> SurveillanceInfo:
        client = SynologySurveillanceApi(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            use_ssl=data[CONF_USE_SSL],
            verify_ssl=data[CONF_VERIFY_SSL],
        )
        try:
            info, _cameras = await client.async_initialize()
            return info
        finally:
            await client.async_logout()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate the NAS and create an entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
                info = await self._async_validate(user_input)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except SynologyAuthError:
                errors["base"] = "invalid_auth"
            except SynologyPermissionError:
                errors["base"] = "insufficient_permissions"
            except SynologyUnsupportedError:
                errors["base"] = "unsupported"
            except SynologyConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected Surveillance Station validation error")
                errors["base"] = "unknown"
            else:
                unique_id = info.serial or (
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Surveillance Station ({user_input[CONF_HOST]})",
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    async def async_step_reauth(self, _entry_data: Mapping[str, Any]) -> FlowResult:
        """Start credential reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate and save a replacement password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            updated = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await self._async_validate(updated)
            except (SynologyAuthError, SynologyPermissionError):
                errors["base"] = "invalid_auth"
            except SynologyConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reconfigure connection details without creating a duplicate."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
                info = await self._async_validate(user_input)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except SynologyAuthError:
                errors["base"] = "invalid_auth"
            except SynologyPermissionError:
                errors["base"] = "insufficient_permissions"
            except SynologyUnsupportedError:
                errors["base"] = "unsupported"
            except SynologyConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    info.serial or f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                    reason="reconfigure_successful",
                )
        defaults = {**entry.data}
        defaults.pop(CONF_PASSWORD, None)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or defaults),
            errors=errors,
        )


class SynologySurveillanceOptionsFlow(config_entries.OptionsFlow):
    """Configure event polling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure recording event polling."""
        runtime = self.config_entry.runtime_data
        cameras = runtime.available_cameras
        try:
            cameras = await runtime.api.async_list_cameras()
        except SynologyApiError:
            _LOGGER.debug(
                "Could not refresh the camera list for the options flow",
                exc_info=True,
            )
        else:
            runtime.available_cameras = cameras
        available_ids = {str(camera.camera_id) for camera in cameras}
        if user_input is not None:
            camera_ids = [
                value
                for value in user_input[CONF_CAMERA_IDS]
                if value in available_ids
            ]
            if not camera_ids:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._options_schema(cameras, user_input),
                    errors={CONF_CAMERA_IDS: "select_camera"},
                )
            return self.async_create_entry(
                data={
                    CONF_POLLING_ENABLED: user_input[CONF_POLLING_ENABLED],
                    CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                    CONF_CAMERA_IDS: camera_ids,
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self._options_schema(cameras),
        )

    def _options_schema(
        self,
        cameras: tuple[Camera, ...],
        values: Mapping[str, Any] | None = None,
    ) -> vol.Schema:
        """Build options including only cameras visible to the account."""
        current = values or self.config_entry.options
        default_camera_ids = current.get(CONF_CAMERA_IDS) or [
            str(camera.camera_id) for camera in cameras
        ]
        return vol.Schema(
            {
                vol.Required(
                    CONF_CAMERA_IDS,
                    default=default_camera_ids,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=str(camera.camera_id), label=camera.name
                            )
                            for camera in cameras
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_POLLING_ENABLED,
                    default=current.get(CONF_POLLING_ENABLED, True),
                ): bool,
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                ),
            }
        )
