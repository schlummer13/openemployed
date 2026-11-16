"""SEPA-Dateien nach DK-Spezifikation (Anlage 3): Überweisungen pain.001.001.09 und Basislastschriften pain.008.001.08.

Die Dateien werden im Online-Banking bzw. über EBICS/FinTS hochgeladen und dort freigegeben – OpenEmployed löst keine Zahlung aus.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from xml.sax.saxutils import escape

NS_001 = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"
NS_008 = "urn:iso:std:iso:20022:tech:xsd:pain.008.001.08"
CREDITOR_ID = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{3}[A-Z0-9]{1,28}$")
BIC = re.compile(r"^[A-Z]{6}[A-Z2-9][A-NP-Z0-9]([A-Z0-9]{3})?$")
MANDATE_REF = re.compile(r"^[A-Za-z0-9+?/\-:().,' ]{1,35}$")
# Zulässiger SEPA-Zeichensatz (DK „Latin Character Set“); Umlaute werden umschrieben
_ALLOWED = re.compile(r"[^A-Za-z0-9/\-?:().,'+ ]")
_REPL = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "&": "+", "€": "EUR", "@": "(at)", "_": "-"}


class SepaError(ValueError):
    pass


def sepa_text(value: str, max_len: int) -> str:
    v = "".join(_REPL.get(c, c) for c in value)
    v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode()
    v = _ALLOWED.sub(" ", v)
    return re.sub(r"\s+", " ", v).strip()[:max_len]


def iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", s):
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def creditor_id_ok(cid: str) -> bool:
    """Gläubiger-Identifikationsnummer (z. B. DE98ZZZ09999999999) inkl. Prüfziffer (ohne Geschäftsbereichskennung)."""
    s = cid.replace(" ", "").upper()
    if not CREDITOR_ID.fullmatch(s):
        return False
    national = s[7:]
    digits = "".join(str(int(c, 36)) for c in national + s[:2] + "00")
    return 98 - int(digits) % 97 == int(s[2:4])


@dataclass(frozen=True, slots=True)
class Party:
    name: str
    iban: str
    bic: str | None = None


@dataclass(frozen=True, slots=True)
class Transfer:
    party: Party
    amount: Decimal
    reference: str
    end_to_end: str


@dataclass(frozen=True, slots=True)
class Debit:
    party: Party
    amount: Decimal
    reference: str
    end_to_end: str
    mandate_id: str
    mandate_date: dt.date
    sequence: str  # FRST | RCUR | OOFF | FNAL


def _amt(v: Decimal) -> str:
    return f"{v.quantize(Decimal('0.01')):.2f}"


def _check_party(p: Party, what: str) -> Party:
    iban = p.iban.replace(" ", "").upper()
    if not iban_ok(iban):
        raise SepaError(f"{what}: IBAN ungültig ({p.name})")
    bic = (p.bic or "").replace(" ", "").upper() or None
    if bic and not BIC.fullmatch(bic):
        raise SepaError(f"{what}: BIC ungültig ({p.name})")
    name = sepa_text(p.name, 70)
    if not name:
        raise SepaError(f"{what}: Name fehlt")
    return Party(name, iban, bic)


def _agent(bic: str | None) -> str:
    return f"<FinInstnId><BICFI>{bic}</BICFI></FinInstnId>" if bic else "<FinInstnId><Othr><Id>NOTPROVIDED</Id></Othr></FinInstnId>"


def _id(value: str) -> str:
    v = sepa_text(value, 35).replace(" ", "")
    if not v:
        raise SepaError("Kennung leer")
    return v


def pain001(*, debtor: Party, transfers: list[Transfer], execution_date: dt.date, msg_id: str, created: dt.datetime,
            batch_booking: bool = True) -> str:
    if not transfers:
        raise SepaError("Keine Überweisungen")
    d = _check_party(debtor, "Auftraggeber")
    items = []
    total = Decimal(0)
    for t in transfers:
        if t.amount <= 0 or t.amount > Decimal("999999999.99"):
            raise SepaError(f"Betrag ungültig ({t.party.name})")
        p = _check_party(t.party, "Empfänger")
        total += t.amount
        items.append(
            f"<CdtTrfTxInf><PmtId><EndToEndId>{escape(_id(t.end_to_end))}</EndToEndId></PmtId>"
            f'<Amt><InstdAmt Ccy="EUR">{_amt(t.amount)}</InstdAmt></Amt>'
            + (f"<CdtrAgt>{_agent(p.bic)}</CdtrAgt>" if p.bic else "")
            + f"<Cdtr><Nm>{escape(p.name)}</Nm></Cdtr><CdtrAcct><Id><IBAN>{p.iban}</IBAN></Id></CdtrAcct>"
            f"<RmtInf><Ustrd>{escape(sepa_text(t.reference, 140))}</Ustrd></RmtInf></CdtTrfTxInf>")
    mid = escape(_id(msg_id))
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="{NS_001}"><CstmrCdtTrfInitn>'
        f"<GrpHdr><MsgId>{mid}</MsgId><CreDtTm>{created:%Y-%m-%dT%H:%M:%S}</CreDtTm><NbOfTxs>{len(items)}</NbOfTxs>"
        f"<CtrlSum>{_amt(total)}</CtrlSum><InitgPty><Nm>{escape(d.name)}</Nm></InitgPty></GrpHdr>"
        f"<PmtInf><PmtInfId>{mid}-1</PmtInfId><PmtMtd>TRF</PmtMtd><BtchBookg>{str(batch_booking).lower()}</BtchBookg>"
        f"<NbOfTxs>{len(items)}</NbOfTxs><CtrlSum>{_amt(total)}</CtrlSum><PmtTpInf><SvcLvl><Cd>SEPA</Cd></SvcLvl></PmtTpInf>"
        f"<ReqdExctnDt><Dt>{execution_date:%Y-%m-%d}</Dt></ReqdExctnDt><Dbtr><Nm>{escape(d.name)}</Nm></Dbtr>"
        f"<DbtrAcct><Id><IBAN>{d.iban}</IBAN></Id></DbtrAcct><DbtrAgt>{_agent(d.bic)}</DbtrAgt><ChrgBr>SLEV</ChrgBr>"
        f"{''.join(items)}</PmtInf></CstmrCdtTrfInitn></Document>"
    )


def pain008(*, creditor: Party, creditor_id: str, debits: list[Debit], collection_date: dt.date, msg_id: str, created: dt.datetime,
            scheme: str = "CORE", batch_booking: bool = True) -> str:
    if not debits:
        raise SepaError("Keine Lastschriften")
    cid = creditor_id.replace(" ", "").upper()
    if not creditor_id_ok(cid):
        raise SepaError("Gläubiger-Identifikationsnummer ungültig")
    c = _check_party(creditor, "Gläubiger")
    groups: dict[str, list[str]] = {}
    sums: dict[str, Decimal] = {}
    for x in debits:
        if x.amount <= 0:
            raise SepaError(f"Betrag ungültig ({x.party.name})")
        if x.sequence not in ("FRST", "RCUR", "OOFF", "FNAL"):
            raise SepaError("Sequenztyp ungültig")
        if not MANDATE_REF.fullmatch(x.mandate_id):
            raise SepaError(f"Mandatsreferenz ungültig ({x.mandate_id})")
        p = _check_party(x.party, "Zahlungspflichtiger")
        groups.setdefault(x.sequence, []).append(
            f"<DrctDbtTxInf><PmtId><EndToEndId>{escape(_id(x.end_to_end))}</EndToEndId></PmtId>"
            f'<InstdAmt Ccy="EUR">{_amt(x.amount)}</InstdAmt><DrctDbtTx><MndtRltdInf><MndtId>{escape(x.mandate_id)}</MndtId>'
            f"<DtOfSgntr>{x.mandate_date:%Y-%m-%d}</DtOfSgntr></MndtRltdInf></DrctDbtTx>"
            + (f"<DbtrAgt>{_agent(p.bic)}</DbtrAgt>" if p.bic else f"<DbtrAgt>{_agent(None)}</DbtrAgt>")
            + f"<Dbtr><Nm>{escape(p.name)}</Nm></Dbtr><DbtrAcct><Id><IBAN>{p.iban}</IBAN></Id></DbtrAcct>"
            f"<RmtInf><Ustrd>{escape(sepa_text(x.reference, 140))}</Ustrd></RmtInf></DrctDbtTxInf>")
        sums[x.sequence] = sums.get(x.sequence, Decimal(0)) + x.amount
    mid = escape(_id(msg_id))
    blocks = []
    for i, (seq, txs) in enumerate(sorted(groups.items()), start=1):
        blocks.append(
            f"<PmtInf><PmtInfId>{mid}-{i}</PmtInfId><PmtMtd>DD</PmtMtd><BtchBookg>{str(batch_booking).lower()}</BtchBookg>"
            f"<NbOfTxs>{len(txs)}</NbOfTxs><CtrlSum>{_amt(sums[seq])}</CtrlSum>"
            f"<PmtTpInf><SvcLvl><Cd>SEPA</Cd></SvcLvl><LclInstrm><Cd>{scheme}</Cd></LclInstrm><SeqTp>{seq}</SeqTp></PmtTpInf>"
            f"<ReqdColltnDt>{collection_date:%Y-%m-%d}</ReqdColltnDt><Cdtr><Nm>{escape(c.name)}</Nm></Cdtr>"
            f"<CdtrAcct><Id><IBAN>{c.iban}</IBAN></Id></CdtrAcct><CdtrAgt>{_agent(c.bic)}</CdtrAgt><ChrgBr>SLEV</ChrgBr>"
            f"<CdtrSchmeId><Id><PrvtId><Othr><Id>{cid}</Id><SchmeNm><Prtry>SEPA</Prtry></SchmeNm></Othr></PrvtId></Id></CdtrSchmeId>"
            f"{''.join(txs)}</PmtInf>")
    total = sum(sums.values(), Decimal(0))
    count = sum(len(t) for t in groups.values())
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="{NS_008}"><CstmrDrctDbtInitn>'
        f"<GrpHdr><MsgId>{mid}</MsgId><CreDtTm>{created:%Y-%m-%dT%H:%M:%S}</CreDtTm><NbOfTxs>{count}</NbOfTxs>"
        f"<CtrlSum>{_amt(total)}</CtrlSum><InitgPty><Nm>{escape(c.name)}</Nm></InitgPty></GrpHdr>"
        f"{''.join(blocks)}</CstmrDrctDbtInitn></Document>"
    )


def next_business_day(d: dt.date, days: int = 1) -> dt.date:
    """TARGET2-Geschäftstage (Wochenende, 1.1., Karfreitag, Ostermontag, 1.5., 25./26.12.)."""
    def easter(y: int) -> dt.date:
        a, b, c = y % 19, y // 100, y % 100
        d_, e = b // 4, b % 4
        g = (8 * b + 13) // 25
        h = (19 * a + b - d_ - g + 15) % 30
        i, k = c // 4, c % 4
        m = (32 + 2 * e + 2 * i - h - k) % 7
        n = (a + 11 * h + 19 * m) // 433
        month = (h + m - 7 * n + 90) // 25
        return dt.date(y, month, (h + m - 7 * n + 33 * month + 19) % 32)

    def closed(x: dt.date) -> bool:
        e = easter(x.year)
        return x.weekday() >= 5 or (x.month, x.day) in ((1, 1), (5, 1), (12, 25), (12, 26)) or x in (e - dt.timedelta(days=2),
                                                                                                          e + dt.timedelta(days=1))
    out = d
    for _ in range(days):
        out += dt.timedelta(days=1)
        while closed(out):
            out += dt.timedelta(days=1)
    return out
