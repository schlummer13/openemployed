"""ELSTER-Datensatz der UStVA (ohne ERiC)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.domain.elster import ElsterError, elster_tax_number, ustva_xml, zeitraum

NS = {"e": "http://www.elster.de/elsterxml/schema/v11", "u": "http://finkonsens.de/elster/elsteranmeldung/ustva/v2026"}


def test_tax_number_conversion() -> None:
    assert elster_tax_number("103/5734/0815", "NW") == "5103057340815"
    assert elster_tax_number("5103057340815", "NW") == "5103057340815", "bereits im ELSTER-Format"
    assert elster_tax_number("93815/08152", "BW") == "2893081508152"
    assert elster_tax_number("181/815/08155", "BY") == "9181081508155"
    with pytest.raises(ElsterError, match="11 Ziffern"):
        elster_tax_number("12/345", "NW")


def test_zeitraum() -> None:
    assert zeitraum("2026-11") == ("2026", "11") and zeitraum("2026-Q4") == ("2026", "44")
    with pytest.raises(ElsterError):
        zeitraum("2026-13")


def test_ustva_xml_amounts_and_test_marker() -> None:
    lines = [{"kz": "81", "base": Decimal("1758"), "tax": Decimal("334.02")}, {"kz": "66", "tax": Decimal("8.55")},
             {"kz": "46", "base": Decimal("300")}, {"kz": "47", "tax": Decimal("57.00")}, {"kz": "67", "tax": Decimal("57.00")},
             {"kz": "83", "tax": Decimal("325.47")}]
    xml = ustva_xml(spec="2026-11", lines=lines, tax_number="103/5734/0815", state="NW",  # type: ignore[arg-type]
                    company={"name": "Beispiel Software UG (haftungsbeschränkt) & Co", "street": "Königsallee 1", "postal_code": "40212",
                             "city": "Düsseldorf"}, hersteller_id="12345", test=True, created=dt.date(2026, 11, 15))
    root = ET.fromstring(xml)
    assert root.find("e:TransferHeader/e:Testmerker", NS).text == "700000004"  # type: ignore[union-attr]
    assert root.find(".//e:Empfaenger", NS).text == "5103"  # type: ignore[union-attr]
    u = root.find(".//u:Umsatzsteuervoranmeldung", NS)
    assert u is not None
    vals = {child.tag.split("}")[1]: child.text for child in u}
    assert vals["Steuernummer"] == "5103057340815" and vals["Zeitraum"] == "11" and vals["Jahr"] == "2026"
    assert vals["Kz81"] == "1758" and vals["Kz66"] == "8.55" and vals["Kz46"] == "300" and vals["Kz83"] == "325.47"
    assert "&amp; Co" in xml, "Sonderzeichen werden maskiert"
    prod = ustva_xml(spec="2026-Q4", lines=lines, tax_number="5103057340815", state="NW", company={"name": "X", "street": "", "postal_code": "",  # type: ignore[arg-type]
                                                                                                     "city": ""}, hersteller_id="1", test=False, created=dt.date(2026, 1, 1))
    assert "Testmerker" not in prod and "<Zeitraum>44</Zeitraum>" in prod
