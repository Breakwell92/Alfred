"""`python -m alfred_server.confirmations`: list tiers, and relax an irreversible tool."""

import pytest

from alfred_server.confirmations.__main__ import main
from alfred_server.confirmations.models import Tier
from alfred_server.confirmations.service import ConfirmationService, IrreversibleRelaxError
from alfred_server.confirmations.store import ConfirmationStore
from alfred_server.connectors.registry import default_registry
from tests.budget_helpers import memory_engine


@pytest.fixture
def service() -> ConfirmationService:
    return ConfirmationService(ConfirmationStore(memory_engine()), default_registry())


def tier(service: ConfirmationService) -> str:
    return service.policy().tier("send_test_message")


def test_the_cli_relaxes_an_irreversible_tool(
    service: ConfirmationService, capsys: pytest.CaptureFixture[str]
) -> None:
    service.set_override("test_messages", "send_test_message", "irreversible")
    with pytest.raises(IrreversibleRelaxError):
        service.set_override("test_messages", "send_test_message", "read")

    assert main(["set", "test_messages", "send_test_message", "read"], service) == 0
    assert tier(service) == "read"

    service.set_override("test_messages", "send_test_message", "irreversible")
    assert main(["set", "test_messages", "send_test_message", "reset"], service) == 0
    assert tier(service) == "write"
    assert "reset" in capsys.readouterr().out


def test_the_cli_lists_declared_and_effective_tiers(
    service: ConfirmationService, capsys: pytest.CaptureFixture[str]
) -> None:
    service.set_override("test_messages", "send_test_message", "irreversible")

    assert main(["list"], service) == 0

    out = capsys.readouterr().out
    assert "test_messages/send_test_message  irreversible (override; default write)" in out
    assert "time/get_time  read" in out


def test_the_cli_rejects_unknown_tools(
    service: ConfirmationService, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["set", "nope", "x", "read"], service) == 1
    assert "No declared tool nope/x" in capsys.readouterr().err


def test_the_cli_reports_storage_failures(capsys: pytest.CaptureFixture[str]) -> None:
    class Broken(ConfirmationStore):
        def overrides(self) -> dict[tuple[str, str], Tier]:
            raise RuntimeError("postgres down")

    broken = ConfirmationService(Broken(memory_engine()), default_registry())
    assert main(["list"], broken) == 1
    assert "postgres down" in capsys.readouterr().err
