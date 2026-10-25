"""Routine routes. Included in the token-protected router in `main.py`.

`GET /routines`, `POST /routines`, `GET /routines/{id}`, `PUT /routines/{id}`,
`DELETE /routines/{id}`, `POST /routines/{id}/run`.

`approved_tools` lets a routine run gated (`write` tier) tools without asking, the same trust
as `PUT /settings/permissions` relaxing a `write` tool. `irreversible` tools can never be in a
routine (docs/decisions/0021-routines.md).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from alfred_server.chat import routine_service_dependency
from alfred_server.confirmations.service import ConfirmationsUnavailableError
from alfred_server.routines.models import Routine, RoutineResult, RoutineSpec
from alfred_server.routines.service import RoutineNotFoundError, RoutineService
from alfred_server.routines.validation import RoutineInvalidError

logger = logging.getLogger(__name__)

router = APIRouter()

Service = Annotated[RoutineService, Depends(routine_service_dependency)]


def _unavailable(exc: Exception) -> HTTPException:
    logger.error("routines unavailable: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Routines are unavailable")


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "No such routine")


@router.get("/routines")
async def list_routines(service: Service) -> list[Routine]:
    try:
        return await asyncio.to_thread(service.all)
    except Exception as exc:
        raise _unavailable(exc) from exc


@router.post("/routines", status_code=status.HTTP_201_CREATED)
async def create_routine(spec: RoutineSpec, service: Service) -> Routine:
    try:
        return await asyncio.to_thread(service.create, spec)
    except RoutineInvalidError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:
        raise _unavailable(exc) from exc


@router.get("/routines/{routine_id}")
async def get_routine(routine_id: str, service: Service) -> Routine:
    try:
        return await asyncio.to_thread(service.get, routine_id)
    except RoutineNotFoundError as exc:
        raise _not_found() from exc
    except Exception as exc:
        raise _unavailable(exc) from exc


@router.put("/routines/{routine_id}")
async def update_routine(routine_id: str, spec: RoutineSpec, service: Service) -> Routine:
    try:
        return await asyncio.to_thread(service.update, routine_id, spec)
    except RoutineNotFoundError as exc:
        raise _not_found() from exc
    except RoutineInvalidError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:
        raise _unavailable(exc) from exc


@router.delete("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routine(routine_id: str, service: Service) -> Response:
    try:
        await asyncio.to_thread(service.delete, routine_id)
    except RoutineNotFoundError as exc:
        raise _not_found() from exc
    except Exception as exc:
        raise _unavailable(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/routines/{routine_id}/run")
async def run_routine(routine_id: str, service: Service) -> RoutineResult:
    """Run a routine now (the app's Run button), whatever its trigger phrases."""
    try:
        return await service.run_by_id(routine_id, "api")
    except RoutineNotFoundError as exc:
        raise _not_found() from exc
    except ConfirmationsUnavailableError as exc:
        raise _unavailable(exc) from exc
