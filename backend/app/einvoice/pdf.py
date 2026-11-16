"""ZUGFeRD-/Factur-X-PDF: Bildschirm-/Druckfassung (PDF/A-3b) mit eingebettetem CII-XML (EN 16931).

Ablauf: Snapshot → HTML (Jinja, Autoescape) → WeasyPrint PDF/A-3b → factur-x bettet factur-x.xml ein
(AFRelationship „Alternative“, XMP-Metadaten). Externe Ressourcen werden beim Rendern blockiert (nur data:-URIs),
damit z. B. ein SVG-Logo keine Netzwerk- oder Dateizugriffe auslösen kann.
"""

from __future__ import annotations

import base64
import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import facturx
import segno.helpers
from jinja2 import Environment, FileSystemLoader, pass_context, select_autoescape

UNIT_LABELS = {"C62": "Stück", "HUR": "Std.", "MIN": "Min.", "DAY": "Tag", "WEE": "Woche", "MON": "Monat",
               "ANN": "Jahr", "LS": "Pausch.", "KMT": "km"}


def _euro(value: str | Decimal | None) -> str:
    if value is None:
        return "–"
    d = Decimal(value)
    text = f"{abs(d):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{'−' if d < 0 else ''}{text} €"


def _date(value: str | None) -> str:
    return dt.date.fromisoformat(value).strftime("%d.%m.%Y") if value else "–"


def _qty(value: str) -> str:
    d = Decimal(value)
    text = f"{abs(d):.4f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{'−' if d < 0 else ''}{text}"


def _rate(value: str) -> str:
    return f"{Decimal(value).normalize():f}".replace(".", ",")


def _iban(value: str) -> str:
    return " ".join(value[i:i + 4] for i in range(0, len(value), 4))


def _neg(value: str) -> str:
    return str(-Decimal(value))


_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}


@pass_context
def _money(ctx: Any, value: str | Decimal | None) -> str:
    """Betrag in Rechnungswährung (Kontext ``s.currency``), sonst Euro."""
    snap = ctx.get("s") or {}
    cur = snap.get("currency", "EUR") if isinstance(snap, dict) else "EUR"
    text = _euro(value)
    return text if cur == "EUR" else text.replace(" €", f" {SYMBOLS.get(cur, cur)}")


_env.filters.update(euro=_money, eur=_euro, date=_date, qty=_qty, rate=_rate, iban=_iban, neg=_neg,
                    unit=lambda c: UNIT_LABELS.get(c, c))


def girocode_data_uri(snapshot: dict[str, Any], number: str) -> str | None:
    """EPC-QR-Code (GiroCode) für Überweisungen; nur bei positivem Betrag und vorhandener IBAN."""
    seller = snapshot["seller"]
    amount = Decimal(snapshot["totals"]["gross"])
    if not seller.get("iban") or amount <= 0 or amount > Decimal("999999999.99"):
        return None
    qr = segno.helpers.make_epc_qr(
        name=seller["legal_name"][:70], iban=seller["iban"], amount=amount, text=number[:140],
        bic=seller.get("bic") or None,
    )
    uri: str = qr.svg_data_uri(scale=4, border=0)
    return uri


def render_html(number: str, snapshot: dict[str, Any], *, logo_data_uri: str | None) -> str:
    credit = snapshot["kind"] == "cancellation"
    return _env.get_template("invoice.html").render(
        s=snapshot,
        number=number,
        title="Stornorechnung" if credit else "Rechnung",
        credit=credit,
        gross=snapshot["price_mode"] == "gross",
        logo=logo_data_uri,
        girocode=None if credit else girocode_data_uri(snapshot, number),
        offer=False,
    )


def render_offer_html(number: str, snapshot: dict[str, Any], *, logo_data_uri: str | None) -> str:
    return _env.get_template("invoice.html").render(
        s=snapshot, number=number, title="Angebot", credit=False, offer=True,
        gross=snapshot["price_mode"] == "gross", logo=logo_data_uri, girocode=None,
    )


def render_offer_pdf(number: str, snapshot: dict[str, Any], *, logo: str | None) -> bytes:
    """Angebot als einfaches PDF (kein E-Rechnungs-XML: Angebote sind keine Rechnungen)."""
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False, fail_on_errors=False)
    pdf: bytes = HTML(string=render_offer_html(number, snapshot, logo_data_uri=logo), url_fetcher=fetcher,
                      base_url=None).write_pdf(pdf_variant="pdf/a-3b")
    return pdf


def logo_data_uri(content: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def render_facturx_pdf(number: str, snapshot: dict[str, Any], xml: bytes, *, logo: str | None) -> bytes:
    from weasyprint import HTML  # erst hier importieren: benötigt Systembibliotheken (Pango)
    from weasyprint.urls import URLFetcher

    html = render_html(number, snapshot, logo_data_uri=logo)
    # Nur eingebettete data:-URIs zulassen – keine Netzwerk-, Datei- oder FTP-Zugriffe (SSRF-Schutz)
    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False, fail_on_errors=False)
    pdf: bytes = HTML(string=html, url_fetcher=fetcher, base_url=None).write_pdf(
        pdf_variant="pdf/a-3b", uncompressed_pdf=False
    )
    title = f"{'Stornorechnung' if snapshot['kind'] == 'cancellation' else 'Rechnung'} {number}"
    result: bytes = facturx.generate_from_binary(
        pdf,
        xml,
        flavor="factur-x",
        level="en16931",
        check_xsd=True,
        pdf_metadata={
            "author": snapshot["seller"]["legal_name"],
            "keywords": "Factur-X, ZUGFeRD, Rechnung",
            "title": title,
            "subject": f"{title} an {snapshot['buyer']['name']}",
        },
        lang="de-DE",
        afrelationship="alternative",
    )
    return result


def render_dunning_html(number: str, snapshot: dict[str, Any], *, logo_data_uri: str | None) -> str:
    return _env.get_template("dunning.html").render(s=snapshot, number=number, logo=logo_data_uri,
                                                    girocode=_dunning_girocode(snapshot, number))


def _dunning_girocode(snapshot: dict[str, Any], number: str) -> str | None:
    fake = {"seller": snapshot["seller"], "totals": {"gross": snapshot["total"]}}
    return girocode_data_uri(fake, f"{snapshot['invoice']['number']} {number}")


def render_dunning_pdf(number: str, snapshot: dict[str, Any], *, logo: str | None) -> bytes:
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False, fail_on_errors=False)
    pdf: bytes = HTML(string=render_dunning_html(number, snapshot, logo_data_uri=logo), url_fetcher=fetcher,
                      base_url=None).write_pdf(pdf_variant="pdf/a-3b")
    return pdf


def render_plain_pdf(html: str) -> bytes:
    """Beliebiges HTML (eigene Vorlagen) als PDF – nur eingebettete data:-URIs erlaubt."""
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False, fail_on_errors=False)
    pdf: bytes = HTML(string=html, url_fetcher=fetcher, base_url=None).write_pdf()
    return pdf
