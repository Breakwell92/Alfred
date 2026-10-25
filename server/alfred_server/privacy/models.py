"""Privacy settings: may `local_only` tool output go to a cloud model?"""

from __future__ import annotations

from pydantic import BaseModel, Field

from alfred_server.connectors.registry import ConnectorRegistry


class PrivacySettings(BaseModel):
    """Dan's privacy settings, persisted in Postgres and edited via `PUT /settings/privacy`.

    The defaults are the seed values used until the first save.
    """

    # Decided on STU-58: everything may go to cloud models by default. Turning this off keeps
    # every `local_only` tool's output on local models.
    allow_private_to_cloud: bool = True
    # Per-connector overrides by connector id: True allows that connector's `local_only` tools
    # to reach cloud models, False keeps them local, whatever the global setting says.
    connectors: dict[str, bool] = Field(default_factory=dict)

    def allows(self, connector_id: str) -> bool:
        return self.connectors.get(connector_id, self.allow_private_to_cloud)


# Used when the settings can't be read: private output stays local (fail closed).
FAIL_CLOSED = PrivacySettings(allow_private_to_cloud=False)


def blocked_tools(registry: ConnectorRegistry, settings: PrivacySettings) -> frozenset[str]:
    """Names of the `local_only` tools whose output must not reach a cloud model."""
    return frozenset(
        tool.name
        for connector in registry.all()
        if not settings.allows(connector.id)
        for tool in connector.tools
        if tool.privacy == "local_only"
    )
