"""Find INSIIDA devices in the Matter integration and read their raw attributes.

Only uses what the Matter integration already exposes: the device registry, the
entity registry and the Matter client held by the Matter config entry. Nothing is
written to the devices.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    MANUFACTURER_PREFIX,
    MATTER_DOMAIN,
    SIGNAL_QUALITY_BANDS,
    SIGNAL_QUALITY_POOR,
    SW01_KEY_ENDPOINTS,
    SW01_MODEL,
)

_DEVICE_ID_RE = re.compile(r"^deviceid_([0-9A-Fa-f]{16})-([0-9A-Fa-f]{16})-MatterNodeDevice$")
_EVENT_UNIQUE_ID_RE = re.compile(r"-MatterNodeDevice-(\d+)-GenericSwitch-")


@dataclass(frozen=True)
class InsiidaDevice:
    """An INSIIDA device found in the Matter integration."""

    device_id: str  # HA device registry id
    matter_identifier: tuple[str, str]
    node_id: int
    name: str
    model: str | None


@dataclass(frozen=True)
class SceneKey:
    """A SW01 rocker that is set to scene mode (a Matter event entity)."""

    entity_id: str
    device: InsiidaDevice
    position: str  # "up", "down" or "ep<N>"


def get_matter_client(hass: HomeAssistant) -> Any | None:
    """Return the Matter client from the loaded Matter config entry, if any."""
    for entry in hass.config_entries.async_entries(MATTER_DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        data = getattr(entry, "runtime_data", None)
        adapter = getattr(data, "adapter", None)
        if adapter is not None:
            return adapter.matter_client
    # Older layout: hass.data["matter"][entry_id] -> MatterEntryData
    for data in (hass.data.get(MATTER_DOMAIN) or {}).values():
        adapter = getattr(data, "adapter", None)
        if adapter is not None:
            return adapter.matter_client
    return None


def find_insiida_devices(hass: HomeAssistant) -> list[InsiidaDevice]:
    """Return INSIIDA devices that the Matter integration has added."""
    devices: list[InsiidaDevice] = []
    for device in dr.async_get(hass).devices.values():
        if not (device.manufacturer or "").upper().startswith(MANUFACTURER_PREFIX):
            continue
        for domain, value in device.identifiers:
            if domain != MATTER_DOMAIN:
                continue
            if match := _DEVICE_ID_RE.match(value):
                devices.append(
                    InsiidaDevice(
                        device_id=device.id,
                        matter_identifier=(domain, value),
                        node_id=int(match.group(2), 16),
                        name=device.name_by_user or device.name or "INSIIDA",
                        model=device.model,
                    )
                )
                break
    return devices


def find_scene_keys(hass: HomeAssistant) -> list[SceneKey]:
    """Return the SW01 scene keys (Generic Switch event entities)."""
    ent_reg = er.async_get(hass)
    keys: list[SceneKey] = []
    for device in find_insiida_devices(hass):
        if device.model != SW01_MODEL:
            continue
        for entry in er.async_entries_for_device(ent_reg, device.device_id):
            if entry.platform != MATTER_DOMAIN or entry.domain != "event":
                continue
            match = _EVENT_UNIQUE_ID_RE.search(entry.unique_id)
            if not match:
                continue
            endpoint = int(match.group(1))
            keys.append(
                SceneKey(
                    entity_id=entry.entity_id,
                    device=device,
                    position=SW01_KEY_ENDPOINTS.get(endpoint, f"ep{endpoint}"),
                )
            )
    return keys


def read_cached(client: Any, node_id: int, path: str) -> Any:
    """Return the raw cached value of an attribute path, or None."""
    try:
        node = client.get_node(node_id)
    except (KeyError, AttributeError):
        return None
    if node is None:
        return None
    return node.node_data.attributes.get(path)


def _field(item: Any, field_id: int, name: str) -> Any:
    """Read a struct field from either raw dict form or a parsed object."""
    if isinstance(item, dict):
        for key in (str(field_id), field_id, name):
            if key in item:
                return item[key]
        return None
    return getattr(item, name, None)


def signal_quality(rssi: int) -> str:
    """Rate a signal strength in dBm for customers."""
    for floor, name in SIGNAL_QUALITY_BANDS:
        if rssi >= floor:
            return name
    return SIGNAL_QUALITY_POOR


def summarize_neighbors(table: Any) -> dict[str, Any] | None:
    """Summarise the Thread NeighborTable into plain numbers.

    Returns the strongest average RSSI (dBm) plus per-neighbour values.
    """
    if not isinstance(table, list):
        return None
    neighbors = []
    for item in table:
        avg = _field(item, 6, "averageRssi")
        last = _field(item, 7, "lastRssi")
        lqi = _field(item, 5, "lqi")
        is_child = _field(item, 13, "isChild")
        rssi = avg if isinstance(avg, int) and avg != 0 else last
        if not isinstance(rssi, int):
            continue
        neighbors.append({"rssi": rssi, "lqi": lqi, "is_child": bool(is_child)})
    if not neighbors:
        return None
    # Children (e.g. sleepy sensors under this router) are not our link to the mesh.
    links = [n for n in neighbors if not n["is_child"]] or neighbors
    return {
        "best_rssi": max(n["rssi"] for n in links),
        "neighbor_count": len(links),
        "neighbors": links,
    }
