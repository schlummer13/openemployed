"""Formvorschriften vor der elektronischen Signatur (Documenso = einfache/fortgeschrittene elektronische Signatur).

Documenso erfüllt die **Textform** (§ 126b BGB) und Formfreiheit, aber nicht die **Schriftform** (§ 126 BGB). Die Schriftform kann
nur durch eigenhändige Unterschrift oder eine **qualifizierte** elektronische Signatur (§ 126a BGB) ersetzt werden – und bei einigen
Erklärungen ist die elektronische Form ganz ausgeschlossen. Stufen:

- ``ok``: formfrei oder Textform genügt → Signatur über Documenso wirksam.
- ``warn``: Schriftform nötig, elektronisch nur mit qualifizierter Signatur → Versand nur nach ausdrücklicher Bestätigung;
  sonst Papier mit Unterschrift.
- ``block``: elektronische Form gesetzlich ausgeschlossen oder notarielle Beurkundung nötig → kein Versand über Documenso.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FormRule:
    label: str
    level: str          # ok | warn | block
    law: str
    consequence: str


RULES: dict[str, FormRule] = {
    "general": FormRule("Allgemeiner Vertrag (formfrei)", "ok", "§ 125 BGB – grundsätzlich formfrei", ""),
    "service": FormRule("Dienst-/Werk-/Beratungsvertrag", "ok", "formfrei", ""),
    "supplier": FormRule("Liefer-/Rahmenvertrag", "ok", "formfrei", ""),
    "nda": FormRule("Geheimhaltungsvereinbarung (NDA)", "ok", "formfrei", ""),
    "license": FormRule("Lizenz-/Software-/SaaS-Vertrag", "ok", "formfrei", ""),
    "dpa": FormRule("Auftragsverarbeitung (AVV)", "ok", "Art. 28 Abs. 9 DSGVO – elektronisches Format genügt", ""),
    "employment": FormRule("Arbeitsvertrag (unbefristet)", "ok", "Nachweisgesetz § 2 – Textform genügt (seit 1.1.2025)",
                           "Ausnahme: Branchen nach § 2a SchwarzArbG brauchen den Nachweis weiterhin schriftlich."),
    "employment_fixed_term": FormRule("Befristeter Arbeitsvertrag", "warn", "§ 14 Abs. 4 TzBfG – Schriftform",
                                      "Ohne eigenhändige oder qualifizierte elektronische Unterschrift gilt der Vertrag als unbefristet."),
    "amendment": FormRule("Vertragsänderung/Zusatzvereinbarung", "ok", "formfrei, sofern der Vertrag keine Schriftformklausel hat", ""),
    "non_compete": FormRule("Nachvertragliches Wettbewerbsverbot", "warn", "§ 74 Abs. 1 HGB – Schriftform und Aushändigung",
                            "Ohne Schriftform ist das Wettbewerbsverbot nichtig."),
    "lease": FormRule("Miet-/Pachtvertrag über mehr als ein Jahr", "warn", "§ 550, § 578 BGB – Schriftform",
                      "Ohne Schriftform gilt der Vertrag als auf unbestimmte Zeit geschlossen und ist nach einem Jahr kündbar."),
    "consumer_loan": FormRule("Verbraucherdarlehen", "warn", "§ 492 BGB – Schriftform", "Ohne Schriftform nichtig bzw. Zinsermäßigung."),
    "termination": FormRule("Kündigung/Aufhebungsvertrag (Arbeitsverhältnis)", "block", "§ 623 BGB – elektronische Form ausgeschlossen",
                            "Nur auf Papier mit eigenhändiger Unterschrift wirksam; Original übergeben bzw. zustellen."),
    "guarantee": FormRule("Bürgschaft (privat)", "block", "§ 766 BGB – elektronische Form ausgeschlossen", "Nur auf Papier wirksam."),
    "acknowledgment": FormRule("Schuldversprechen/-anerkenntnis", "block", "§§ 780, 781 BGB – elektronische Form ausgeschlossen",
                               "Nur auf Papier wirksam (Ausnahme: Kaufleute, § 350 HGB)."),
    "real_estate": FormRule("Grundstücksgeschäft", "block", "§ 311b BGB – notarielle Beurkundung", "Nur beim Notar."),
    "shares": FormRule("Abtretung von GmbH-Anteilen", "block", "§ 15 GmbHG – notarielle Beurkundung", "Nur beim Notar."),
}

KEYWORDS: tuple[tuple[str, str], ...] = (
    (r"kündigung|aufhebungsvertrag|auflösungsvertrag", "termination"),
    (r"wettbewerbsverbot", "non_compete"),
    (r"bürgschaft", "guarantee"),
    (r"schuldanerkenntnis|schuldversprechen", "acknowledgment"),
    (r"grundstück|kaufvertrag.*immobil|notar", "real_estate"),
    (r"geschäftsanteil|anteilsabtretung", "shares"),
    (r"(?<!un)befrist", "employment_fixed_term"),  # nicht „unbefristet“
    (r"mietvertrag|pachtvertrag|gewerbemiet", "lease"),
)


def detect(title: str) -> str | None:
    t = title.lower()
    for pattern, kind in KEYWORDS:
        if re.search(pattern, t):
            return kind
    return None


@dataclass(frozen=True, slots=True)
class FormCheck:
    kind: str
    level: str
    label: str
    message: str


def check(kind: str, title: str = "") -> FormCheck:
    """Strengste Regel aus gewählter Art und Titel (z. B. „Aufhebungsvertrag“ im Titel überstimmt „Allgemein“)."""
    order = {"ok": 0, "warn": 1, "block": 2}
    candidates = [k for k in (kind if kind in RULES else "general", detect(title)) if k]
    worst = max(candidates, key=lambda k: order[RULES[k].level])
    r = RULES[worst]
    msg = f"{r.label}: {r.law}." + (f" {r.consequence}" if r.consequence else "")
    return FormCheck(worst, r.level, r.label, msg)


def options() -> list[dict[str, str]]:
    return [{"key": k, "label": r.label, "level": r.level, "law": r.law} for k, r in RULES.items()]
