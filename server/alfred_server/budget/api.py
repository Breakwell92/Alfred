"""Budget REST routes. Included in the token-protected router in `main.py`."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from alfred_server.budget.models import BudgetSettings
from alfred_server.budget.service import BudgetService, SpendSummary, get_budget_service
from alfred_server.budget.spend import SpendUnavailableError

router = APIRouter()

Service = Annotated[BudgetService, Depends(get_budget_service)]


@router.get("/settings/budget")
def get_budget_settings(service: Service) -> BudgetSettings:
    return service.get_settings()


@router.put("/settings/budget")
def put_budget_settings(settings: BudgetSettings, service: Service) -> BudgetSettings:
    """Replace the budget settings (cap, tiers, enabled providers, preferences)."""
    return service.save_settings(settings)


@router.get("/spend/summary")
def spend_summary(service: Service) -> SpendSummary:
    try:
        return service.summary()
    except SpendUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
