"""Prüfung der E-Rechnung vor der Ablage.

1. XSD (Struktur) – Factur-X-Schema EN 16931
2. Schematron (Geschäftsregeln EN 16931, z. B. BR-CO-10 „Summe Positionen = Gesamtnetto“) – offizielle,
   kompilierte Regeln aus dem factur-x-Paket, ausgeführt mit Saxon-HE (XSLT 2/3)

Schlägt eine Prüfung mit Fehler (flag="fatal" bzw. ohne flag) fehl, wird die Rechnung nicht abgelegt.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import facturx
from lxml import etree

SVRL = "http://purl.oclc.org/dsdl/svrl"


@dataclass(frozen=True, slots=True)
class RuleViolation:
    rule_id: str
    flag: str
    text: str
    location: str


class EInvoiceValidationError(ValueError):
    def __init__(self, violations: list[RuleViolation] | list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(str(v) for v in violations[:5]))


@lru_cache(maxsize=1)
def _xslt_path() -> Path:
    path = Path(facturx.__file__).parent / "xsd_and_schematron" / "facturx-en16931" / "FACTUR-X_EN16931.xslt"
    if not path.is_file():
        raise RuntimeError(f"Schematron-Regeln nicht gefunden: {path}")
    return path


_lock = threading.Lock()
_compiled: dict[str, object] = {}


def _executable() -> object:
    # Saxon-Prozessor ist nicht threadsicher bei der Kompilierung → einmalig unter Sperre
    with _lock:
        if "exe" not in _compiled:
            from saxonche import PySaxonProcessor

            proc = PySaxonProcessor(license=False)
            xslt = proc.new_xslt30_processor()
            _compiled["proc"] = proc
            _compiled["exe"] = xslt.compile_stylesheet(stylesheet_file=str(_xslt_path()))
        return _compiled["exe"]


def check_schematron(xml: bytes) -> list[RuleViolation]:
    exe = _executable()
    proc = _compiled["proc"]
    with _lock:
        node = proc.parse_xml(xml_text=xml.decode("utf-8"))  # type: ignore[attr-defined]
        svrl_text: str = exe.transform_to_string(xdm_node=node)  # type: ignore[attr-defined]
    report = etree.fromstring(svrl_text.encode("utf-8"))
    violations: list[RuleViolation] = []
    for failed in report.iter(f"{{{SVRL}}}failed-assert"):
        text_node = failed.find(f"{{{SVRL}}}text")
        violations.append(
            RuleViolation(
                rule_id=failed.get("id", ""),
                flag=failed.get("flag", "fatal"),
                text=" ".join((text_node.text or "").split()) if text_node is not None else "",
                location=failed.get("location", ""),
            )
        )
    return violations


def validate_einvoice(xml: bytes) -> list[RuleViolation]:
    """Wirft EInvoiceValidationError bei Fehlern; gibt Warnungen zurück."""
    try:
        facturx.xml_check_xsd(xml, flavor="factur-x", level="en16931")
    except Exception as exc:  # facturx wirft verschiedene Typen
        raise EInvoiceValidationError([f"XSD: {exc}"]) from exc
    violations = check_schematron(xml)
    errors = [v for v in violations if v.flag in ("fatal", "error")]
    if errors:
        raise EInvoiceValidationError(errors)
    return [v for v in violations if v not in errors]
