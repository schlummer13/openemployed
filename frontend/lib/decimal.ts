// Exakte Dezimalrechnung für Preisvorschläge (BigInt, keine Fließkommazahlen).

const SCALE = 4;
const FACTOR = 10n ** BigInt(SCALE);

/** "1758.5" → 17585000n (4 Nachkommastellen) */
export function toScaled(value: string): bigint {
  const m = /^(-)?(\d+)(?:\.(\d{1,4}))?$/.exec(value.trim());
  if (!m) throw new Error(`Ungültiger Betrag: ${value}`);
  const [, sign, int = "0", frac = ""] = m;
  const scaled = BigInt(int) * FACTOR + BigInt(frac.padEnd(SCALE, "0"));
  return sign ? -scaled : scaled;
}

export function fromScaled(value: bigint, places = SCALE): string {
  const negative = value < 0n;
  const abs = negative ? -value : value;
  const int = abs / FACTOR;
  const frac = (abs % FACTOR).toString().padStart(SCALE, "0").slice(0, places);
  return `${negative ? "-" : ""}${int}${places > 0 ? "." + frac : ""}`;
}

/** Ganzzahlige Division mit kaufmännischer Rundung (half-up, symmetrisch). */
function divRound(n: bigint, d: bigint): bigint {
  const negative = (n < 0n) !== (d < 0n);
  const an = n < 0n ? -n : n;
  const ad = d < 0n ? -d : d;
  const q = (an * 2n + ad) / (ad * 2n);
  return negative ? -q : q;
}

/** Netto → Brutto (2 Nachkommastellen) bzw. Brutto → Netto (4 Nachkommastellen) bei gegebenem Satz in Prozent. */
export function convertPrice(price: string, ratePercent: number, from: "net" | "gross", to: "net" | "gross"): string {
  if (from === to) return fromScaled(toScaled(price));
  const p = toScaled(price);
  const rate = BigInt(100 + ratePercent);
  if (from === "net") {
    // brutto auf Cent: p * (100+r) / 100, gerundet auf 0,01 → in 4er-Skala: Vielfache von 100
    const gross = divRound(p * rate, 100n * 100n) * 100n;
    return fromScaled(gross, 2);
  }
  return fromScaled(divRound(p * 100n, rate));
}

export function rateOf(taxCase: string): number {
  return taxCase === "OUT_DE_7" ? 7 : 19;
}
