"use client";

import { BarChart } from "@tremor/react";

import { EmptyChart } from "@/components/charts/empty-chart";

export interface ToneQuarter {
  quarter: string;
  challenging: number;
  headwind: number;
  strong: number;
  record: number;
}

interface ToneShiftChartProps {
  data: ToneQuarter[] | null;
}

export function ToneShiftChart({ data }: ToneShiftChartProps) {
  if (!data || data.length === 0) {
    return (
      <EmptyChart
        title="Tone keyword counts not available"
        reason="The earnings-call agent computes these in Python (see TONE_KEYWORDS in earnings_call_agent.py) but they're embedded in narrative text rather than the structured Memo. When the backend adds a per-quarter counts field, this stacked chart will populate."
      />
    );
  }

  return (
    <BarChart
      data={data}
      index="quarter"
      categories={["challenging", "headwind", "strong", "record"]}
      colors={["rose", "amber", "emerald", "blue"]}
      stack
      valueFormatter={(v) => v.toString()}
      className="h-72"
      aria-label="Tone keyword counts per quarter"
    />
  );
}
