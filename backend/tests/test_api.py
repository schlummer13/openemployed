from fastapi.testclient import TestClient


def test_live(client: TestClient) -> None:
    assert client.get("/api/health/live").json() == {"status": "ok"}


def test_health_reports_unavailable_dependencies(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "checks": {"database": False, "ledger": False}}


def test_business_endpoints_require_login(client: TestClient) -> None:
    assert client.get("/api/tax-cases").status_code == 401
    assert client.get("/api/tax/gross-check", params={"gross": "1.00"}).status_code == 401
    r = client.post("/api/invoices/calculate", json={"lines": [{"description": "x", "quantity": "1", "unit_price": "1"}]})
    assert r.status_code == 401


def test_tax_cases_only_active_outgoing(auth_client: TestClient) -> None:
    codes = [x["code"] for x in auth_client.get("/api/tax-cases", params={"direction": "out"}).json()]
    assert codes == ["OUT_DE_19", "OUT_DE_7", "OUT_EU_B2B_SERVICE", "OUT_EU_B2B_GOODS", "OUT_DRITTLAND_SERVICE"]


def test_calculate_returns_decimal_strings(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/invoices/calculate",
        json={"lines": [{"description": "HR-Software", "quantity": "1", "unit_code": "MON", "unit_price": "1758.00"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total_net"], body["total_tax"], body["total_gross"]) == ("1758.00", "334.02", "2092.02")
    assert body["tax_breakdown"][0]["category"] == "S"


def test_calculate_rejects_float_amounts(auth_client: TestClient) -> None:
    r = auth_client.post("/api/invoices/calculate", json={"lines": [{"description": "x", "quantity": 1, "unit_price": 0.1}]})
    assert r.status_code == 422
    assert "Fließkommazahl" in r.text


def test_calculate_rejects_unknown_fields(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/invoices/calculate",
        json={"lines": [{"description": "x", "quantity": "1", "unit_price": "1", "total": "999"}]},
    )
    assert r.status_code == 422


def test_calculate_rejects_incoming_tax_case(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/invoices/calculate",
        json={"lines": [{"description": "x", "quantity": "1", "unit_price": "1", "tax_case": "IN_DE_19"}]},
    )
    assert r.status_code == 422


def test_gross_check(auth_client: TestClient) -> None:
    ok = auth_client.get("/api/tax/gross-check", params={"gross": "29.00"}).json()
    bad = auth_client.get("/api/tax/gross-check", params={"gross": "1.10"}).json()
    invalid = auth_client.get("/api/tax/gross-check", params={"gross": "1,10"})
    assert ok["representable"] is True and ok["net"] == "24.37"
    assert bad["representable"] is False and bad["nearest_lower_gross"] == "1.09"
    assert invalid.status_code == 422


def test_csrf_rejects_foreign_or_missing_origin(auth_client: TestClient) -> None:
    body = {"lines": [{"description": "x", "quantity": "1", "unit_price": "1"}]}
    foreign = auth_client.post("/api/invoices/calculate", json=body, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    no_origin = auth_client.post("/api/invoices/calculate", json=body, headers={"Origin": ""})
    assert no_origin.status_code == 403
    via_referer = auth_client.post(
        "/api/invoices/calculate", json=body, headers={"Origin": "", "Referer": "http://testserver/rechner"}
    )
    # leerer Origin-Header zählt als vorhanden und falsch → abgelehnt; nur fehlender Origin nutzt den Referer
    assert via_referer.status_code == 403
