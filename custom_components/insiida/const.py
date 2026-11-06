"""Constants for the INSIIDA integration."""

from __future__ import annotations

import logging

DOMAIN = "insiida"
LOGGER = logging.getLogger(__package__)

MATTER_DOMAIN = "matter"
MANUFACTURER_PREFIX = "INSIIDA"

# Matter attribute paths: endpoint/cluster/attribute
PATH_REBOOT_COUNT = "0/51/1"  # General Diagnostics RebootCount
PATH_UPTIME = "0/51/2"  # General Diagnostics UpTime (seconds)
PATH_ROUTING_ROLE = "0/53/1"  # Thread Network Diagnostics RoutingRole
PATH_NEIGHBOR_TABLE = "0/53/7"  # Thread Network Diagnostics NeighborTable

PATH_EP0_SERVER_LIST = "0/29/1"  # Descriptor ServerList on endpoint 0
THREAD_DIAG_CLUSTER = 53

DIAG_PATHS = (PATH_REBOOT_COUNT, PATH_UPTIME, PATH_ROUTING_ROLE, PATH_NEIGHBOR_TABLE)
DIAG_REFRESH_MINUTES = 10

# Signal quality bands (best neighbour RSSI, dBm), strongest first.
SIGNAL_QUALITY_BANDS = ((-65, "excellent"), (-75, "good"), (-85, "fair"))
SIGNAL_QUALITY_POOR = "poor"

ROUTING_ROLES = {
    0: "unspecified",
    1: "unassigned",
    2: "sleepy_end_device",
    3: "end_device",
    4: "reed",
    5: "router",
    6: "leader",
}

# SW01 scene keys: Generic Switch endpoints. EP2 = upper rocker, EP3 = lower rocker.
SW01_MODEL = "SD-SW01"
SW01_KEY_ENDPOINTS = {2: "up", 3: "down"}

PRESS_SINGLE = "multi_press_1"
PRESS_DOUBLE = "multi_press_2"
PRESS_LONG = "long_press"

OPT_KEYS = "keys"
OPT_SINGLE_LIGHTS = "single_lights"
OPT_DOUBLE_TARGETS = "double_targets"
OPT_LONG_TARGETS = "long_targets"
