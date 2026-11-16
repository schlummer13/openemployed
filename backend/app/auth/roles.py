"""Rollen und Rechte je Bereich – zentral geprüft bei jeder API-Anfrage (``current_user``).

Jede Rolle vergibt je Bereich ``none`` (kein Zugriff), ``read`` (lesen) oder ``write`` (bearbeiten). Die vier Standardrollen
sind Vorlagen; eigene Rollen legt die Geschäftsführung unter *Benutzer & Rollen* an (Tabelle ``roles``). ``admin`` hat immer
alle Rechte. Eigene Daten (Konto, Stempeluhr, Urlaub, Handbuch) sind für jede angemeldete Person erreichbar.
"""

from __future__ import annotations

from typing import Literal

Level = Literal["none", "read", "write"]
Role = str

AREAS: dict[str, tuple[str, str]] = {
    "dashboard": ("Übersicht & Aufgaben", "Kennzahlen, Zu erledigen"),
    "sales": ("Vertrieb & Kunden", "Pipeline, Kunden, Produkte, Angebote, Verträge, Signaturen"),
    "invoices": ("Rechnungen & Forderungen", "Rechnungen, Zahlungen, Mahnwesen"),
    "receipts": ("Belege & Lieferanten", "Belegpostfach, Erfassung, Lieferanten-Regeln"),
    "accounting": ("Buchhaltung", "Journal, Bank, Anlagen, Budgets, DATEV, Import, Währungen"),
    "payments": ("Zahlungsverkehr", "SEPA-Überweisungen und Lastschriften"),
    "payroll": ("Lohn & Personal", "Mitarbeiter, Lohnläufe, GF-Gehälter, Urlaubsgenehmigung, Zeitkonten"),
    "taxes": ("Steuern", "UStVA, ELSTER, ZM, Vorauszahlungen"),
    "reports": ("Auswertungen & Abschlüsse", "BWA, Monatsabschluss, Jahresabschluss, Liquidität"),
    "archive": ("Dokumentenarchiv", "Archiv, Upload, Vernichtung"),
    "projects": ("Projekte & Zeiten", "Projekte, Aufgaben, Zeiterfassung, Reisekosten"),
    "automations": ("Automatisierungen", "Eingebaute Jobs und eigene Workflows"),
    "admin": ("Verwaltung", "Benutzer, Rollen, Einstellungen, API, Firmen, Firmenprofil"),
}

ROLE_LABELS: dict[str, str] = {"admin": "Geschäftsführung", "accounting": "Buchhaltung", "advisor": "Steuerberater (nur lesen)",
                               "employee": "Mitarbeiter"}
_ALL_WRITE = dict.fromkeys(AREAS, "write")
BUILTIN: dict[str, dict[str, str]] = {
    "admin": _ALL_WRITE,
    "accounting": {**_ALL_WRITE, "admin": "none"},
    "advisor": {**dict.fromkeys(AREAS, "read"), "admin": "none", "automations": "none"},
    "employee": {**dict.fromkeys(AREAS, "none"), "projects": "write"},
}

# Längster passender Präfix gewinnt
PATH_AREAS: tuple[tuple[str, str], ...] = (
    ("/api/reports/prepayments", "taxes"), ("/api/reports/zm", "taxes"), ("/api/reports/vat-extension", "taxes"),
    ("/api/reports", "reports"), ("/api/year-end", "reports"), ("/api/finance", "reports"),
    ("/api/dashboard", "dashboard"), ("/api/inbox", "dashboard"),
    ("/api/crm", "sales"), ("/api/customers", "sales"), ("/api/products", "sales"), ("/api/offers", "sales"), ("/api/contracts", "sales"),
    ("/api/terminations", "sales"), ("/api/portal-access", "sales"), ("/api/signatures", "sales"),
    ("/api/invoices", "invoices"), ("/api/receivables", "invoices"), ("/api/payments", "invoices"), ("/api/dunning", "invoices"),
    ("/api/base-rates", "invoices"),
    ("/api/receipts", "receipts"), ("/api/receipt-files", "receipts"), ("/api/receipt-inbox", "receipts"), ("/api/suppliers", "receipts"),
    ("/api/receipt-accounts", "receipts"),
    ("/api/accounting", "accounting"), ("/api/bank", "accounting"), ("/api/assets", "accounting"), ("/api/datev", "accounting"),
    ("/api/gobd", "accounting"), ("/api/import", "accounting"), ("/api/budgets", "accounting"), ("/api/currencies", "accounting"),
    ("/api/sepa", "payments"),
    ("/api/payroll", "payroll"), ("/api/shareholders", "payroll"), ("/api/compensations", "payroll"), ("/api/absences", "payroll"),
    ("/api/timeclock", "payroll"), ("/api/holidays/company", "payroll"), ("/api/personnel", "payroll"),
    ("/api/tax", "taxes"), ("/api/elster", "taxes"),
    ("/api/archive", "archive"),
    ("/api/projects", "projects"), ("/api/tasks", "projects"), ("/api/time", "projects"), ("/api/travel", "projects"),
    ("/api/automations", "automations"), ("/api/workflows", "automations"), ("/api/mail", "automations"),
    ("/api/admin/", "admin"), ("/api/setup", "admin"), ("/api/company-profile", "admin"), ("/api/companies", "admin"),
)
# Für jede angemeldete Person: eigene Daten und gemeinsame Stammdaten zum Lesen
SELF_SERVICE = ("/api/auth/", "/api/help", "/api/me/")
COMMON_READ = ("/api/team", "/api/tax-cases", "/api/expense-meta", "/api/setup", "/api/ai/status", "/api/mail/status",
               "/api/company-profile", "/api/currencies", "/api/companies", "/api/holidays")
COMMON_POST = ("/api/companies/select",)
# Lesende Rechte genügen für diese POST-Endpunkte (sie erzeugen nur Dateien)
READ_POSTS = ("/api/datev/export", "/api/datev/check")
SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


def area_for(path: str) -> str | None:
    best: tuple[int, str | None] = (0, None)
    for prefix, area in PATH_AREAS:
        if path.startswith(prefix) and len(prefix) > best[0]:
            best = (len(prefix), area)
    return best[1]


def permissions_of(role: str, custom: dict[str, str] | None = None) -> dict[str, str]:
    if role == "admin":
        return dict(_ALL_WRITE)
    base = custom if custom is not None else BUILTIN.get(role, dict.fromkeys(AREAS, "none"))
    return {a: base.get(a, "none") if base.get(a) in ("none", "read", "write") else "none" for a in AREAS}


def allowed_with(perms: dict[str, str], method: str, path: str) -> bool:
    if path.startswith(SELF_SERVICE) or (method in SAFE and path.startswith(COMMON_READ)) or path.startswith(COMMON_POST):
        return True
    area = area_for(path)
    if area is None:  # unbekannter Bereich: lesen mit irgendeinem Recht, schreiben nur Verwaltung
        return (method in SAFE and any(v != "none" for v in perms.values())) or perms.get("admin") == "write"
    level = perms.get(area, "none")
    if method in SAFE:
        return level in ("read", "write")
    if path.startswith(READ_POSTS):
        return level in ("read", "write")
    return level == "write"


def allowed(role: str, method: str, path: str) -> bool:
    """Prüfung für die Standardrollen (Tests, API-Schlüssel)."""
    return allowed_with(permissions_of(role), method, path)
