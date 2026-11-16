from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import MoneyOut, StrictDecimal
from app.domain.invoice_calc import PriceMode


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LineIn(StrictModel):
    description: str = Field(min_length=1, max_length=1000)
    quantity: StrictDecimal
    unit_code: str = Field(default="C62", min_length=1, max_length=3)
    unit_price: StrictDecimal
    tax_case: str = Field(default="OUT_DE_19", max_length=40)


class CalculateRequest(StrictModel):
    price_mode: PriceMode = PriceMode.NET
    lines: list[LineIn] = Field(min_length=1, max_length=500)


class LineOut(BaseModel):
    position: int
    description: str
    quantity: MoneyOut
    unit_code: str
    unit_price_net: MoneyOut
    unit_price_gross: MoneyOut | None
    line_net: MoneyOut
    line_gross: MoneyOut | None
    tax_case: str
    category: str
    rate: MoneyOut


class TaxBreakdownOut(BaseModel):
    category: str
    rate: MoneyOut
    taxable_amount: MoneyOut
    tax_amount: MoneyOut
    tax_cases: list[str]
    exemption_reason: str | None
    vatex: str | None


class CalculateResponse(BaseModel):
    price_mode: PriceMode
    lines: list[LineOut]
    tax_breakdown: list[TaxBreakdownOut]
    total_net: MoneyOut
    total_tax: MoneyOut
    total_gross: MoneyOut
    gross_target: MoneyOut | None
    gross_deviation: MoneyOut
    warnings: list[str]


class TaxCaseOut(BaseModel):
    code: str
    direction: str
    label: str
    rate: MoneyOut
    category: str
    reverse_charge: bool
    invoice_note: str | None
    ustva_base: str | None
    ustva_tax: str | None
    ustva_input_tax: str | None
    vat_deductible: bool


class GrossCheckOut(BaseModel):
    gross: MoneyOut
    rate: MoneyOut
    representable: bool
    net: MoneyOut
    tax: MoneyOut
    nearest_lower_gross: MoneyOut

