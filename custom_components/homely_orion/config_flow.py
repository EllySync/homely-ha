"""Set up Homely from the UI: e-mail and password, choose home, reauth."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HomelyAuthError, HomelyClient, HomelyError, HomelyRateLimitError
from .const import CONF_LOCATION_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

_USERNAME = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username"))
_PASSWORD = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


class HomelyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Homely Orion."""

    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._locations: dict[str, str] = {}

    async def _fetch_locations(
        self, username: str, password: str, errors: dict[str, str]
    ) -> list[dict[str, Any]] | None:
        """Log in and list homes. Fills errors and returns None on failure."""
        client = HomelyClient(async_get_clientsession(self.hass), username, password)
        try:
            return await client.get_locations()
        except HomelyAuthError:
            errors["base"] = "invalid_auth"
        except HomelyRateLimitError:
            errors["base"] = "rate_limited"
        except HomelyError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while logging in to Homely")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for e-mail and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            locations = await self._fetch_locations(username, password, errors)
            if locations is not None:
                self._locations = {
                    location["locationId"]: location.get("name") or location["locationId"]
                    for location in locations
                    if isinstance(location.get("locationId"), str)
                }
                if not self._locations:
                    return self.async_abort(reason="no_locations")
                self._username, self._password = username, password
                if len(self._locations) == 1:
                    return await self._create_entry(next(iter(self._locations)))
                return await self.async_step_location()

        schema = vol.Schema(
            {vol.Required(CONF_USERNAME): _USERNAME, vol.Required(CONF_PASSWORD): _PASSWORD}
        )
        suggested = {CONF_USERNAME: user_input[CONF_USERNAME]} if user_input else None
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_location(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a home when the account has several."""
        if user_input is not None:
            return await self._create_entry(user_input[CONF_LOCATION_ID])
        options = [
            SelectOptionDict(value=location_id, label=name)
            for location_id, name in self._locations.items()
        ]
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {vol.Required(CONF_LOCATION_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def _create_entry(self, location_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(location_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._locations.get(location_id, "Homely"),
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_LOCATION_ID: location_id,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth when Homely rejects the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        username = entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            locations = await self._fetch_locations(username, password, errors)
            if locations is not None:
                if entry.unique_id not in {location.get("locationId") for location in locations}:
                    return self.async_abort(reason="unique_id_mismatch")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"username": username},
            errors=errors,
        )
