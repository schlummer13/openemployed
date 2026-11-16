"""Erzeugt aus dem amtlichen Lohnsteuer-PAP (XML des ITZBund, www.bmf-steuerrechner.de) ein Python-Modul.

Aufruf (jährlich nach Veröffentlichung des neuen PAP):
    python tools/pap_codegen.py app/domain/pap/Lohnsteuer2027.xml > app/domain/pap/lst2027.py

Die Ausdrücke werden 1:1 übernommen; nur Bezeichner werden auf ``self.`` umgestellt und Java-Operatoren (&&, ||) ersetzt.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET

TOKEN = re.compile(r"(?<![.\w])([A-Za-z_][A-Za-z0-9_]*)\b")


def convert(expr: str, names: set[str]) -> str:
    e = expr.strip().rstrip(";")
    e = e.replace("&&", " and ").replace("||", " or ")
    e = re.sub(r"!(?!=)", " not ", e)
    e = re.sub(r"\btrue\b", "True", e)
    e = re.sub(r"\bfalse\b", "False", e)
    return TOKEN.sub(lambda m: f"self.{m.group(1)}" if m.group(1) in names else m.group(1), e)


def default(var: ET.Element) -> str:
    d = var.get("default") or var.get("defaul")  # Tippfehler im PAP 2026 (VJAHR)
    t = var.get("type", "")
    if d:
        return d.replace("BigDecimal.ZERO", "BigDecimal.ZERO")
    return "BigDecimal.ZERO" if t.startswith("BigDecimal") else ("0.0" if t == "double" else "0")


def main(path: str) -> None:
    root = ET.fromstring(open(path, encoding="utf-8-sig").read())  # noqa: SIM115, S314 – amtliche Datei des BMF
    name = root.get("name", "Lohnsteuer")
    variables = {"INPUT": [], "OUTPUT": [], "INTERNAL": []}  # type: dict[str, list[ET.Element]]
    for kind in variables:
        variables[kind] = list(root.iter(kind))
    constants = list(root.iter("CONSTANT"))
    names = {v.get("name", "") for vs in variables.values() for v in vs} | {c.get("name", "") for c in constants}
    out = [f'"""Lohnsteuerberechnung nach dem amtlichen Programmablaufplan {name} (automatisch erzeugt aus {path.split("/")[-1]}).',
           "",
           "NICHT VON HAND ÄNDERN – neu erzeugen mit tools/pap_codegen.py.",
           '"""',
           "",
           "# ruff: noqa",
           "# mypy: ignore-errors",
           "from app.domain.pap.runtime import BD, BigDecimal",
           "",
           "",
           f"class {name}:",
           f"    INPUTS = {sorted(v.get('name') for v in variables['INPUT'])!r}",
           f"    OUTPUTS = {sorted(v.get('name') for v in variables['OUTPUT'])!r}",
           ""]
    for c in constants:
        value = c.get("value", "")
        if value.startswith("{"):
            items = [convert(x, set()) for x in value.strip("{} ").split(",")]
            value = "[" + ", ".join(items) + "]"
        out.append(f"    {c.get('name')} = {convert(value, set())}")
    out += ["", "    def __init__(self, **inputs):"]
    for kind in ("INPUT", "OUTPUT", "INTERNAL"):
        for v in variables[kind]:
            out.append(f"        self.{v.get('name')} = {convert(default(v), set())}")
    out += ["        for k, v in inputs.items():",
            "            if k not in self.INPUTS:",
            "                raise ValueError(f'Unbekannter PAP-Eingabeparameter: {k}')",
            "            setattr(self, k, BigDecimal.valueOf(v) if isinstance(getattr(self, k), BD) else v)",
            ""]

    def body(node: ET.Element, indent: int) -> list[str]:
        lines: list[str] = []
        pad = " " * indent
        for el in node:
            if el.tag == "EVAL":
                target, _, expr = el.get("exec", "").partition("=")
                lines.append(f"{pad}self.{target.strip()} = {convert(expr, names)}")
            elif el.tag == "EXECUTE":
                lines.append(f"{pad}self.{el.get('method')}()")
            elif el.tag == "IF":
                lines.append(f"{pad}if {convert(el.get('expr', ''), names)}:")
                then = el.find("THEN")
                inner = body(then, indent + 4) if then is not None else []
                lines += inner or [f"{pad}    pass"]
                other = el.find("ELSE")
                if other is not None:
                    lines.append(f"{pad}else:")
                    lines += body(other, indent + 4) or [f"{pad}    pass"]
        return lines

    methods = root.find("METHODS")
    main_node = methods.find("MAIN") if methods is not None else None
    if methods is None or main_node is None:
        raise SystemExit("PAP ohne METHODS/MAIN")
    out += ["    def calculate(self):"] + (body(main_node, 8) or ["        pass"]) + ["        return self", ""]
    for m in methods.findall("METHOD"):
        out += [f"    def {m.get('name')}(self):"] + (body(m, 8) or ["        pass"]) + [""]
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
