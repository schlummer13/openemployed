from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError

from app.api.schemas import CalculateRequest, CalculateResponse, GrossCheckOut, TaxCaseOut
from app.auth.deps import CurrentUser
from app.core.money import StrictDecimal, round_cent
from app.db.session import ping_database
from app.domain.invoice_calc import InvoiceCalculationError, LineInput, calculate_invoice, net_from_gross
from app.domain.tax_cases import Direction, UnknownTaxCaseError, active_cases, get_tax_case
from app.services.ledger_client import LedgerClient

router = APIRouter(prefix="/api")

_STRICT_DECIMAL: TypeAdapter[Decimal] = TypeAdapter(StrictDecimal)


@router.get("/health/live", tags=["system"])
async def live() -> dict[str, str]:
    """Prozess läuft (für Docker-Healthcheck, ohne Abhängigkeiten)."""
    return {"status": "ok"}


@router.get("/health", tags=["system"])
async def health(request: Request) -> JSONResponse:
    """Bereitschaft inkl. Datenbank und Festschreibungsdienst."""
    ledger: LedgerClient = request.app.state.ledger

    async def db_ok() -> bool:
        try:
            await asyncio.wait_for(ping_database(), timeout=3)
        except Exception:  # jeder Fehler bedeutet „nicht bereit“
            return False
        return True

    database, ledger_ok = await asyncio.gather(db_ok(), ledger.health())
    checks = {"database": database, "ledger": ledger_ok}
    ok = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )


@router.get("/tax-cases", response_model=list[TaxCaseOut], tags=["steuern"])
async def list_tax_cases(_user: CurrentUser, direction: Direction | None = None) -> list[dict[str, Any]]:
    return [
        {**asdict(c), "direction": c.direction.value, "category": c.category.value}
        for c in active_cases(direction)
    ]


@router.get("/tax/gross-check", response_model=GrossCheckOut, tags=["steuern"])
async def gross_check(
    _user: CurrentUser,
    gross: Annotated[str, Query(description="Bruttobetrag, z. B. 29.00", max_length=20)],
    tax_case: Annotated[str, Query(max_length=40)] = "OUT_DE_19",
) -> dict[str, Any]:
    """Prüft, ob ein Bruttopreis (B2C) bei diesem Steuersatz exakt darstellbar ist."""
    try:
        amount = _STRICT_DECIMAL.validate_python(gross)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
    try:
        case = get_tax_case(tax_case)
    except UnknownTaxCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    target = round_cent(amount)
    net, tax = net_from_gross(target, case.rate)
    return {
        "gross": target,
        "rate": case.rate,
        "representable": net + tax == target,
        "net": net,
        "tax": tax,
        "nearest_lower_gross": net + tax,
    }


@router.post("/invoices/calculate", response_model=CalculateResponse, tags=["rechnungen"])
async def calculate(req: CalculateRequest, _user: CurrentUser) -> dict[str, Any]:
    """Berechnet Positionen, Steueraufschlüsselung und Summen nach EN 16931 (ohne zu speichern)."""
    try:
        totals = calculate_invoice(
            [
                LineInput(
                    description=line.description,
                    quantity=line.quantity,
                    unit_code=line.unit_code,
                    unit_price=line.unit_price,
                    tax_case=line.tax_case,
                )
                for line in req.lines
            ],
            req.price_mode,
        )
    except (InvoiceCalculationError, UnknownTaxCaseError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = asdict(totals)
    result["lines"] = [{**line, "category": line["category"].value} for line in result["lines"]]
    result["tax_breakdown"] = [
        {**b, "category": b["category"].value, "tax_cases": list(b["tax_cases"])}
        for b in result["tax_breakdown"]
    ]
    result["warnings"] = list(result["warnings"])
    return result
