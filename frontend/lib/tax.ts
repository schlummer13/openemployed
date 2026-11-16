import { api } from "@/lib/http";

export interface MonthRow {
  month: string;
  revenue: string;
  output_tax: string;
  expense: string;
  deductible: string;
  input_tax: string;
  rc_tax: string;
  profit: string;
  vat_payable: string;
}

export interface Overview {
  year: number;
  months: MonthRow[];
  totals: Omit<MonthRow, "month">;
  income_tax: {
    profit_before_tax: string;
    taxable_income: string;
    kst_rate: string;
    kst: string;
    solz: string;
    gewst_base: string;
    gewst: string;
    total: string;
    profit_after_tax: string;
    ug_reserve: string;
    trade_tax_multiplier: number;
  };
  reserve_total: string;
}

export interface VatLine {
  kz: string;
  label: string;
  base?: string;
  tax?: string;
  tax_exact?: string;
  rounding_difference?: string;
  items: { type: "invoice" | "receipt"; id: string; ref: string; date: string; base: string; tax: string }[];
}

export interface VatReturn {
  period: string;
  months: string[];
  lines: VatLine[];
  output_tax: string;
  input_tax: string;
  payable: string;
  locked: boolean;
  closable: boolean;
}

export interface Deadline {
  due: string;
  kind: string;
  label: string;
  period: string | null;
  done: boolean;
  overdue: boolean;
  active: boolean;
}

export interface Calendar {
  filing: "monthly" | "quarterly";
  extension: boolean;
  deadlines: Deadline[];
}

export const getOverview = (year: number) => api<Overview>("GET", `/api/tax/overview/${year}`);
export const getVat = (spec: string) => api<VatReturn>("GET", `/api/tax/vat/${spec}`);
export const lockVat = (spec: string, confirmOpenItems = false) =>
  api<{ ledger_hash: string }>("POST", `/api/tax/vat/${spec}/lock${confirmOpenItems ? "?confirm_open_items=true" : ""}`);
export const getCalendar = (year: number) => api<Calendar>("GET", `/api/tax/calendar/${year}`);

const MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
export function monthLabel(m: string): string {
  return `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
}

export function periodLabel(spec: string): string {
  return spec.includes("-Q") ? `${spec.slice(6)}. Quartal ${spec.slice(0, 4)}` : monthLabel(spec);
}
