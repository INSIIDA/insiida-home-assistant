"""Setup wizard for INSIIDA: one entry, then per-key choices in the options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    MATTER_DOMAIN,
    OPT_DOUBLE_TARGETS,
    OPT_KEYS,
    OPT_LONG_TARGETS,
    OPT_SINGLE_LIGHTS,
)
from .matter_link import find_insiida_devices, find_scene_keys

CONF_KEY = "key"

# Labels built in code (device names are dynamic, so these cannot live in the
# translation files). Chinese for zh*, English otherwise.
LABELS = {
    "zh": {
        "up": "上键", "down": "下键", "unset": "未设置", "set": "已设置",
        OPT_SINGLE_LIGHTS: "单击", OPT_DOUBLE_TARGETS: "双击", OPT_LONG_TARGETS: "长按",
        "sep": "、",
    },
    "en": {
        "up": "upper key", "down": "lower key", "unset": "not set", "set": "set",
        OPT_SINGLE_LIGHTS: "single", OPT_DOUBLE_TARGETS: "double", OPT_LONG_TARGETS: "long",
        "sep": ", ",
    },
}


class InsiidaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add the INSIIDA integration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Check that Matter is running and INSIIDA devices exist."""
        if not any(
            e.state is ConfigEntryState.LOADED
            for e in self.hass.config_entries.async_entries(MATTER_DOMAIN)
        ):
            return self.async_abort(reason="matter_not_ready")
        devices = find_insiida_devices(self.hass)
        if not devices:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            return self.async_create_entry(title="INSIIDA", data={})
        return self.async_show_form(
            step_id="user",
            description_placeholders={"devices": "、".join(d.name for d in devices)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return InsiidaOptionsFlow()


class InsiidaOptionsFlow(OptionsFlow):
    """Choose what each SW01 scene key does."""

    def __init__(self) -> None:
        """Start with no key chosen."""
        self._key: str | None = None

    def _labels(self) -> dict[str, str]:
        return LABELS["zh" if (self.hass.config.language or "").startswith("zh") else "en"]

    def _key_label(self, entity_id: str, with_status: bool = False) -> str:
        t = self._labels()
        for key in find_scene_keys(self.hass):
            if key.entity_id != entity_id:
                continue
            label = f"{key.device.name} {t.get(key.position, key.position)}"
            if not with_status:
                return label
            settings = self.config_entry.options.get(OPT_KEYS, {}).get(entity_id, {})
            used = [t[opt] for opt in (OPT_SINGLE_LIGHTS, OPT_DOUBLE_TARGETS, OPT_LONG_TARGETS)
                    if settings.get(opt)]
            status = f"{t['set']}: {t['sep'].join(used)}" if used else t["unset"]
            return f"{label} ({status})"
        return entity_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a scene key."""
        keys = find_scene_keys(self.hass)
        if not keys:
            return self.async_abort(reason="no_scene_keys")
        if user_input is not None:
            self._key = user_input[CONF_KEY]
            return await self.async_step_key()
        options = [
            selector.SelectOptionDict(
                value=k.entity_id, label=self._key_label(k.entity_id, with_status=True)
            )
            for k in keys
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KEY): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_key(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose single / double / long press actions for one key."""
        assert self._key is not None
        all_keys: dict[str, dict[str, Any]] = dict(self.config_entry.options.get(OPT_KEYS, {}))
        if user_input is not None:
            settings = {
                OPT_SINGLE_LIGHTS: user_input.get(OPT_SINGLE_LIGHTS, []),
                OPT_DOUBLE_TARGETS: user_input.get(OPT_DOUBLE_TARGETS, []),
                OPT_LONG_TARGETS: user_input.get(OPT_LONG_TARGETS, []),
            }
            if any(settings.values()):
                all_keys[self._key] = settings
            else:
                all_keys.pop(self._key, None)
            return self.async_create_entry(data={**self.config_entry.options, OPT_KEYS: all_keys})

        current = all_keys.get(self._key, {})
        lights = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["light", "switch"], multiple=True)
        )
        scenes = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["scene", "script"], multiple=True)
        )
        return self.async_show_form(
            step_id="key",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        OPT_SINGLE_LIGHTS,
                        description={"suggested_value": current.get(OPT_SINGLE_LIGHTS)},
                    ): lights,
                    vol.Optional(
                        OPT_DOUBLE_TARGETS,
                        description={"suggested_value": current.get(OPT_DOUBLE_TARGETS)},
                    ): scenes,
                    vol.Optional(
                        OPT_LONG_TARGETS,
                        description={"suggested_value": current.get(OPT_LONG_TARGETS)},
                    ): scenes,
                }
            ),
            description_placeholders={"key": self._key_label(self._key)},
        )
