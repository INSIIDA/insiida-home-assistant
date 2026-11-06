"""Diagnostic sensors for INSIIDA devices: signal, network role, reboots, last boot."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PATH_NEIGHBOR_TABLE,
    PATH_REBOOT_COUNT,
    PATH_ROUTING_ROLE,
    PATH_UPTIME,
    ROUTING_ROLES,
    SIGNAL_QUALITY_BANDS,
    SIGNAL_QUALITY_POOR,
)
from .diagnostics_feed import DiagnosticsFeed
from .matter_link import signal_quality, summarize_neighbors


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create diagnostic sensors for every INSIIDA device found at setup."""
    feeds: list[DiagnosticsFeed] = hass.data[DOMAIN][entry.entry_id]["feeds"]
    entities: list[SensorEntity] = []
    for feed in feeds:
        entities += [RebootSensor(feed), LastBootSensor(feed)]
        # Signal and role need the Thread Network Diagnostics cluster (0x0035),
        # which not every firmware enables.
        if feed.has_thread_diagnostics:
            entities += [SignalQualitySensor(feed), SignalSensor(feed), RoleSensor(feed)]
    async_add_entities(entities)


class InsiidaDiagSensor(SensorEntity):
    """Base class: one value derived from the device's diagnostics feed."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    key: str

    def __init__(self, feed: DiagnosticsFeed) -> None:
        """Attach to the existing Matter device in the device registry."""
        self.feed = feed
        self._attr_translation_key = self.key
        self._attr_unique_id = f"{feed.device.matter_identifier[1]}-{self.key}"
        self._attr_device_info = DeviceInfo(identifiers={feed.device.matter_identifier})

    async def async_added_to_hass(self) -> None:
        """Listen for new values."""
        self.async_on_remove(self.feed.add_listener(self._handle_update))

    @property
    def available(self) -> bool:
        """Unavailable while the device is offline, like its Matter entities."""
        return self.feed.available

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    def _value(self, path: str) -> Any:
        return self.feed.values.get(path)


class SignalSensor(InsiidaDiagSensor):
    """Strongest Thread link (average RSSI of the best neighbour)."""

    key = "signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        summary = summarize_neighbors(self._value(PATH_NEIGHBOR_TABLE))
        return summary["best_rssi"] if summary else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        summary = summarize_neighbors(self._value(PATH_NEIGHBOR_TABLE))
        if not summary:
            return None
        return {
            "neighbor_count": summary["neighbor_count"],
            "neighbor_rssi": [n["rssi"] for n in summary["neighbors"]],
            "neighbor_lqi": [n["lqi"] for n in summary["neighbors"]],
        }


class SignalQualitySensor(InsiidaDiagSensor):
    """Plain-language signal rating for customers: excellent / good / fair / poor."""

    key = "signal_quality"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [name for _, name in SIGNAL_QUALITY_BANDS] + [SIGNAL_QUALITY_POOR]

    @property
    def native_value(self) -> str | None:
        summary = summarize_neighbors(self._value(PATH_NEIGHBOR_TABLE))
        return signal_quality(summary["best_rssi"]) if summary else None


class RoleSensor(InsiidaDiagSensor):
    """Thread routing role (router, end device, ...)."""

    key = "network_role"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ROUTING_ROLES.values())

    @property
    def native_value(self) -> str | None:
        value = self._value(PATH_ROUTING_ROLE)
        return ROUTING_ROLES.get(value) if isinstance(value, int) else None


class RebootSensor(InsiidaDiagSensor):
    """How many times the device has restarted."""

    key = "reboot_count"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int | None:
        value = self._value(PATH_REBOOT_COUNT)
        return value if isinstance(value, int) else None


class LastBootSensor(InsiidaDiagSensor):
    """When the device last started, derived from UpTime."""

    key = "last_boot"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _last: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        uptime = self._value(PATH_UPTIME)
        received = self.feed.received_at.get(PATH_UPTIME)
        # A cached UpTime has unknown age (the device does not report it as it
        # ticks), so only a value just received from the device is usable.
        if not isinstance(uptime, int) or received is None:
            return self._last
        boot = (received - timedelta(seconds=uptime)).replace(microsecond=0)
        # UpTime ticks every second; only move the timestamp on a real change
        # (a reboot) so the history is not flooded.
        if self._last is None or abs((boot - self._last).total_seconds()) > 120:
            self._last = boot
        return self._last
