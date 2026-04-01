"use client";

import { LineChart } from "@tremor/react";

import { EmptyChart } from "@/components/charts/empty-chart";

export interface PriceBarPoint {
  date: string;
  ticker: number;
  spy: number;
}

interface PriceChartProps {
  ticker: string;
  data: PriceBarPoint[] | null;
  height?: number;
}

/**
 * Normalized 1Y price chart: ticker close + SPY close, both rebased to 100
 * at the start of the window. Y-axis is % return (-100..+inf).
 */
export function PriceChart({ ticker, data, height }: PriceChartProps) {
  if (!data || data.length === 0) {
    return (
      <EmptyChart
        title="Price history not available"
        reason="The MemoResponse does not yet expose a price_history series. Once the backend includes one, this chart will show 1Y normalized returns vs SPY."
      />
    );
  }
  return (
    <LineChart
      data={data}
      index="date"
      categories={[ticker, "SPY"]}
      colors={["blue", "amber"]}
      valueFormatter={(v) => `${v.toFixed(1)}%`}
      yAxisWidth={48}
      showLegend
      className={height ? `h-[${height}px]` : "h-72"}
      aria-label={`Normalized 1-year price chart for ${ticker} versus SPY`}
    />
  );
}

/**
 * Helper to rebase raw closes to 100 at series start, returning the shape
 * the chart consumes.
 */
export function rebaseToOneHundred(
  ticker: string,
  series: Array<{ date: string; tickerClose: number; spyClose: number }>,
): PriceBarPoint[] {
  if (series.length === 0) return [];
  const t0 = series[0].tickerClose;
  const s0 = series[0].spyClose;
  if (t0 === 0 || s0 === 0) return [];
  return series.map((p) => ({
    date: p.date,
    [ticker]: ((p.tickerClose - t0) / t0) * 100,
    SPY: ((p.spyClose - s0) / s0) * 100,
  })) as unknown as PriceBarPoint[];
}
