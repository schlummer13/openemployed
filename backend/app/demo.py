"""Demodaten für den lokalen Testbetrieb – nie in Produktion.

Start:  docker compose exec backend python -m app.demo

Legt über die normale API (Entwickler-Anmeldung) realistische Daten an: Firmenprofil, vollständige
Gesellschafter-Geschäftsführer mit Lohnsteuerdaten, Kunden, Produkte, Verträge, Angebote, Rechnungen mit Zahlungen,
Lieferanten und gebuchte Belege über mehrere Monate, Budgets, Projekte mit Aufgaben und einen Kontostand.
Mehrfaches Ausführen ist unbedenklich: Vorhandenes (gleicher Name) wird übersprungen. Alle Mails gehen im Testmodus
an Mailpit (http://localhost:8025), nichts verlässt den Rechner.
"""

from __future__ import annotations

import datetime as dt
import sys
import zlib
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.invoices import today_berlin

BASE = "http://127.0.0.1:8000"
TODAY = today_berlin()
YEAR = TODAY.year


def month_start(offset: int) -> dt.date:
    """Erster Tag des Monats `offset` Monate vor dem aktuellen."""
    m = TODAY.month - 1 - offset
    return dt.date(YEAR + m // 12, m % 12 + 1, 1)


def pdf(text: str) -> bytes:
    """Kleines, gültiges PDF mit einer Textzeile (Platzhalter für einen eingescannten Beleg)."""
    content = f"BT /F1 14 Tf 72 760 Td ({text}) Tj ET".encode("latin-1", "replace")
    stream = zlib.compress(content)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


class Demo:
    def __init__(self) -> None:
        s = get_settings()
        self.api = httpx.Client(base_url=BASE, headers={"Origin": s.app_origin}, timeout=60)
        r = self.api.post("/api/auth/dev-login", json={"email": "anna@selbstlite.test"})
        r.raise_for_status()
        self.log: list[str] = []

    def call(self, method: str, url: str, ok: tuple[int, ...] = (200, 201, 204), **kw: Any) -> Any:
        r = self.api.request(method, url, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {url} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print("  ✓", msg)

    # ─────────────────────────── Stammdaten ───────────────────────────
    def company(self) -> None:
        cur = self.api.get("/api/company-profile")
        if cur.status_code == 200 and cur.json() and not cur.json().get("missing_for_invoices"):
            return
        self.call("POST", "/api/company-profile", ok=(201, 409), json={
            "valid_from": f"{YEAR}-01-01", "legal_name": "Beispiel Software UG (haftungsbeschränkt)",
            "legal_form": "UG (haftungsbeschränkt)", "street": "Königsallee 1", "postal_code": "40212", "city": "Düsseldorf",
            "register_court": "Amtsgericht Düsseldorf", "register_number": "HRB 12345",
            "managing_directors": ["Anna Adler", "Ben Becker", "Cem Çelik", "Dana Dorn"],
            "tax_number": "103/5734/0815", "iban": "DE89 3704 0044 0532 0130 00", "email": "hallo@selbstlite.test"})
        self.note("Firmenprofil")

    def shareholders(self) -> dict[str, str]:
        people = [
            ("Anna", "Anna Adler", "Geschäftsführerin Finanzen", "#6c5cf2", "1988-04-12", 3, True, 1, "AOK Rheinland"),
            ("Ben", "Ben Becker", "Geschäftsführer Technik", "#10b981", "1991-09-03", 1, False, 0, "TK"),
            ("Cem", "Cem Çelik", "Geschäftsführer Vertrieb", "#f59e0b", "1985-01-27", 4, False, 2, "Barmer"),
            ("Dana", "Dana Dorn", "Geschäftsführerin Produkt", "#ec4899", "1993-06-18", 1, True, 0, "DAK"),
        ]
        team = self.call("GET", "/api/team")["shareholders"]
        ids: dict[str, str] = {}
        for i, (short, name, role, color, born, tax_class, church, kids, insurer) in enumerate(people):
            existing = next((s for s in team if s["name"].split()[0] == short), None)
            body = {"name": name, "email": f"{short.lower()}@selbstlite.test", "share_percent": "25.00",
                    "is_managing_director": True, "phone": f"+49 211 5550{i + 1}0", "birth_date": born,
                    "street": f"Rheinallee {10 + i}", "postal_code": "40476", "city": "Düsseldorf",
                    "role_title": role, "joined_on": f"{YEAR - 1}-03-01", "color": color}
            if existing:
                self.call("PUT", f"/api/shareholders/{existing['id']}", json=body)
                ids[short] = existing["id"]
            else:
                ids[short] = self.call("POST", "/api/shareholders", json=body)["id"]
            self.call("POST", f"/api/shareholders/{ids[short]}/tax-profile", ok=(201, 409), json={
                "valid_from": f"{YEAR}-01-01", "tax_class": tax_class, "child_allowances": f"{kids}.0" if kids else "0",
                "church_tax": church, "state": "NW", "birth_year": int(born[:4]), "children_under_25": kids,
                "health_insurance": "gkv", "health_insurer": insurer})
        self.call("POST", "/api/compensations", ok=(201, 409), json={
            # Rückwirkungsverbot: Gehalt frühestens ab dem laufenden Monat
            "shareholder_ids": list(ids.values()), "valid_from": month_start(0).isoformat(), "gross_month": "3500.00",
            "salaries_per_year": 12, "sv_status": "pflichtig"})
        self.note("4 Gesellschafter-Geschäftsführer mit Lohnsteuer- und SV-Daten, Gehalt 3.500 €")
        return ids

    def customers(self) -> dict[str, str]:
        wanted = [
            {"customer_type": "b2b", "company_name": "HR 1 GmbH", "email": "buchhaltung@hr1.test", "street": "Weg 1",
             "postal_code": "50667", "city": "Köln"},
            {"customer_type": "b2b", "company_name": "Rheinwerk Logistik AG", "email": "rechnung@rheinwerk.test",
             "street": "Hafenstraße 12", "postal_code": "40213", "city": "Düsseldorf", "vat_id": "DE123456789"},
            {"customer_type": "b2b", "company_name": "Praxis Dr. Lenz", "email": "praxis@lenz.test",
             "street": "Kölner Straße 88", "postal_code": "40211", "city": "Düsseldorf"},
            {"customer_type": "b2b", "company_name": "Nordlicht Media GmbH", "email": "finance@nordlicht.test",
             "street": "Elbchaussee 5", "postal_code": "22765", "city": "Hamburg"},
            {"customer_type": "b2c", "first_name": "Max", "last_name": "Muster", "email": "max@muster.test",
             "street": "Gasse 2", "postal_code": "10115", "city": "Berlin"},
        ]
        have = {c["display_name"]: c["id"] for c in self.call("GET", "/api/customers")}
        out: dict[str, str] = {}
        for c in wanted:
            name = c.get("company_name") or f"{c['first_name']} {c['last_name']}"
            if name in have:
                out[name] = have[name]
                continue
            # Willkommensmail mit Portalzugang → landet in Mailpit
            r = self.call("POST", "/api/customers?welcome=true", json=c)
            out[name] = r["id"]
        self.note(f"{len(wanted)} Kunden (neue erhalten die Willkommensmail → Mailpit)")
        return out

    def products(self) -> None:
        have = {p["sku"] for p in self.call("GET", "/api/products")}
        for p in [
            {"sku": "HR-BASIC", "name": "HR-Software Basic", "unit_code": "MON", "price": "1758.00"},
            {"sku": "HR-PRO", "name": "HR-Software Pro", "unit_code": "MON", "price": "2490.00"},
            {"sku": "ONBOARD", "name": "Einrichtung & Datenübernahme", "unit_code": "C62", "price": "3200.00"},
            {"sku": "SCHULUNG", "name": "Schulung (Tagessatz)", "unit_code": "DAY", "price": "1200.00"},
        ]:
            if p["sku"] not in have:
                self.call("POST", "/api/products", json=p)
        self.note("4 Produkte")

    # ─────────────────────────── Vertrieb ───────────────────────────
    def contracts(self, cust: dict[str, str]) -> None:
        have = {c["title"] for c in self.call("GET", "/api/contracts")["contracts"]}
        for name, title, price, start in [
            ("Rheinwerk Logistik AG", "HR-Software Pro – Rheinwerk", "2490.00", month_start(5)),
            ("Praxis Dr. Lenz", "HR-Software Basic – Praxis Lenz", "1758.00", month_start(3)),
        ]:
            if title in have:
                continue
            c = self.call("POST", "/api/contracts", json={
                "customer_id": cust[name], "title": title, "start_date": start.isoformat(), "min_term_months": 12,
                "notice_months": 3, "renewal": "monthly",
                "lines": [{"description": title.split(" – ")[0], "quantity": "1", "unit_code": "MON", "unit_price": price,
                           "tax_case": "OUT_DE_19"}]})
            self.call("POST", f"/api/contracts/{c['id']}/activate")
        self.note("2 aktive Verträge (wiederkehrender Umsatz)")

    def offers(self, cust: dict[str, str]) -> None:
        have = {o["title"] for o in self.call("GET", "/api/offers")}
        title = "HR-Software Pro inkl. Einrichtung"
        if title in have:
            return
        o = self.call("POST", "/api/offers", json={
            "customer_id": cust["Nordlicht Media GmbH"], "title": title,
            "valid_until": (TODAY + dt.timedelta(days=21)).isoformat(),
            "intro_text": "vielen Dank für das nette Gespräch. Gerne bieten wir Ihnen an:",
            "lines": [{"description": "HR-Software Pro (12 Monate)", "quantity": "12", "unit_code": "MON",
                       "unit_price": "2490.00", "tax_case": "OUT_DE_19"},
                      {"description": "Einrichtung & Datenübernahme", "quantity": "1", "unit_code": "C62",
                       "unit_price": "3200.00", "tax_case": "OUT_DE_19"}]})
        self.call("POST", f"/api/offers/{o['id']}/send", ok=(200, 201, 204, 409, 503), json={"email": True})
        self.note("1 Angebot an Nordlicht Media (per Mail → Mailpit)")

    def invoices(self, cust: dict[str, str]) -> None:
        existing = self.call("GET", "/api/invoices")
        existing = existing.get("items", existing) if isinstance(existing, dict) else existing
        if sum(1 for i in existing if i.get("customer_name") == "Rheinwerk Logistik AG") >= 2:
            return
        plans = [
            ("Rheinwerk Logistik AG", "Einrichtung & Datenübernahme", "3200.00", "C62", True),
            ("Rheinwerk Logistik AG", "Schulung Personalabteilung (2 Tage)", "2400.00", "C62", True),
            ("Praxis Dr. Lenz", "Einrichtung & Datenübernahme", "3200.00", "C62", False),
            ("HR 1 GmbH", "Schulung (Tagessatz)", "1200.00", "DAY", False),
        ]
        prev = month_start(1)
        for name, desc, price, unit, paid in plans:
            inv = self.call("POST", "/api/invoices", json={
                "customer_id": cust[name], "service_date_from": prev.isoformat(),
                "service_date_to": (month_start(0) - dt.timedelta(days=1)).isoformat(),
                "lines": [{"description": desc, "quantity": "1", "unit_code": unit, "unit_price": price,
                           "tax_case": "OUT_DE_19"}]})
            inv = self.call("POST", f"/api/invoices/{inv['id']}/issue")
            if paid:
                self.call("POST", f"/api/invoices/{inv['id']}/payments",
                          json={"paid_on": TODAY.isoformat(), "amount": inv["total_gross"], "reference": "Überweisung"})
        self.note("4 Rechnungen freigegeben (2 bezahlt, 2 offen)")

    # ─────────────────────────── Ausgaben ───────────────────────────
    def receipts(self) -> None:
        have_sup = {s["name"]: s["id"] for s in self.call("GET", "/api/suppliers")}
        suppliers = {
            "Hetzner Online GmbH": {"default_category": "hosting", "default_account": "6495"},
            "Google Ireland Ltd.": {"country_code": "IE", "vat_id": "IE6388047V", "default_tax_case": "IN_13B_EU_SERVICE",
                                    "default_category": "software", "default_account": "6837"},
            "Regus Düsseldorf": {"default_category": "rent", "default_account": "6310"},
            "LinkedIn Ireland": {"country_code": "IE", "vat_id": "IE9740425P", "default_tax_case": "IN_13B_EU_SERVICE",
                                 "default_category": "advertising", "default_account": "6600"},
            "Telekom Deutschland GmbH": {"default_category": "telecom", "default_account": "6805"},
            "Steuerkanzlei Weber": {"default_category": "bookkeeping", "default_account": "6830"},
        }
        for name, extra in suppliers.items():
            # Lieferanten-Regel (Standardkonto): danach werden ihre Belege automatisch kontiert
            sup_body = {"name": name, **extra}
            if name in have_sup:
                self.call("PUT", f"/api/suppliers/{have_sup[name]}", json={"country_code": "DE", **sup_body})
            else:
                have_sup[name] = self.call("POST", "/api/suppliers", json=sup_body)["id"]
        existing = {r.get("invoice_number") for r in self._list("/api/receipts")}
        count = 0
        for offset in range(5, -1, -1):
            first = month_start(offset)
            day = min(first + dt.timedelta(days=4), TODAY)
            tag = f"{first:%Y%m}"
            plan = [
                ("Hetzner Online GmbH", f"H{tag}", f"Server & Backup {first:%m/%Y}", "hosting", "IN_DE_19", "89.00", "16.91"),
                ("Google Ireland Ltd.", f"GW{tag}", f"Google Workspace {first:%m/%Y}", "software", "IN_13B_EU_SERVICE",
                 "55.20", None),
                ("Regus Düsseldorf", f"RG{tag}", f"Büro Coworking {first:%m/%Y}", "rent", "IN_DE_19", "690.00", "131.10"),
                ("Telekom Deutschland GmbH", f"TK{tag}", f"Mobilfunk {first:%m/%Y}", "telecom", "IN_DE_19", "119.90", "22.78"),
                ("Steuerkanzlei Weber", f"SW{tag}", f"Finanzbuchhaltung {first:%m/%Y}", "bookkeeping", "IN_DE_19",
                 "450.00", "85.50"),
            ]
            if offset % 2 == 0:
                plan.append(("LinkedIn Ireland", f"LI{tag}", f"LinkedIn Kampagne {first:%m/%Y}", "advertising",
                             "IN_13B_EU_SERVICE", "1450.00", None))
            if offset == 0:
                plan.append(("LinkedIn Ireland", f"LX{tag}", f"Messestand HR-Expo {first:%m/%Y}", "advertising",
                             "IN_13B_EU_SERVICE", "1380.00", None))
            for sup, number, desc, cat, case, net, tax in plan:
                if number in existing or day > TODAY:
                    continue
                up = self.call("POST", "/api/receipt-files",
                               files={"file": (f"{number}.pdf", pdf(f"{sup} - Rechnung {number} - {desc}"), "application/pdf")})
                body: dict[str, Any] = {"supplier_id": have_sup[sup], "file_id": up["id"], "receipt_date": day.isoformat(),
                                        "invoice_number": number, "description": desc, "category": cat, "tax_case": case,
                                        "net_amount": net}
                if tax:
                    body["tax_amount"] = tax
                paid = day + dt.timedelta(days=7)
                if offset > 0 and paid <= TODAY:
                    body["paid_on"] = paid.isoformat()  # ältere Monate per Überweisung bezahlt
                r = self.call("POST", "/api/receipts", json=body)
                # aktueller Monat: zwei Belege bleiben als Entwurf zum Selbst-Ausprobieren
                if offset == 0 and number.startswith(("TK", "SW")):
                    continue
                self.call("POST", f"/api/receipts/{r['id']}/book")
                count += 1
        self.new_supplier_from_inbox()
        self.note(f"6 Lieferanten mit Regel, {count} gebuchte Belege über 6 Monate, Entwürfe „bereit zum Buchen“ "
                  "und ein neuer Lieferant zum Einsortieren")

    def new_supplier_from_inbox(self) -> None:
        """Wie aus dem Belegpostfach: neuer Lieferant (noch ohne Regel) mit einer Rechnung → „Bitte prüfen“."""
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.services import receipts as rc
        from app.services.invoices import today_berlin as _today

        name = "Adobe Systems Software Ireland Ltd."
        if any(s["name"] == name for s in self.call("GET", "/api/suppliers")):
            return
        tenant = get_settings().tenant_id

        async def create() -> str:
            engine = create_async_engine(get_settings().database_url.get_secret_value())
            async with engine.begin() as conn:
                sup = await rc.save_supplier(conn, tenant, {
                    "name": name, "country_code": "IE", "vat_id": "IE6364992H", "default_tax_case": "IN_13B_EU_SERVICE",
                    "default_category": "software", "notes": "Automatisch aus dem Belegpostfach"}, "system:belegpostfach",
                    needs_review=True)
            await engine.dispose()
            return str(sup["id"])

        sid = asyncio.run(create())
        up = self.call("POST", "/api/receipt-files", files={"file": ("adobe.pdf", pdf(f"Adobe - Creative Cloud {_today():%m/%Y}"),
                                                                     "application/pdf")})
        r = self.call("POST", "/api/receipts", json={
            "supplier_id": sid, "file_id": up["id"], "receipt_date": TODAY.isoformat(), "invoice_number": f"ADB{TODAY:%Y%m}",
            "description": f"Creative Cloud Teams {TODAY:%m/%Y}", "category": "software", "tax_case": "IN_13B_EU_SERVICE",
            "net_amount": "89.99"})

        async def mark_inbox() -> None:
            engine = create_async_engine(get_settings().database_url.get_secret_value())
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE receipts SET source = 'inbox' WHERE id = :id"), {"id": r["id"]})
            await engine.dispose()

        asyncio.run(mark_inbox())

    def _list(self, url: str) -> list[dict[str, Any]]:
        d = self.call("GET", url)
        return d if isinstance(d, list) else d.get("items", d.get("receipts", []))

    # ─────────────────────────── Steuerung ───────────────────────────
    def budgets(self) -> None:
        have = {a["name"] for a in self.call("GET", f"/api/budgets?year={YEAR}")["areas"]}
        for name, budget, color, cats, desc in [
            ("Marketing", "2500.00", "#6c5cf2", ["advertising"], "Anzeigen, Messen, Agentur"),
            ("Büro & Infrastruktur", "1000.00", "#10b981", ["office", "telecom", "hosting"], "Miete, Telefon, Server"),
            ("Software & Tools", "300.00", "#0ea5e9", ["software"], "Lizenzen und SaaS"),
            ("Beratung", "600.00", "#f59e0b", ["bookkeeping", "consulting"], "Steuerberatung, Rechtsberatung"),
        ]:
            if name in have:
                self.call("PUT", f"/api/budgets/{self._area_id(name)}", json={
                    "name": name, "monthly_budget": budget, "color": color, "categories": cats, "description": desc})
            else:
                self.call("POST", "/api/budgets", json={"name": name, "monthly_budget": budget, "color": color,
                                                        "categories": cats, "description": desc})
        for y in {YEAR, month_start(5).year}:
            self.call("POST", f"/api/budgets/apply-suggestions?year={y}")
        self.note("4 Budgetbereiche, Ausgaben nach Kategorie zugeordnet (Marketing diesen Monat über Budget)")

    def _area_id(self, name: str) -> str:
        return str(next(a["id"] for a in self.call("GET", f"/api/budgets?year={YEAR}")["areas"] if a["name"] == name))

    def projects(self, ids: dict[str, str], cust: dict[str, str]) -> None:
        have = {p["name"] for p in self._list("/api/projects")}
        plans = [
            ("Onboarding Rheinwerk", cust["Rheinwerk Logistik AG"], "#10b981", [
                ("Zugänge und SSO einrichten", "Ben", "hoch", 3, "In Arbeit"),
                ("Datenübernahme Personalstamm", "Ben", "dringend", 1, "Zu erledigen"),
                ("Schulung Personalabteilung", "Dana", "mittel", 10, "Zu erledigen"),
                ("Kick-off-Termin", "Cem", "mittel", -5, "Erledigt"),
            ]),
            ("Vertrieb Q4", None, "#f59e0b", [
                ("Angebot Nordlicht nachfassen", "Cem", "hoch", 2, "Zu erledigen"),
                ("Messe HR-Expo vorbereiten", "Dana", "mittel", 14, "In Arbeit"),
                ("Referenzkunden-Case schreiben", "Anna", "niedrig", 30, "Zu erledigen"),
            ]),
            ("Jahresabschluss", None, "#6c5cf2", [
                ("Belege November vollständig erfassen", "Anna", "dringend", 4, "In Arbeit"),
                ("Inventur Hardware", "Ben", "niedrig", 40, "Zu erledigen"),
            ]),
        ]
        for name, customer, color, tasks in plans:
            if name in have:
                continue
            pid = self.call("POST", "/api/projects", json={"name": name, "customer_id": customer, "color": color})["id"]
            cols = {c["name"]: c["id"] for c in self.call("GET", f"/api/projects/{pid}/board")["columns"]}
            for title, who, prio, due, col in tasks:
                t = self.call("POST", f"/api/projects/{pid}/tasks", json={
                    "column_id": cols["Zu erledigen"], "title": title, "assignee_id": ids[who], "priority": prio,
                    "due_date": (TODAY + dt.timedelta(days=due)).isoformat()})
                if col != "Zu erledigen":
                    self.call("POST", f"/api/tasks/{t['id']}/move", json={"column_id": cols[col]})
        self.note("3 Projekte mit 9 Aufgaben, zugeordnet an die Gesellschafter")

    def employees(self) -> None:
        have = {f"{e['first_name']} {e['last_name']}" for e in self.call("GET", "/api/payroll/employees")["employees"]}
        staff: list[dict[str, Any]] = [
            {"first_name": "Lena", "last_name": "Vogel", "email": "lena.vogel@example.org", "position": "Projektmanagerin",
             "entry_date": "2025-03-01", "salary_type": "monthly", "gross_month": "3900.00", "weekly_hours": "40", "tax_class": 1,
             "tax_id": "12345678903", "health_insurer": "Techniker Krankenkasse", "kv_additional_rate": "2.45",
             "street": "Kaiserstraße 5", "postal_code": "40479", "city": "Düsseldorf", "iban": "DE89370400440532013000",
             "portal_enabled": True, "vacation_days": 28},
            {"first_name": "Tim", "last_name": "Becker", "position": "Werkstudent Entwicklung", "entry_date": "2025-10-01",
             "employment_type": "werkstudent", "salary_type": "hourly", "hourly_rate": "17.50", "weekly_hours": "18",
             "health_insurer": "AOK Rheinland/Hamburg", "iban": "DE02120300000000202051"},
            {"first_name": "Sabine", "last_name": "Krüger", "position": "Buchhaltung (Minijob)", "entry_date": "2024-09-01",
             "employment_type": "minijob", "salary_type": "hourly", "hourly_rate": "15.00", "weekly_hours": "8",
             "iban": "DE02500105170137075030"},
        ]
        for e in staff:
            if f"{e['first_name']} {e['last_name']}" not in have:
                self.call("POST", "/api/payroll/employees", json=e)
        self.note("3 Mitarbeiter (Angestellte mit Portal-Zugang, Werkstudent, Minijob) – Login lena.vogel@example.org unter /portal")

    def finance(self) -> None:
        self.call("PUT", "/api/finance/settings", json={"min_liquidity": "10000", "monthly_fixed_costs": "1500",
                                                         "offer_followup_days": 7, "offer_auto_reminder": False,
                                                         "welcome_mail": True})
        self.call("POST", "/api/bank/balance", json={"balance_on": TODAY.isoformat(), "amount": "48250.00"})
        self.note("Kontostand 48.250 € und Mindestreserve 10.000 €")

    def run(self) -> None:
        print("Demodaten werden angelegt …")
        self.company()
        ids = self.shareholders()
        cust = self.customers()
        self.products()
        self.contracts(cust)
        self.offers(cust)
        self.invoices(cust)
        self.receipts()
        self.budgets()
        self.projects(ids, cust)
        self.employees()
        self.finance()
        print("Fertig. App: http://localhost  ·  Test-Postfach: http://localhost:8025")


def main() -> None:
    s = get_settings()
    if s.is_production or not s.dev_login_enabled:
        sys.exit("Demodaten nur im lokalen Testbetrieb (APP_ENV=development, DEV_LOGIN_ENABLED=true)")
    Demo().run()


if __name__ == "__main__":
    main()
