"""DATEV-Export gegen echtes PostgreSQL."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
TODAY = today_berlin()
PERIOD = {"period_from": TODAY.replace(day=1).isoformat(), "period_to": TODAY.isoformat()}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


def rows(archive: bytes) -> tuple[list[list[str]], list[str]]:
    z = zipfile.ZipFile(io.BytesIO(archive))
    name = next(n for n in z.namelist() if n.startswith("EXTF_Buchungsstapel"))
    data = list(csv.reader(io.StringIO(z.read(name).decode("cp1252")), delimiter=";"))
    return data, z.namelist()


async def test_datev_export(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    assert (await api.post("/api/datev/export", json=PERIOD)).status_code == 422, "Berater-/Mandantennummer fehlen"
    settings = (await api.get("/api/datev/settings")).json()
    assert {k["tax_case"]: k["bu_key"] for k in settings["tax_keys"]}["IN_DE_19"] == "9"
    settings |= {"consultant_number": 1234567, "client_number": 10001}
    assert (await api.put("/api/datev/settings", json=settings)).status_code == 200
    skr03 = (await api.put("/api/datev/settings", json=settings | {"chart": "SKR03"})).json()
    assert skr03["bank_account"] == "1200" and {k["tax_case"]: k["account"] for k in skr03["tax_keys"]}["OUT_DE_19"] == "8400"
    settings = (await api.put("/api/datev/settings", json=skr03 | {"chart": "SKR04"})).json()
    assert settings["bank_account"] == "1800"

    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    await api.get(f"/api/invoices/{inv['id']}/pdf")  # Dokumente erzeugen
    await api.post(f"/api/invoices/{inv['id']}/payments", json={"paid_on": TODAY.isoformat(), "amount": "1000.00"})

    supplier = (await api.post("/api/suppliers", json={"name": "Hetzner Online GmbH", "vat_id": "DE812871812"})).json()
    up = (await api.post("/api/receipt-files", files={"file": ("h.png", PNG, "image/png")})).json()
    rec = (await api.post("/api/receipts", json={"supplier_id": supplier["id"], "file_id": up["id"], "receipt_date": TODAY.isoformat(),
                                                 "invoice_number": "R0001", "description": "Server", "category": "hosting",
                                                 "tax_case": "IN_DE_19", "net_amount": "100.00", "tax_amount": "19.00",
                                                 "paid_on": TODAY.isoformat()})).json()
    assert (await api.post(f"/api/receipts/{rec['id']}/book")).status_code == 200

    check = (await api.post("/api/datev/check", json=PERIOD)).json()
    assert check["bookings"] == 4 and check["problems"] == []

    preview = await api.post("/api/datev/export", json=PERIOD)
    assert preview.status_code == 200 and preview.headers["x-export-kind"] == "preview"
    assert (await api.get("/api/datev/exports")).json() == [], "Vorschau wird nicht gespeichert"

    final = await api.post("/api/datev/export", json=PERIOD | {"final": True})
    assert final.headers["x-export-kind"] == "final" and final.headers["x-bookings"] == "4"
    data, names = rows(final.content)
    header, columns, body = data[0], data[1], data[2:]
    assert header[:5] == ["EXTF", "700", "21", "Buchungsstapel", "13"] and header[10:12] == ["1234567", "10001"]
    assert columns[0] == "Umsatz (ohne Soll/Haben-Kz)" and len(body) == 4
    by_type = {r[10]: r for r in body}
    invoice_row = next(r for r in body if r[10] == inv["number"] and r[6] != "1800")
    assert invoice_row[:2] == ["2092,02", "S"] and invoice_row[7] == "4400" and invoice_row[8] == ""
    assert invoice_row[6] == inv["snapshot"]["buyer"]["number"].replace("K-", "")
    expense = by_type["R0001"]
    assert expense[6] in ("6495", "70001") and any(r[6] == "6495" and r[7] == "70001" and r[8] == "9" for r in body)
    assert any(r[6] == "1800" and r[0] == "1000,00" for r in body), "Zahlungseingang Bank an Debitor"
    assert any(n.startswith("Belege/RE-") and n.endswith(".xml") for n in names) and "Protokoll.txt" in names
    assert any(n.startswith("Belege/Eingang_R0001") for n in names)

    again = await api.post("/api/datev/export", json=PERIOD | {"final": True})
    assert again.status_code == 422 and "Keine neuen Buchungen" in again.json()["detail"]

    # Storno nach Export → Korrekturstapel nur mit der Stornobuchung
    await api.post(f"/api/invoices/{inv['id']}/cancel", json={"reason": "Fehler"})
    corr = await api.post("/api/datev/export", json=PERIOD | {"final": True})
    assert corr.headers["x-export-kind"] == "correction" and corr.headers["x-bookings"] == "1"
    cdata, _ = rows(corr.content)
    assert cdata[2][:2] == ["2092,02", "H"] and cdata[2][13].startswith("Storno")
    history = (await api.get("/api/datev/exports")).json()
    assert [h["kind"] for h in history] == ["correction", "final"]
    dl = await api.get(f"/api/datev/exports/{history[1]['id']}")
    assert dl.status_code == 200 and dl.content == final.content
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("DELETE FROM datev_exports"))


async def test_reverse_charge_requires_advisor_key(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    s = (await api.get("/api/datev/settings")).json() | {"consultant_number": 1234567, "client_number": 10001}
    await api.put("/api/datev/settings", json=s)
    supplier = (await api.post("/api/suppliers", json={"name": "Anthropic, PBC", "country_code": "US",
                                                       "default_tax_case": "IN_13B_DRITTLAND_SERVICE"})).json()
    up = (await api.post("/api/receipt-files", files={"file": ("a.png", PNG + b"2", "image/png")})).json()
    rec = (await api.post("/api/receipts", json={"supplier_id": supplier["id"], "file_id": up["id"], "receipt_date": TODAY.isoformat(),
                                                 "description": "API", "category": "software", "tax_case": "IN_13B_DRITTLAND_SERVICE",
                                                 "net_amount": "50.00", "tax_amount": "0.00"})).json()
    await api.post(f"/api/receipts/{rec['id']}/book")
    check = (await api.post("/api/datev/check", json=PERIOD)).json()
    assert check["problems"] and "BU-Schlüssel" in check["problems"][0]
    assert (await api.post("/api/datev/export", json=PERIOD | {"final": True})).status_code == 422
    s["tax_keys"] = [k | {"bu_key": "94"} if k["tax_case"] == "IN_13B_DRITTLAND_SERVICE" else k for k in s["tax_keys"]]
    await api.put("/api/datev/settings", json=s)
    final = await api.post("/api/datev/export", json=PERIOD | {"final": True})
    assert final.status_code == 200
    body = rows(final.content)[0][2:]
    assert body[0][:2] == ["50,00", "S"] and body[0][8] == "94", "Reverse Charge: Netto mit BU-Schlüssel"
