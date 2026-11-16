"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useFeedback } from "@/components/Feedback";
import { Alert, Badge, Button, Card, Drawer, Empty, Field, Input, PageHeader, PageSkeleton, Select, Table, Textarea } from "@/components/ui";
import { formatEuro, toApiDecimal } from "@/lib/api";
import { api, HttpError } from "@/lib/http";
import { BILLING, TAX_CASES, UNITS, unitLabel, type Product, type ProductInput } from "@/lib/types";

const EMPTY: ProductInput = {
  sku: "",
  name: "",
  description: "",
  unit_code: "MON",
  price: "",
  price_basis: "net",
  tax_case: "OUT_DE_19",
  billing: "monthly",
  active: true,
};

function toGermanDecimal(v: string): string {
  return v.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "").replace(".", ",");
}

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<{ id: string | null; data: ProductInput } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const { toast } = useFeedback();

  const load = useCallback(() => {
    api<Product[]>("GET", `/api/products?include_inactive=${showInactive}`)
      .then((p) => { setProducts(p); setLoaded(true); })
      .catch((e: unknown) => setError(e instanceof HttpError ? e.message : "Laden fehlgeschlagen"));
  }, [showInactive]);
  useEffect(load, [load]);

  const set = <K extends keyof ProductInput>(k: K, v: ProductInput[K]) =>
    setEditing((e) => (e ? { ...e, data: { ...e.data, [k]: v } } : e));

  async function save(ev: FormEvent) {
    ev.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const body = { ...editing.data, price: toApiDecimal(editing.data.price) };
      const saved = editing.id
        ? await api<Product>("PUT", `/api/products/${editing.id}`, body)
        : await api<Product>("POST", "/api/products", body);
      setWarnings(saved.warnings);
      toast(`Produkt „${saved.name}“ gespeichert.`);
      setEditing(null);
      load();
    } catch (err) {
      setError(err instanceof HttpError ? err.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  const d = editing?.data;
  if (!loaded && !error) return <PageSkeleton stats={0} />;
  return (
    <>
      <PageHeader
        title="Produkte"
        description="Eure Softwarelösungen (Miete) und Leistungen (z. B. Stunden für Sonderanfertigungen). Preisänderungen wirken nur auf neue Rechnungen."
        actions={
          <>
            <Button onClick={() => setShowInactive((s) => !s)}>{showInactive ? "Inaktive ausblenden" : "Inaktive zeigen"}</Button>
            <Button variant="primary" onClick={() => { setWarnings([]); setEditing({ id: null, data: { ...EMPTY } }); }}>+ Produkt</Button>
          </>
        }
      />
      {warnings.map((w) => <div key={w} className="mb-4"><Alert kind="warning">{w}</Alert></div>)}
      {error && !editing && <div className="mb-4"><Alert>{error}</Alert></div>}

      <Drawer open={!!editing} onClose={() => { setEditing(null); setError(null); }} size="lg"
        title={editing?.id ? "Produkt bearbeiten" : "Neues Produkt"}
        footer={<><Button onClick={() => { setEditing(null); setError(null); }}>Abbrechen</Button><Button type="submit" form="product-form" variant="primary" disabled={busy}>{busy ? "Speichere …" : "Speichern"}</Button></>}>
      {editing && d && (
          <form id="product-form" onSubmit={save} className="grid gap-4 md:grid-cols-2">
            <Field label="Artikelnummer" hint="Buchstaben, Ziffern, . _ -">
              <Input required pattern="[A-Za-z0-9._\-]{1,40}" value={d.sku} onChange={(e) => set("sku", e.target.value)} />
            </Field>
            <Field label="Bezeichnung">
              <Input required value={d.name} onChange={(e) => set("name", e.target.value)} />
            </Field>
            <Field label="Beschreibung" className="md:col-span-2">
              <Textarea value={d.description ?? ""} onChange={(e) => set("description", e.target.value)} />
            </Field>
            <Field label="Abrechnung">
              <Select value={d.billing} onChange={(e) => set("billing", e.target.value as Product["billing"])}>
                {BILLING.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Einheit">
              <Select value={d.unit_code} onChange={(e) => set("unit_code", e.target.value)}>
                {UNITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Preis (€)">
              <Input required inputMode="decimal" placeholder="0,00" value={d.price} onChange={(e) => set("price", e.target.value)} />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Preis ist">
                <Select value={d.price_basis} onChange={(e) => set("price_basis", e.target.value as Product["price_basis"])}>
                  <option value="net">netto (B2B)</option>
                  <option value="gross">brutto (B2C)</option>
                </Select>
              </Field>
              <Field label="USt">
                <Select value={d.tax_case} onChange={(e) => set("tax_case", e.target.value as Product["tax_case"])}>
                  {TAX_CASES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </Select>
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={d.active} onChange={(e) => set("active", e.target.checked)} />
              Aktiv (auswählbar in Rechnungen)
            </label>
            {error && <div className="md:col-span-2"><Alert>{error}</Alert></div>}
          </form>
      )}
      </Drawer>

      <Card>
        {products.length === 0 ? (
          <Empty>Noch keine Produkte.</Empty>
        ) : (
          <Table
            head={
              <tr>
                <th className="pb-2 font-medium">Art.-Nr.</th>
                <th className="pb-2 font-medium">Bezeichnung</th>
                <th className="pb-2 font-medium">Abrechnung</th>
                <th className="pb-2 text-right font-medium">Preis</th>
                <th className="pb-2 font-medium">USt</th>
                <th className="pb-2" />
              </tr>
            }
          >
            {products.map((p) => (
              <tr key={p.id} className={p.active ? "" : "opacity-60"}>
                <td className="py-2 tabular text-slate-500">{p.sku}</td>
                <td className="py-2 font-medium">
                  {p.name} {p.warnings.length > 0 && <Badge tone="amber">Preis prüfen</Badge>}
                </td>
                <td className="py-2">{BILLING.find(([v]) => v === p.billing)?.[1]} · {unitLabel(p.unit_code)}</td>
                <td className="py-2 text-right tabular">
                  {formatEuro(p.price)} <span className="text-xs text-slate-500">{p.price_basis === "net" ? "netto" : "brutto"}</span>
                </td>
                <td className="py-2">{p.tax_case === "OUT_DE_7" ? "7 %" : "19 %"}</td>
                <td className="py-2 text-right">
                  <Button onClick={() => { setWarnings([]); setEditing({ id: p.id, data: { ...p, price: toGermanDecimal(p.price) } }); }}>Bearbeiten</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
