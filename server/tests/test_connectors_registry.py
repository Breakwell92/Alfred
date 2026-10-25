from dataclasses import replace

import pytest

from alfred_server.connectors.models import ToolMeta
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from tests.connector_helpers import API_KEY_CONNECTOR, OAUTH_CONNECTOR, build_registry


def test_default_registry_has_time_connector() -> None:
    registry = default_registry()
    time = registry.get("time")

    assert time is not None
    assert time.auth_type == "none"
    assert time.specialist == "general"
    assert [c.id for c in registry.for_specialist("general")] == ["time", "test_messages"]


def test_tool_meta_exposes_risk_and_privacy() -> None:
    registry = build_registry()

    found = registry.tool_meta("acme_read")
    assert found is not None
    connector, meta = found
    assert connector.id == "acme"
    assert (meta.risk, meta.privacy) == ("read", "local_only")

    delete = registry.tool_meta("acme_delete")
    assert delete is not None and delete[1].risk == "irreversible"
    assert delete[1].privacy == "cloud_ok"
    assert registry.tool_meta("nope") is None


def test_duplicate_connector_rejected() -> None:
    registry = ConnectorRegistry([API_KEY_CONNECTOR])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(API_KEY_CONNECTOR)


def test_duplicate_tool_name_across_connectors_rejected() -> None:
    clash = replace(API_KEY_CONNECTOR, id="other")
    with pytest.raises(ValueError, match="already declared"):
        ConnectorRegistry([API_KEY_CONNECTOR, clash])


@pytest.mark.parametrize(
    "bad",
    [
        replace(OAUTH_CONNECTOR, oauth=None),
        replace(API_KEY_CONNECTOR, auth_type="oauth2"),
        replace(API_KEY_CONNECTOR, tools=()),
        replace(API_KEY_CONNECTOR, id="Bad Id"),
        replace(
            API_KEY_CONNECTOR,
            tools=(
                ToolMeta(name="t", description="", risk="read"),
                ToolMeta(name="t", description="", risk="write"),
            ),
        ),
    ],
)
def test_inconsistent_definitions_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        ConnectorRegistry([bad])  # type: ignore[list-item]
