"""Serializable calculation recipes. Decimal strings and source identities survive export."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from src.models.financial import FinancialObservation, ReportingPeriod, payload_hash
from src.models.evidence import SourcePassage


class DCFScenario(BaseModel):
    """Analyst assumptions, never represented as historical reported facts."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=12, max_length=2000)
    wacc: Decimal = Field(gt=0, lt=1, allow_inf_nan=False)
    terminal_growth: Decimal = Field(gt=-1, lt=1, allow_inf_nan=False)
    fcff_margin: Decimal = Field(ge=-1, le=1, allow_inf_nan=False)
    projection_years: int = Field(default=5, ge=3, le=10)


    @model_validator(mode="after")
    def valid_spread(self):
        if self.terminal_growth >= self.wacc:
            raise ValueError("Terminal growth must be below WACC")
        return self


class CalculationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str
    operation: str
    inputs: list[str] = Field(default_factory=list)
    value: Decimal = Field(allow_inf_nan=False)
    unit: str
    observation_id: str | None = None
    note: str | None = None


class CalculationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["decimal-28-half-even-v1"] = "decimal-28-half-even-v1"
    recipe: Literal["growth-v1", "net-margin-v1", "ocf-margin-v1", "dcf-fcff-v1", "comps-v1", "ttm-income-v1", "comps-ttm-v1", "q4-v1", "ttm-ytd-v1", "cashflow-difference-v1", "ttm-flex-v1", "comps-flex-v1"]
    ticker: str
    bindings: dict[str, list[str]]
    scenario: DCFScenario | None = None
    valuation_date: date | None = None
    steps: list[CalculationStep]
    outputs: dict[str, str]
    currency: str
    period_methods: dict[str, Literal["quarters", "annual-ytd", "q4-plus-quarters", "cashflow-quarters"]] = Field(default_factory=dict, exclude_if=lambda value: not value)
    periods: dict[str, ReportingPeriod] = Field(default_factory=dict, exclude_if=lambda value: not value)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def retained_identity(cls, data, handler):
        supplied = data.get("calculation_id") if isinstance(data, dict) else None
        if isinstance(data, dict):
            data = {key: value for key, value in data.items() if key != "calculation_id"}
        result = handler(data)
        if supplied is not None and supplied != result.calculation_id:
            raise ValueError("Calculation identity does not match its contents")
        return result

    @computed_field
    @property
    def calculation_id(self) -> str:
        return payload_hash(self.model_dump(mode="json", exclude={"calculation_id"}))


class CalculationAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calculation_id: str
    output: str
    value: Decimal = Field(allow_inf_nan=False)
    unit: str
    currency: str | None


class CalculationSource(BaseModel):
    observation: FinancialObservation
    passages: list[SourcePassage]


class CalculationRecord(BaseModel):
    trace: CalculationTrace
    sources: list[CalculationSource]
