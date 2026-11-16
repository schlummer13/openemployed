"""SEPA-XML (pain.001.001.09 / pain.008.001.08) – Aufbau, Summen, Zeichensatz, Prüfungen."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal as D

import pytest

from app.domain.sepa import NS_001, NS_008, Debit, Party, SepaError, Transfer, creditor_id_ok, pain001, pain008, sepa_text

ME = Party("Beispiel UG (haftungsbeschränkt)", "DE89 3704 0044 0532 0130 00", "COBADEFFXXX")
NOW = dt.datetime(2026, 11, 15, 10, 0, 0)


def test_pain001_structure_and_sums() -> None:
    xml = pain001(debtor=ME, transfers=[
        Transfer(Party("Müller & Söhne", "DE02120300000000202051"), D("119.00"), "Rechnung R-1 vom 01.11.2026", "RC-1"),
        Transfer(Party("Hosting AG", "DE02500105170137075030", "INGDDEFFXXX"), D("50.5"), "R-2", "RC-2")],
        execution_date=dt.date(2026, 11, 16), msg_id="SL-UEB-1", created=NOW)
    root = ET.fromstring(xml)
    ns = {"p": NS_001}
    assert root.find("p:CstmrCdtTrfInitn/p:GrpHdr/p:CtrlSum", ns).text == "169.50"  # type: ignore[union-attr]
    assert root.find(".//p:PmtInf/p:NbOfTxs", ns).text == "2"  # type: ignore[union-attr]
    names = [e.text for e in root.findall(".//p:Cdtr/p:Nm", ns)]
    assert names == ["Mueller + Soehne", "Hosting AG"]
    assert root.find(".//p:DbtrAcct/p:Id/p:IBAN", ns).text == "DE89370400440532013000"  # type: ignore[union-attr]
    assert "Beispiel UG (haftungsbeschraenkt)" in xml
    with pytest.raises(SepaError, match="IBAN"):
        pain001(debtor=ME, transfers=[Transfer(Party("X", "DE00123"), D("1"), "r", "e")], execution_date=dt.date(2026, 11, 16),
                msg_id="m", created=NOW)


def test_pain008_groups_by_sequence() -> None:
    debits = [Debit(Party("Kunde A", "DE02120300000000202051"), D("100"), "Rechnung RE-1", "DD-1", "SL-10001-1", dt.date(2026, 1, 5), "FRST"),
              Debit(Party("Kunde B", "DE02500105170137075030"), D("20"), "Rechnung RE-2", "DD-2", "SL-10002-1", dt.date(2025, 3, 1), "RCUR"),
              Debit(Party("Kunde C", "DE02500105170137075030"), D("30"), "Rechnung RE-3", "DD-3", "SL-10003-1", dt.date(2025, 3, 1), "RCUR")]
    xml = pain008(creditor=ME, creditor_id="DE98ZZZ09999999999", debits=debits, collection_date=dt.date(2026, 11, 17), msg_id="SL-LS-1",
                  created=NOW)
    root = ET.fromstring(xml)
    ns = {"p": NS_008}
    blocks = root.findall(".//p:PmtInf", ns)
    assert [b.find("p:PmtTpInf/p:SeqTp", ns).text for b in blocks] == ["FRST", "RCUR"]  # type: ignore[union-attr]
    assert [b.find("p:CtrlSum", ns).text for b in blocks] == ["100.00", "50.00"]  # type: ignore[union-attr]
    assert root.find(".//p:CdtrSchmeId//p:Othr/p:Id", ns).text == "DE98ZZZ09999999999"  # type: ignore[union-attr]
    with pytest.raises(SepaError, match="Gläubiger"):
        pain008(creditor=ME, creditor_id="DE00ZZZ09999999999", debits=debits, collection_date=dt.date(2026, 11, 17), msg_id="x",
                created=NOW)


def test_helpers() -> None:
    assert creditor_id_ok("DE98ZZZ09999999999") and not creditor_id_ok("DE98ZZZ0999999999X")
    assert sepa_text("Straße <script>", 20) == "Strasse script"
