export type CustomerType = "b2b" | "b2c";

export interface CompanyProfile {
  id: string;
  valid_from: string;
  legal_name: string;
  legal_form: "UG (haftungsbeschränkt)" | "GmbH";
  street: string;
  postal_code: string;
  city: string;
  country_code: string;
  register_court: string | null;
  register_number: string | null;
  managing_directors: string[];
  tax_number: string | null;
  vat_id: string | null;
  iban: string | null;
  bic: string | null;
  bank_name: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  missing_for_invoices: string[];
}

export interface Customer {
  id: string;
  number: string;
  customer_type: CustomerType;
  display_name: string;
  company_name: string | null;
  first_name: string | null;
  last_name: string | null;
  email: string;
  phone: string | null;
  street: string;
  postal_code: string;
  city: string;
  country_code: string;
  vat_id: string | null;
  buyer_reference: string | null;
  payment_terms_days: number;
  notes: string | null;
  archived: boolean;
  currency?: string;
  einvoice_format?: "facturx" | "xrechnung";
  peppol_id?: string | null;
}

export type CustomerInput = Omit<Customer, "id" | "number" | "display_name" | "archived">;

export interface Product {
  id: string;
  sku: string;
  name: string;
  description: string | null;
  unit_code: string;
  price: string;
  price_basis: "net" | "gross";
  tax_case: "OUT_DE_19" | "OUT_DE_7";
  billing: "one_time" | "monthly" | "yearly" | "hourly";
  active: boolean;
  warnings: string[];
}

export type ProductInput = Omit<Product, "id" | "warnings">;

export interface InvoiceLine {
  position?: number;
  product_id: string | null;
  description: string;
  quantity: string;
  unit_code: string;
  unit_price: string;
  tax_case: string;
}

export interface InvoiceSummary {
  id: string;
  kind: "invoice" | "cancellation";
  status: "draft" | "issued";
  number: string | null;
  customer_id: string;
  customer_name: string;
  issue_date: string | null;
  due_date: string | null;
  total_gross: string | null;
  cancelled_by: string | null;
  cancels_invoice_id: string | null;
  created_at: string;
}

export interface Invoice extends InvoiceSummary {
  price_mode: "net" | "gross";
  service_date_from: string | null;
  service_date_to: string | null;
  due_days: number;
  intro_text: string | null;
  closing_text: string | null;
  cancellation_reason: string | null;
  total_net: string | null;
  total_tax: string | null;
  lines: InvoiceLine[];
  snapshot: Record<string, unknown> | null;
  ledger_record_id: string | null;
  ledger_hash: string | null;
  issued_by: string | null;
  issued_at: string | null;
}

export const UNITS: [string, string][] = [
  ["MON", "Monat"],
  ["HUR", "Stunde"],
  ["C62", "Stück"],
  ["LS", "Pauschale"],
  ["DAY", "Tag"],
  ["ANN", "Jahr"],
];

export const TAX_CASES: [string, string][] = [
  ["OUT_DE_19", "19 %"],
  ["OUT_DE_7", "7 %"],
  ["OUT_EU_B2B_SERVICE", "EU-Leistung (Reverse Charge)"],
  ["OUT_EU_B2B_GOODS", "ig. Lieferung (EU)"],
  ["OUT_DRITTLAND_SERVICE", "Drittland (nicht steuerbar)"],
];

export const BILLING: [Product["billing"], string][] = [
  ["monthly", "monatlich"],
  ["yearly", "jährlich"],
  ["one_time", "einmalig"],
  ["hourly", "nach Stunden"],
];

export function unitLabel(code: string): string {
  return UNITS.find(([c]) => c === code)?.[1] ?? code;
}
