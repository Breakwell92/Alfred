"""Connectors that ship with Alfred. Add new ones to `BUILTIN_CONNECTORS`."""

from alfred_server.connectors.builtin.clock import TIME_CONNECTOR
from alfred_server.connectors.builtin.spotify import SPOTIFY_CONNECTOR
from alfred_server.connectors.builtin.test_messages import TEST_MESSAGES_CONNECTOR
from alfred_server.connectors.models import Connector

BUILTIN_CONNECTORS: tuple[Connector, ...] = (
    TIME_CONNECTOR,
    TEST_MESSAGES_CONNECTOR,
    SPOTIFY_CONNECTOR,
)
