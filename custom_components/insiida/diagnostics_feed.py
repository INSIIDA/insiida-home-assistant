"""Keep the latest General / Thread Network Diagnostics values of one device.

Values arrive from the Matter client's attribute subscription and from a slow
periodic read. Read-only: nothing is written to the device.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DIAG_PATHS,
    DIAG_REFRESH_MINUTES,
    LOGGER,
    PATH_EP0_SERVER_LIST,
    THREAD_DIAG_CLUSTER,
)
from .matter_link import InsiidaDevice, read_cached


class DiagnosticsFeed:
    """Latest diagnostics values of one INSIIDA device."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: Any,
        device: InsiidaDevice,
        on_capability_change: Callable[[], None] | None = None,
    ) -> None:
        """Initialise from the Matter client cache.

        ``on_capability_change`` is called when the device starts or stops
        offering Thread Network Diagnostics (for example after a firmware
        update), so the integration can reload and add or drop its entities.
        """
        self.hass = hass
        self.client = client
        self.device = device
        self._on_capability_change = on_capability_change
        self.values: dict[str, Any] = {
            path: read_cached(client, device.node_id, path) for path in DIAG_PATHS
        }
        # When each value was last received from the device (cache values have none:
        # their age is unknown, which matters for UpTime).
        self.received_at: dict[str, datetime] = {}
        self.has_thread_diagnostics = self._read_has_thread_diagnostics()
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._warned = False

    @property
    def available(self) -> bool:
        """Whether the Matter node is currently reachable."""
        try:
            node = self.client.get_node(self.device.node_id)
        except (KeyError, AttributeError):
            return False
        return bool(getattr(node, "available", True)) if node is not None else False

    def start(self) -> None:
        """Subscribe to attribute updates and schedule the periodic read."""
        try:
            from matter_server.common.models import EventType  # noqa: PLC0415
        except ImportError:  # pragma: no cover - depends on HA version
            LOGGER.warning("Matter client events unavailable; only periodic reads are used")
        else:
            for path in DIAG_PATHS:
                self._unsubs.append(
                    self.client.subscribe_events(
                        callback=self._make_handler(path),
                        event_filter=EventType.ATTRIBUTE_UPDATED,
                        node_filter=self.device.node_id,
                        attr_path_filter=path,
                    )
                )
            # New firmware may add or remove clusters.
            self._unsubs.append(
                self.client.subscribe_events(
                    callback=self._handle_server_list,
                    event_filter=EventType.ATTRIBUTE_UPDATED,
                    node_filter=self.device.node_id,
                    attr_path_filter=PATH_EP0_SERVER_LIST,
                )
            )
            # Online / offline changes and re-interviews of the node.
            self._unsubs.append(
                self.client.subscribe_events(
                    callback=self._handle_node_updated,
                    event_filter=EventType.NODE_UPDATED,
                    node_filter=self.device.node_id,
                )
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_refresh, timedelta(minutes=DIAG_REFRESH_MINUTES)
            )
        )
        self.hass.async_create_task(self._async_refresh())

    def stop(self) -> None:
        """Remove all subscriptions."""
        while self._unsubs:
            unsub = self._unsubs.pop()
            try:
                unsub()
            except ValueError:  # already removed (e.g. Matter reloaded)
                pass

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register an entity update callback."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    def _make_handler(self, path: str) -> Callable[[Any, Any], None]:
        @callback
        def handle(event: Any, value: Any) -> None:
            self._set(path, value)

        return handle

    @callback
    def _handle_node_updated(self, event: Any, node: Any) -> None:
        self._check_capabilities()
        self._notify()

    @callback
    def _handle_server_list(self, event: Any, value: Any) -> None:
        self._check_capabilities()

    def _read_has_thread_diagnostics(self) -> bool:
        servers = read_cached(self.client, self.device.node_id, PATH_EP0_SERVER_LIST)
        return isinstance(servers, list) and THREAD_DIAG_CLUSTER in servers

    @callback
    def _check_capabilities(self) -> None:
        """Ask for a reload when Thread Network Diagnostics appears or disappears."""
        servers = read_cached(self.client, self.device.node_id, PATH_EP0_SERVER_LIST)
        if not isinstance(servers, list):
            return  # unknown right now (e.g. offline); keep the current entities
        has_thread = THREAD_DIAG_CLUSTER in servers
        if has_thread == self.has_thread_diagnostics:
            return
        LOGGER.info(
            "%s %s network diagnostics; reloading INSIIDA",
            self.device.name,
            "now offers" if has_thread else "no longer offers",
        )
        self.has_thread_diagnostics = has_thread
        if self._on_capability_change is not None:
            self._on_capability_change()

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def _set(self, path: str, value: Any) -> None:
        if value is None:
            return
        self.values[path] = value
        self.received_at[path] = dt_util.utcnow()
        self._notify()

    async def _async_refresh(self, _now: Any = None) -> None:
        """Read the diagnostics attributes from the device."""
        paths = [p for p in DIAG_PATHS if self.has_thread_diagnostics or not p.startswith("0/53/")]
        try:
            result = await self.client.read_attribute(self.device.node_id, paths)
        except Exception as err:  # noqa: BLE001 - device may be offline; keep old values
            self._warn_once("Reading diagnostics of %s failed: %r", self.device.name, err)
            return
        if not isinstance(result, dict):
            self._warn_once("Unexpected diagnostics read result for %s: %.200r", self.device.name, result)
            return
        for path, value in result.items():
            if path in self.values:
                self._set(path, value)

    def _warn_once(self, msg: str, *args: Any) -> None:
        if self._warned:
            LOGGER.debug(msg, *args)
            return
        self._warned = True
        LOGGER.warning(msg, *args)
