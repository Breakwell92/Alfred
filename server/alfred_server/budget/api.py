"""Budget REST routes. Included in the token-protected router in `main.py`."""

from typing import Annotated, get_args

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from alfred_server.budget.models import BudgetSettings, Provider
from alfred_server.budget.service import (
    BudgetService,
    SettingsUnavailableError,
    SpendSummary,
    get_budget_service,
)
from alfred_server.budget.spend import SpendUnavailableError

router = APIRouter()

Service = Annotated[BudgetService, Depends(get_budget_service)]


def _unset_fields(model: BaseModel, prefix: str = "") -> list[str]:
    """Fields of `model` (and nested models) that the request body left out."""
    missing = []
    for name in type(model).model_fields:
        if name not in model.model_fields_set:
            missing.append(prefix + name)
        elif isinstance(value := getattr(model, name), BaseModel):
            missing += _unset_fields(value, f"{prefix}{name}.")
    return missing


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.get("/settings/budget")
def get_budget_settings(service: Service) -> BudgetSettings:
    try:
        return service.get_settings()
    except SettingsUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.put("/settings/budget")
def put_budget_settings(settings: BudgetSettings, service: Service) -> BudgetSettings:
    """Replace the budget settings (cap, tiers, enabled providers, preferences).

    A full replacement, so every field must be given (422 otherwise): a partial body would
    silently reset what it leaves out. GET, edit, PUT.
    """
    missing = _unset_fields(settings)
    missing += [f"providers.{p}" for p in get_args(Provider) if p not in settings.providers]
    if missing:
        raise HTTPException(
            422,
            f"PUT replaces all budget settings; missing: {', '.join(missing)}",
        )
    return service.save_settings(settings)


@router.get("/spend/summary")
def spend_summary(service: Service) -> SpendSummary:
    try:
        return service.summary()
    except (SpendUnavailableError, SettingsUnavailableError) as exc:
        raise _unavailable(exc) from exc
