"""Handbuch: Kapitel aus app/help/*.md, intelligente Suche und Antworten der lokalen KI auf Basis des Handbuchs.

Suche ohne externe Dienste: deutsche Normalisierung (Umlaute, Groß/Klein), einfache Stammformbildung, Synonyme,
Präfix- und Tippfehlertoleranz (Editierdistanz 1–2), Gewichtung nach Überschrift/Kapitel/Text und Treffer-Nähe.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HELP_DIR = Path(__file__).resolve().parent.parent / "help"
STOP = frozenset([
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "und", "oder", "ich", "wie",
    "was", "wo", "wann", "warum", "wer", "mit", "von", "zu", "im", "in", "am", "an", "auf", "für", "ist", "sind", "wird",
    "werden", "kann", "können", "man", "mein", "meine", "mir", "mich", "bei", "nach", "über", "unter", "aus", "als",
    "auch", "nicht", "nur", "noch", "so", "es", "sie", "wir", "ihr", "um", "dass", "da",
])
SYNONYMS: dict[str, tuple[str, ...]] = {
    "gehalt": ("lohn", "entgelt", "gehaelter", "lohnlauf"), "lohn": ("gehalt", "entgelt", "lohnlauf"), "entgelt": ("lohn", "gehalt"),
    "mitarbeiter": ("angestellte", "personal", "arbeitnehmer"), "rechnung": ("faktura", "ausgangsrechnung", "erechnung"),
    "erechnung": ("xrechnung", "zugferd", "rechnung", "peppol"), "beleg": ("eingangsrechnung", "quittung", "kassenbon"),
    "ueberweisung": ("sepa", "zahlung"), "lastschrift": ("sepa", "mandat", "einzug"), "steuerberater": ("kanzlei", "steuerbuero"),
    "kanzlei": ("steuerberater",), "workflow": ("automatisierung", "ablauf", "regel"), "automatisierung": ("workflow", "job"),
    "abzweigung": ("wenn", "sonst", "bedingung"), "bedingung": ("wenn", "abzweigung"), "passwort": ("kennwort", "anmeldung"),
    "login": ("anmeldung",), "anmeldung": ("login", "passwort"), "zweifaktor": ("2fa", "totp", "authenticator"),
    "2fa": ("zweifaktor", "totp"),
    "kunde": ("kunden", "auftraggeber"), "lead": ("pipeline", "vertrieb", "verkaufschance"), "pipeline": ("lead", "vertrieb"),
    "backup": ("sicherung", "datensicherung"), "sicherung": ("backup",), "storno": ("stornieren", "korrektur"),
    "mahnung": ("mahnlauf", "erinnerung"),
    "waehrung": ("fremdwaehrung", "kurs", "ezb"), "ausland": ("eu", "reverse", "drittland"),
    "ust": ("umsatzsteuer", "ustva", "mehrwertsteuer"),
    "mwst": ("umsatzsteuer", "ust"), "umsatzsteuer": ("ustva", "ust", "voranmeldung"), "api": ("schnittstelle", "rest", "webhook"),
    "import": ("umstieg", "datev", "eroeffnungsbilanz"), "lexoffice": ("umstieg", "import"), "sevdesk": ("umstieg", "import"),
    "archiv": ("aufbewahrung", "dokument"), "firma": ("mandant", "firmen"), "mandant": ("firma",), "zeit": ("zeiterfassung", "stunden"),
    "stempeluhr": ("stempeln", "einstempeln", "arbeitszeit", "check"), "stempeln": ("stempeluhr", "einstempeln"),
    "einstempeln": ("stempeluhr", "check"), "urlaub": ("abwesenheit", "urlaubsantrag", "urlaubskonto"),
    "abwesenheit": ("urlaub", "krankheit"), "krank": ("krankmeldung", "krankheit", "abwesenheit"),
    "krankmeldung": ("krankheit", "abwesenheit"), "rolle": ("rechte", "berechtigung"), "rechte": ("rolle", "berechtigung"),
    "berechtigung": ("rolle", "rechte"), "pause": ("arbeitszeitgesetz", "arbzg"),
    "feiertag": ("feiertage", "brueckentag", "frei"), "brueckentag": ("feiertag", "urlaub"), "ical": ("kalender", "ics", "outlook"),
    "kalender": ("ical", "team", "feiertag"), "heiligabend": ("betrieblich", "feiertag"), "rosenmontag": ("betrieblich", "feiertag"),
}
SUFFIXES = ("ungen", "ung", "en", "er", "es", "e", "n", "s")


def fold(text: str) -> str:
    t = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


def stem(token: str) -> str:
    for suf in SUFFIXES:
        if len(token) > len(suf) + 3 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", fold(text)) if t not in STOP and len(t) > 1]


def _edit_distance(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


@dataclass
class Section:
    chapter: str
    chapter_title: str
    anchor: str
    title: str
    text: str
    terms: dict[str, int] = field(default_factory=dict)
    title_terms: set[str] = field(default_factory=set)


@dataclass
class Chapter:
    slug: str
    title: str
    summary: str
    markdown: str
    sections: list[Section]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


@functools.lru_cache(maxsize=1)
def chapters() -> tuple[Chapter, ...]:
    out = []
    for path in sorted(HELP_DIR.glob("*.md")):
        md = path.read_text(encoding="utf-8")
        lines = md.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        summary = next((ln[1:].strip() for ln in lines[1:4] if ln.startswith(">")), "")
        slug = re.sub(r"^\d+-", "", path.stem)
        sections: list[Section] = []
        current = Section(slug, title, "", title, summary)
        for ln in lines[1:]:
            if ln.startswith("## "):
                sections.append(current)
                head = ln[3:].strip()
                current = Section(slug, title, slugify(head), head, "")
            else:
                current.text += ln + "\n"
        sections.append(current)
        for s in sections:
            for t in tokens(s.text):
                st = stem(t)
                s.terms[st] = s.terms.get(st, 0) + 1
            s.title_terms = {stem(t) for t in tokens(s.title)} | {stem(t) for t in tokens(title)}
        out.append(Chapter(slug, title, summary, md, sections))
    return tuple(out)


def _expand(query: str) -> list[tuple[str, float]]:
    """Suchbegriffe mit Gewicht: Originalbegriff 1.0, Synonyme 0.6."""
    out: list[tuple[str, float]] = []
    for t in tokens(query):
        out.append((stem(t), 1.0))
        for syn in SYNONYMS.get(t, SYNONYMS.get(stem(t), ())):
            out.append((stem(syn), 0.6))
    return out


def _match(term: str, vocab: dict[str, int] | set[str]) -> tuple[float, str | None]:
    if term in vocab:
        return 1.0, term
    best: tuple[float, str | None] = (0.0, None)
    for word in vocab:
        if len(term) >= 3 and word.startswith(term):
            score = 0.8
        elif len(term) >= 5 and term in word:
            score = 0.6
        elif len(term) >= 5 and _edit_distance(term, word, 2 if len(term) >= 8 else 1) <= (2 if len(term) >= 8 else 1):
            score = 0.55
        else:
            continue
        if score > best[0]:
            best = (score, word)
    return best


def search(query: str, limit: int = 12) -> list[dict[str, Any]]:
    return [dict(r) for r in _search_cached(" ".join(fold(query).split()), limit)]


@functools.lru_cache(maxsize=2048)
def _search_cached(query: str, limit: int) -> tuple[Any, ...]:
    return tuple(_search(query, limit))


def _search(query: str, limit: int = 12) -> list[dict[str, Any]]:
    terms = _expand(query)
    if not terms:
        return []
    originals = {t for t, w in terms if w == 1.0}
    results: list[dict[str, Any]] = []
    for ch in chapters():
        for s in ch.sections:
            score, hit_originals, hits = 0.0, set[str](), list[str]()
            for term, weight in terms:
                q, _ = _match(term, s.title_terms)
                if q:
                    score += 6 * q * weight
                    if weight == 1.0:
                        hit_originals.add(term)
                q2, word2 = _match(term, s.terms)
                if q2 and word2:
                    score += min(3.0, 1 + 0.4 * s.terms[word2]) * q2 * weight
                    hits.append(word2)
                    if weight == 1.0:
                        hit_originals.add(term)
            if not score:
                continue
            coverage = len(hit_originals) / max(1, len(originals))
            score *= 0.4 + coverage  # alle Suchbegriffe getroffen = deutlich besser
            results.append({"chapter": ch.slug, "chapter_title": ch.title, "anchor": s.anchor, "title": s.title, "score": round(score, 2),
                            "snippet": _snippet(s.text or ch.summary, hits)})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


def _snippet(text: str, hits: list[str], width: int = 220) -> str:
    plain = re.sub(r"`|\*\*|^#+\s|^>\s|\[|\]\([^)]*\)", "", text, flags=re.M)
    plain = re.sub(r"\s+", " ", plain).strip()
    folded = fold(plain)
    pos = min((folded.find(h) for h in hits if folded.find(h) >= 0), default=0)
    start = max(0, pos - 60)
    piece = plain[start:start + width]
    for h in sorted(set(hits), key=len, reverse=True):
        if len(h) < 3:
            continue
        piece = re.sub(rf"(?i)(\b\w*{re.escape(h)}\w*)", r"«\1»", piece, count=2) if h.isascii() else piece
    return ("… " if start else "") + piece + (" …" if start + width < len(plain) else "")


def chapter(slug: str) -> Chapter | None:
    return next((c for c in chapters() if c.slug == slug), None)


def toc() -> list[dict[str, Any]]:
    return [{"slug": c.slug, "title": c.title, "summary": c.summary,
             "sections": [{"anchor": s.anchor, "title": s.title} for s in c.sections if s.anchor]} for c in chapters()]


ASK_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 2000},
                                               "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 4}},
              "required": ["answer", "sources"]}


async def ask(provider: Any, question: str) -> dict[str, Any]:
    """Antwort der KI ausschließlich aus den passendsten Handbuchabschnitten (mit Quellen)."""
    hits = search(question, limit=5)
    if not hits:
        return {"answer": None, "sources": [], "hits": []}
    context = []
    for h in hits[:4]:
        ch = chapter(h["chapter"])
        sec = next((s for s in ch.sections if s.anchor == h["anchor"]), None) if ch else None
        if sec:
            context.append(f"[{h['chapter']}#{h['anchor']}] {h['chapter_title']} – {sec.title}\n{sec.text.strip()[:2500]}")
    if provider is None:
        return {"answer": None, "sources": [], "hits": hits}
    result = await provider.complete_json(
        system="Du bist die Hilfe der Software OpenEmployed. Beantworte die Frage kurz und konkret auf Deutsch, ausschließlich mit den "
               "Informationen aus den Handbuch-Auszügen. Nenne Menüwege so, wie sie im Text stehen. Wenn die Auszüge die Frage nicht "
               "beantworten, sage das ehrlich. Gib in sources die Kennungen in eckigen Klammern der genutzten Auszüge an.",
        text=f"Frage: {question}\n\nHandbuch-Auszüge:\n\n" + "\n\n---\n\n".join(context), file=None, schema=ASK_SCHEMA)
    valid = {f"{h['chapter']}#{h['anchor']}" for h in hits}
    sources = [s.strip("[] ") for s in result.get("sources", []) if s.strip("[] ") in valid]
    return {"answer": str(result.get("answer") or "").strip() or None, "sources": sources, "hits": hits}
