"""INSIIDA: quick setup for INSIIDA Matter devices in Home Assistant.

Adds diagnostic sensors (signal, network role, reboots, last boot) to INSIIDA
devices already added through the Matter integration, and runs the scene-key
actions chosen in this integration's options. It never writes to the devices.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    DOMAIN,
    LOGGER,
    MANUFACTURER_PREFIX,
    MATTER_DOMAIN,
    OPT_DOUBLE_TARGETS,
    OPT_KEYS,
    OPT_LONG_TARGETS,
    OPT_SINGLE_LIGHTS,
    PRESS_DOUBLE,
    PRESS_LONG,
    PRESS_SINGLE,
)
from .diagnostics_feed import DiagnosticsFeed
from .matter_link import find_insiida_devices, get_matter_client

PLATFORMS = [Platform.SENSOR]
RELOAD_DELAY_S = 30


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up INSIIDA from a config entry.

    The two features are independent on purpose. Scene keys only use public
    entity states, so they keep working even if a Home Assistant update changes
    the Matter integration's internals; diagnostics need the Matter client and
    are skipped (with one log line) when it cannot be reached.
    """
    matter_entries = hass.config_entries.async_entries(MATTER_DOMAIN)
    if not any(e.state is ConfigEntryState.LOADED for e in matter_entries):
        raise ConfigEntryNotReady("Matter integration is not ready")

    request_reload = _delayed_reloader(hass, entry)
    feeds: list[DiagnosticsFeed] = []
    client = get_matter_client(hass)
    if client is None:
        LOGGER.warning(
            "Matter client not found (Home Assistant may have changed the Matter "
            "integration); diagnostics are off, scene keys still work"
        )
    else:
        for dev in find_insiida_devices(hass):
            try:
                feed = DiagnosticsFeed(hass, client, dev, on_capability_change=request_reload)
                feed.start()
            except Exception as err:  # noqa: BLE001 - never block the scene keys
                LOGGER.warning("Diagnostics for %s are off: %r", dev.name, err)
                continue
            feeds.append(feed)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"feeds": feeds}

    _setup_scene_keys(hass, entry)
    _watch_new_devices(hass, entry, {feed.device.device_id for feed in feeds}, request_reload)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        for feed in data["feeds"]:
            feed.stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _delayed_reloader(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[], None]:
    """Return a function that reloads this entry once, RELOAD_DELAY_S later.

    Several triggers close together (a new device, new firmware features)
    lead to a single reload. The delay lets Matter finish its own updates.
    """
    pending: list[CALLBACK_TYPE] = []

    @callback
    def _reload(_now: Any) -> None:
        pending.clear()
        hass.config_entries.async_schedule_reload(entry.entry_id)

    @callback
    def request() -> None:
        if not pending:
            pending.append(async_call_later(hass, RELOAD_DELAY_S, _reload))

    @callback
    def _cancel() -> None:
        while pending:
            pending.pop()()

    entry.async_on_unload(_cancel)
    return request


def _watch_new_devices(
    hass: HomeAssistant, entry: ConfigEntry, known: set[str], request_reload: Callable[[], None]
) -> None:
    """Reload once an INSIIDA device is added to or removed from Matter.

    The customer does not have to re-add the integration: a new switch gets its
    diagnostics automatically.
    """

    @callback
    def _changed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        action, device_id = event.data["action"], event.data["device_id"]
        if action == "remove":
            relevant = device_id in known
        elif action == "create":
            device = dr.async_get(hass).async_get(device_id)
            relevant = device is not None and (device.manufacturer or "").upper().startswith(
                MANUFACTURER_PREFIX
            )
        else:
            return
        if relevant:
            request_reload()

    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _changed))


def _setup_scene_keys(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Run the chosen actions when a configured scene key is pressed."""
    keys: dict[str, dict[str, Any]] = entry.options.get(OPT_KEYS, {})
    if not keys:
        return

    @callback
    def handle(event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        if old is None or old.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return  # entity coming back after restart, not a press
        settings = keys.get(event.data["entity_id"], {})
        press = new.attributes.get("event_type")
        # Link the action to the key press so the logbook shows what caused it.
        context = Context(parent_id=new.context.id)
        if press == PRESS_SINGLE:
            hass.async_create_task(
                _toggle_group(hass, settings.get(OPT_SINGLE_LIGHTS, []), context)
            )
        elif press == PRESS_DOUBLE:
            hass.async_create_task(_turn_on(hass, settings.get(OPT_DOUBLE_TARGETS, []), context))
        elif press == PRESS_LONG:
            hass.async_create_task(_turn_on(hass, settings.get(OPT_LONG_TARGETS, []), context))

    entry.async_on_unload(async_track_state_change_event(hass, list(keys), handle))


async def _toggle_group(hass: HomeAssistant, entity_ids: list[str], context: Context) -> None:
    """Any on -> all off; all off -> all on. Keeps several lights in step."""
    if not entity_ids:
        return
    any_on = any(
        (state := hass.states.get(eid)) is not None and state.state == "on"
        for eid in entity_ids
    )
    service = "turn_off" if any_on else "turn_on"
    await _call(hass, service, entity_ids, context)


async def _turn_on(hass: HomeAssistant, entity_ids: list[str], context: Context) -> None:
    """Start scenes or scripts."""
    if entity_ids:
        await _call(hass, "turn_on", entity_ids, context)


async def _call(hass: HomeAssistant, service: str, entity_ids: list[str], context: Context) -> None:
    try:
        await hass.services.async_call(
            "homeassistant", service, {"entity_id": entity_ids}, blocking=True, context=context
        )
    except Exception as err:  # noqa: BLE001 - log and keep listening
        LOGGER.warning("INSIIDA scene key action failed: %s", err)
