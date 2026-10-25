"""The privacy service: Dan's privacy settings, as the router and `/settings/privacy` see them.

Whether a run may use a cloud model given what its tools return is the router's call
(`agents/router.py`); the gateway model refuses a cloud call carrying private tool output as a
backstop (`models/gateway.py`). See docs/decisions/0026-privacy-routing.md.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from alfred_server.config import Settings, get_settings
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.privacy.models import PrivacySettings
from alfred_server.privacy.store import PrivacyStore

logger = logging.getLogger(__name__)


class PrivacyUnavailableError(RuntimeError):
    """The privacy settings couldn't be read or saved."""


class UnknownConnectorError(ValueError):
    """An override names a connector that doesn't exist."""


class PrivacyService:
    def __init__(self, store: PrivacyStore, registry: ConnectorRegistry) -> None:
        self.store = store
        self.registry = registry

    def settings(self) -> PrivacySettings:
        """The current settings. Raises `PrivacyUnavailableError` if they can't be read."""
        try:
            return self.store.load()
        except SQLAlchemyError as exc:
            raise PrivacyUnavailableError(f"privacy settings unavailable: {exc}") from exc

    def save_settings(self, settings: PrivacySettings) -> PrivacySettings:
        """Replace the settings. Raises `UnknownConnectorError` or `PrivacyUnavailableError`."""
        unknown = sorted(c for c in settings.connectors if self.registry.get(c) is None)
        if unknown:
            raise UnknownConnectorError(f"unknown connector(s): {', '.join(unknown)}")
        try:
            self.store.save(settings)
        except SQLAlchemyError as exc:
            raise PrivacyUnavailableError(f"privacy settings not saved: {exc}") from exc
        logger.info(
            "privacy settings saved: allow_private_to_cloud=%s, overrides=%s",
            settings.allow_private_to_cloud,
            settings.connectors,
        )
        return settings


def build_privacy_service(settings: Settings | None = None) -> PrivacyService:
    settings = settings or get_settings()
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    return PrivacyService(PrivacyStore(engine), default_registry())


@lru_cache
def get_privacy_service() -> PrivacyService:
    """The process-wide privacy service. Use as a FastAPI dependency (overridden in tests)."""
    return build_privacy_service()
