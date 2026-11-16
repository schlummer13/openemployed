"""Kontoauszugsformate der Banken: CAMT.053 (ISO 20022, XML), MT940 (SWIFT) und CSV – automatisch erkannt."""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Any

from lxml import etree

from app.services.bank import BankError, parse_csv

D = Decimal


def _ext_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:40]


def parse_camt(content: bytes) -> list[dict[str, Any]]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError as exc:
        raise BankError(f"CAMT-Datei nicht lesbar: {exc}") from exc
    ns = {"c": etree.QName(root).namespace or ""}
    if "camt.05" not in ns["c"]:
        raise BankError("Keine CAMT.052/053/054-Datei")

    def t(node: Any, path: str) -> str | None:
        found = node.find(path, ns)
        return found.text.strip() if found is not None and found.text else None

    out = []
    for stmt in root.iterfind(".//c:Stmt", ns) or []:
        iban = t(stmt, "c:Acct/c:Id/c:IBAN")
        for i, e in enumerate(stmt.iterfind("c:Ntry", ns)):
            if (t(e, "c:Sts/c:Cd") or t(e, "c:Sts") or "BOOK") not in ("BOOK", "Book"):
                continue  # nur gebuchte Umsätze
            amount = D(t(e, "c:Amt") or "0").quantize(D("0.01"))
            if t(e, "c:CdtDbtInd") == "DBIT":
                amount = -amount
            day = t(e, "c:BookgDt/c:Dt") or (t(e, "c:BookgDt/c:DtTm") or "")[:10] or t(e, "c:ValDt/c:Dt")
            if not day:
                raise BankError("CAMT-Umsatz ohne Buchungsdatum")
            tx = e.find("c:NtryDtls/c:TxDtls", ns)
            credit = amount > 0
            party = iban_party = ref = None
            if tx is not None:
                party = t(tx, "c:RltdPties/c:Dbtr/c:Nm" if credit else "c:RltdPties/c:Cdtr/c:Nm") \
                    or t(tx, "c:RltdPties/c:Dbtr/c:Pty/c:Nm" if credit else "c:RltdPties/c:Cdtr/c:Pty/c:Nm")
                iban_party = t(tx, "c:RltdPties/c:DbtrAcct/c:Id/c:IBAN" if credit else "c:RltdPties/c:CdtrAcct/c:Id/c:IBAN")
                ref = " ".join(x.text.strip() for x in tx.iterfind("c:RmtInf/c:Ustrd", ns) if x.text) or None
            ref = ref or t(e, "c:AddtlNtryInf")
            ext = t(e, "c:AcctSvcrRef") or (t(tx, "c:Refs/c:AcctSvcrRef") if tx is not None else None) \
                or _ext_id(iban, day, amount, party, ref, i)
            out.append({"external_id": ext[:200], "booked_on": dt.date.fromisoformat(day[:10]), "amount": amount,
                        "counterparty": (party or "")[:200] or None, "counterparty_iban": iban_party,
                        "reference": (ref or "")[:500] or None,
                        "account_iban": iban})
    return out


def parse_mt940(content: bytes) -> list[dict[str, Any]]:
    import mt940

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    try:
        txs = mt940.models.Transactions(processors={"pre_statement": [mt940.processors.add_currency_pre_processor("EUR")]})
        txs.parse(text)
    except Exception as exc:
        raise BankError(f"MT940-Datei nicht lesbar: {exc}") from exc
    iban = str(txs.data.get("account_identification") or "") or None
    return [_from_mt940(t.data, iban, i) for i, t in enumerate(txs)]


def _from_mt940(d: dict[str, Any], iban: str | None, i: int) -> dict[str, Any]:
    amount = D(str(d["amount"].amount)).quantize(D("0.01"))
    day = d.get("entry_date") or d.get("date")
    purpose = d.get("purpose") or d.get("transaction_details") or ""
    if isinstance(purpose, list):
        purpose = " ".join(purpose)
    party = d.get("applicant_name") or ""
    ext = d.get("bank_reference") if d.get("bank_reference") not in (None, "NONREF") else None
    return {"external_id": (ext or _ext_id(iban, day, amount, party, purpose, d.get("customer_reference"), i))[:200],
            "booked_on": day, "amount": amount, "counterparty": party[:200] or None,
            "counterparty_iban": d.get("applicant_iban") or None, "reference": str(purpose)[:500] or None, "account_iban": iban}


def parse_any(content: bytes, filename: str) -> list[dict[str, Any]]:
    head = content[:400].lstrip()
    name = filename.lower()
    if head.startswith(b"<?xml") or head.startswith(b"<Document") or name.endswith(".xml"):
        return parse_camt(content)
    if b":20:" in content[:2000] or name.endswith((".sta", ".mt940", ".940")):
        return parse_mt940(content)
    rows = parse_csv(content)
    for r in rows:
        r.setdefault("account_iban", None)
    return rows


def camt_closing_balances(content: bytes) -> list[dict[str, Any]]:
    """Schlusssalden (CLBD) aus CAMT.052/053 – für die Liquiditätsübersicht."""
    try:
        root = etree.fromstring(content, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    except etree.XMLSyntaxError:
        return []
    ns = {"c": etree.QName(root).namespace or ""}
    out = []
    for stmt in root.iterfind(".//c:Stmt", ns):
        iban_el = stmt.find("c:Acct/c:Id/c:IBAN", ns)
        for bal in stmt.iterfind("c:Bal", ns):
            code = bal.find("c:Tp/c:CdOrPrtry/c:Cd", ns)
            amt, ind, day = bal.find("c:Amt", ns), bal.find("c:CdtDbtInd", ns), bal.find("c:Dt/c:Dt", ns)
            if code is None or code.text != "CLBD" or amt is None or day is None or not amt.text or not day.text:
                continue
            value = D(amt.text).quantize(D("0.01"))
            signed = -value if ind is not None and ind.text == "DBIT" else value
            out.append({"balance_on": dt.date.fromisoformat(day.text[:10]), "amount": signed,
                        "iban": iban_el.text if iban_el is not None else None})
    return out
