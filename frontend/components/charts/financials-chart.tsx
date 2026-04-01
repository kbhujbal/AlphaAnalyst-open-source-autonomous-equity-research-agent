"use client";

import { BarChart } from "@tremor/react";

import { EmptyChart } from "@/components/charts/empty-chart";

export interface AnnualFinancials {
  fiscalYear: string;
  revenue: number;
  netIncome: number;
}

interface FinancialsChartProps {
  data: AnnualFinancials[] | null;
}

function formatBn(v: number): string {
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

export function FinancialsChart({ data }: FinancialsChartProps) {
  if (!data || data.length === 0) {
    return (
      <EmptyChart
        title="Annual financials not available"
        reason="MemoResponse does not yet expose a per-year revenue / net income series. Once XBRL facts roll up to the response, this chart will render the last 4 fiscal years."
      />
    );
  }

  const chartData = data.map((row) => ({
    quarter: row.fiscalYear,
    Revenue: row.revenue,
    "Net income": row.netIncome,
  }));

  return (
    <BarChart
      data={chartData}
      index="quarter"
      categories={["Revenue", "Net income"]}
      colors={["blue", "emerald"]}
      valueFormatter={formatBn}
      className="h-72"
      aria-label="Annual revenue and net income, last 4 fiscal years"
    />
  );
}
