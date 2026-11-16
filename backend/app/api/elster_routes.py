"""ELSTER: Umsatzsteuer-Voranmeldung als Datensatz herunterladen, mit ERiC prüfen und übermitteln."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import elster as svc

router = APIRouter(prefix="/api/elster", tags=["elster"])


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pin: SecretStr = Field(min_length=4, max_length=64)


@router.get("/status")
async def status(_u: CurrentUser) -> Any:
    return svc.status()


@router.get("/ustva/{spec}")
async def overview(request: Request, spec: str, _u: CurrentUser) -> Any:
    return money_json({"status": svc.status(), "history": await run(request, lambda conn: svc.history(conn, tenant(), spec))})


@router.get("/ustva/{spec}/xml", response_class=Response)
async def download_xml(request: Request, spec: str, _u: CurrentUser) -> Response:
    xml, _ = await run(request, lambda conn: svc.build_xml(conn, tenant(), spec, test=True))
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="ustva_{spec}_test.xml"', "Cache-Control": "no-store"})


@router.post("/ustva/{spec}/validate")
async def validate(request: Request, spec: str, user: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: svc.validate(conn, tenant(), spec, user.email)))


@router.post("/ustva/{spec}/send")
async def send(request: Request, spec: str, data: SendIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    pin = data.pin.get_secret_value()
    return money_json(await run(request, lambda conn: svc.send(conn, ledger, tenant(), spec, pin, user.email)))
