"""“下载诊断信息” support: a support file a customer can send to INSIIDA.

Contains only what the plugin itself knows (settings, found devices, the
diagnostics values it shows). No pairing codes, keys or network credentials are
read by this integration, so none can end up in the file.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    PATH_NEIGHBOR_TABLE,
    PATH_REBOOT_COUNT,
    PATH_ROUTING_ROLE,
    PATH_UPTIME,
    ROUTING_ROLES,
)
from .diagnostics_feed import DiagnosticsFeed
from .matter_link import find_scene_keys, signal_quality, summarize_neighbors


def _feed_summary(feed: DiagnosticsFeed) -> dict[str, Any]:
    neighbors = summarize_neighbors(feed.values.get(PATH_NEIGHBOR_TABLE))
    role = feed.values.get(PATH_ROUTING_ROLE)
    return {
        "name": feed.device.name,
        "model": feed.device.model,
        "available": feed.available,
        "thread_diagnostics": feed.has_thread_diagnostics,
        "reboot_count": feed.values.get(PATH_REBOOT_COUNT),
        "uptime_s": feed.values.get(PATH_UPTIME),
        "network_role": ROUTING_ROLES.get(role) if isinstance(role, int) else None,
        "signal_dbm": neighbors["best_rssi"] if neighbors else None,
        "signal_quality": signal_quality(neighbors["best_rssi"]) if neighbors else None,
        "neighbors": neighbors["neighbors"] if neighbors else [],
        "last_received": {
            path: when.isoformat() for path, when in feed.received_at.items()
        },
    }


def _feeds(hass: HomeAssistant, entry: ConfigEntry) -> list[DiagnosticsFeed]:
    return hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("feeds", [])


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Whole-plugin support file."""
    return {
        "generated_at": dt_util.utcnow().isoformat(),
        "options": dict(entry.options),
        "scene_keys": [
            {"entity_id": k.entity_id, "device": k.device.name, "position": k.position}
            for k in find_scene_keys(hass)
        ],
        "devices": [_feed_summary(feed) for feed in _feeds(hass, entry)],
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Support file for one device."""
    for feed in _feeds(hass, entry):
        if feed.device.device_id == device.id:
            return {"generated_at": dt_util.utcnow().isoformat(), **_feed_summary(feed)}
    return {"generated_at": dt_util.utcnow().isoformat(), "note": "no diagnostics for this device"}
