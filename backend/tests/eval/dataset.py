"""Hand-curated eval dataset for AlphaAnalyst.

Each ticker carries one or two ground-truth facts taken directly from the
company's most recently filed Form 10-K. Each entry cites the filing item
where the number appears; if a value here ever drifts from the source 10-K,
fix the dataset, not the pipeline.

These figures are commonly cross-checked against multiple data vendors
(Polygon, FMP, sec-api). A ±5% tolerance is applied at compare time to
allow for memo prose that quotes rounded figures.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class GroundTruth:
    """One row of the eval set.

    revenue_usd: total reported revenue / net sales for `fiscal_year_end`,
        in absolute USD (not in millions).
    fiscal_year_end: ISO date of the period end on the 10-K cover page.
    source_note: where to verify the number on the original filing.
    """

    ticker: str
    company_name: str
    fiscal_year_end: str
    revenue_usd: Decimal
    source_note: str


# Tolerances applied at compare time.
REVENUE_TOLERANCE: Decimal = Decimal("0.05")  # 5% relative
DATE_TOLERANCE_DAYS: int = 7  # 10-Ks sometimes refer to "fiscal year ended..."


# All revenue figures are total net sales / total revenues per Item 8 of the
# referenced 10-K's consolidated income statement. If you re-curate, follow
# that convention so segment / "operating revenue" alternatives don't sneak
# in.
EVAL_TICKERS: list[GroundTruth] = [
    GroundTruth(
        ticker="AAPL",
        company_name="Apple Inc.",
        fiscal_year_end="2024-09-28",
        revenue_usd=Decimal("391035000000"),
        source_note=(
            "Apple Inc. 10-K filed 2024-11-01 (FY2024), "
            "Item 8 Consolidated Statements of Operations: total net sales."
        ),
    ),
    GroundTruth(
        ticker="MSFT",
        company_name="Microsoft Corporation",
        fiscal_year_end="2024-06-30",
        revenue_usd=Decimal("245122000000"),
        source_note=(
            "Microsoft Corp. 10-K filed 2024-07-30 (FY2024), "
            "Item 8: total revenue."
        ),
    ),
    GroundTruth(
        ticker="GOOGL",
        company_name="Alphabet Inc.",
        fiscal_year_end="2024-12-31",
        revenue_usd=Decimal("350018000000"),
        source_note=(
            "Alphabet Inc. 10-K filed 2025-02 (FY2024), "
            "Item 8: total revenues."
        ),
    ),
    GroundTruth(
        ticker="AMZN",
        company_name="Amazon.com, Inc.",
        fiscal_year_end="2024-12-31",
        revenue_usd=Decimal("637959000000"),
        source_note=(
            "Amazon.com, Inc. 10-K filed 2025-02 (FY2024), "
            "Item 8: net sales."
        ),
    ),
    GroundTruth(
        ticker="META",
        company_name="Meta Platforms, Inc.",
        fiscal_year_end="2024-12-31",
        revenue_usd=Decimal("164501000000"),
        source_note=(
            "Meta Platforms, Inc. 10-K filed 2025-01 (FY2024), "
            "Item 8: total revenue."
        ),
    ),
    GroundTruth(
        ticker="TSLA",
        company_name="Tesla, Inc.",
        fiscal_year_end="2024-12-31",
        revenue_usd=Decimal("97690000000"),
        source_note=(
            "Tesla, Inc. 10-K filed 2025-01 (FY2024), "
            "Item 8: total revenues."
        ),
    ),
    GroundTruth(
        ticker="NVDA",
        company_name="NVIDIA Corporation",
        # NVDA fiscal year is non-calendar; FY2025 ended Jan 26, 2025.
        fiscal_year_end="2025-01-26",
        revenue_usd=Decimal("130497000000"),
        source_note=(
            "NVIDIA Corporation 10-K filed 2025-02 (FY2025), "
            "Item 8: revenue."
        ),
    ),
    GroundTruth(
        ticker="NFLX",
        company_name="Netflix, Inc.",
        fiscal_year_end="2024-12-31",
        revenue_usd=Decimal("39001000000"),
        source_note=(
            "Netflix, Inc. 10-K filed 2025-01 (FY2024), "
            "Item 8: revenues."
        ),
    ),
    GroundTruth(
        ticker="WMT",
        company_name="Walmart Inc.",
        # Walmart fiscal year is non-calendar; FY2025 ended Jan 31, 2025.
        fiscal_year_end="2025-01-31",
        revenue_usd=Decimal("680985000000"),
        source_note=(
            "Walmart Inc. 10-K filed 2025-03 (FY2025), "
            "Item 8: total revenues."
        ),
    ),
    GroundTruth(
        ticker="JPM",
        company_name="JPMorgan Chase & Co.",
        fiscal_year_end="2024-12-31",
        revenue_usd=Decimal("177421000000"),
        source_note=(
            "JPMorgan Chase & Co. 10-K filed 2025-02 (FY2024), "
            "Item 8: total net revenue (managed basis)."
        ),
    ),
]


__all__ = [
    "DATE_TOLERANCE_DAYS",
    "EVAL_TICKERS",
    "GroundTruth",
    "REVENUE_TOLERANCE",
]
