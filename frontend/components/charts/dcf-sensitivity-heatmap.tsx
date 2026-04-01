"use client";

import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { EmptyChart } from "@/components/charts/empty-chart";
import { cn } from "@/lib/utils";

export interface SensitivityTable {
  // dict[wacc_str → dict[growth_str → decimal_str]] — matches the backend
  // shape from src.modeler.dcf.sensitivity().
  rows: Record<string, Record<string, string>>;
  currentPrice?: number | null;
}

interface DcfSensitivityHeatmapProps {
  data: SensitivityTable | null;
}

function pct(num: number): string {
  return `${(num * 100).toFixed(1)}%`;
}

function formatDollar(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `$${value.toFixed(2)}`;
}

/**
 * Maps a DCF intrinsic-value-vs-current-price ratio to a Tailwind background
 * class. Color is paired with an arrow icon so it isn't the only signal
 * (a11y rule from CLAUDE.md).
 */
function colorClass(iv: number, current: number): string {
  if (!Number.isFinite(iv) || !Number.isFinite(current) || current <= 0) {
    return "bg-muted/40";
  }
  const ratio = iv / current - 1;
  if (ratio >= 0.3) return "bg-emerald-500/30 text-emerald-950 dark:text-emerald-100";
  if (ratio >= 0.1) return "bg-emerald-500/20 text-emerald-950 dark:text-emerald-100";
  if (ratio >= -0.1) return "bg-muted/40";
  if (ratio >= -0.3) return "bg-rose-500/20 text-rose-950 dark:text-rose-100";
  return "bg-rose-500/30 text-rose-950 dark:text-rose-100";
}

function directionIcon(iv: number, current: number) {
  if (!Number.isFinite(iv) || !Number.isFinite(current) || current <= 0) {
    return <Minus className="h-3 w-3" aria-hidden="true" />;
  }
  const ratio = iv / current - 1;
  if (ratio > 0.05) return <ArrowUp className="h-3 w-3" aria-hidden="true" />;
  if (ratio < -0.05) return <ArrowDown className="h-3 w-3" aria-hidden="true" />;
  return <Minus className="h-3 w-3" aria-hidden="true" />;
}

export function DcfSensitivityHeatmap({ data }: DcfSensitivityHeatmapProps) {
  if (!data || Object.keys(data.rows).length === 0) {
    return (
      <EmptyChart
        title="Sensitivity table not available"
        reason="MemoResponse does not yet include the DCF sensitivity grid; once the backend exposes it from DCFResult, this 2-D heatmap will render with green / red intensity vs. current price."
      />
    );
  }

  const wacks = Object.keys(data.rows).sort((a, b) => Number(a) - Number(b));
  const growths = Array.from(
    new Set(
      wacks.flatMap((w) => Object.keys(data.rows[w] ?? {})),
    ),
  ).sort((a, b) => Number(a) - Number(b));
  const current = data.currentPrice ?? 0;

  return (
    <div className="overflow-x-auto">
      <table
        className="min-w-full border-separate border-spacing-1 text-xs"
        aria-label="DCF intrinsic value sensitivity to WACC and terminal growth"
      >
        <thead>
          <tr>
            <th className="sticky left-0 bg-background px-2 py-1 text-left text-muted-foreground">
              WACC \ growth
            </th>
            {growths.map((g) => (
              <th key={g} className="px-2 py-1 text-right text-muted-foreground">
                {pct(Number(g))}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {wacks.map((w) => (
            <tr key={w}>
              <th
                scope="row"
                className="sticky left-0 bg-background px-2 py-1 text-left text-muted-foreground"
              >
                {pct(Number(w))}
              </th>
              {growths.map((g) => {
                const raw = data.rows[w]?.[g];
                if (raw === undefined) {
                  return (
                    <td
                      key={g}
                      className="rounded px-2 py-1 text-right text-muted-foreground/60"
                    >
                      —
                    </td>
                  );
                }
                const iv = Number.parseFloat(raw);
                return (
                  <td
                    key={g}
                    className={cn(
                      "rounded px-2 py-1 text-right tabular-nums",
                      colorClass(iv, current),
                    )}
                  >
                    <span className="inline-flex items-center justify-end gap-1">
                      {current > 0 ? directionIcon(iv, current) : null}
                      {formatDollar(iv)}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {current > 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Cell shading compares each scenario&apos;s intrinsic value to the
          current price (${current.toFixed(2)}).
        </p>
      ) : null}
    </div>
  );
}
