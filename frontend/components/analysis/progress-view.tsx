"use client";

import { Check, Loader2 } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

const STEPS = [
  { key: "fetching_data", label: "Fetching data", pct: 10 },
  { key: "indexing", label: "Indexing filings", pct: 25 },
  { key: "running_agents", label: "Running agents", pct: 50 },
  { key: "valuation", label: "Building DCF", pct: 70 },
  { key: "synthesizing", label: "Synthesizing memo", pct: 85 },
  { key: "exporting", label: "Exporting", pct: 95 },
] as const;

interface ProgressViewProps {
  progressPct: number;
  currentStep: string | null;
  ticker?: string;
}

export function ProgressView({
  progressPct,
  currentStep,
  ticker,
}: ProgressViewProps) {
  const remainingSeconds = Math.max(
    0,
    Math.ceil((100 - progressPct) * 0.6),
  );
  return (
    <div className="mx-auto max-w-xl space-y-8">
      <header className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight">
          {ticker ? `Analyzing ${ticker}` : "Analyzing"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          We&apos;re running the full pipeline. You can stay on this page.
        </p>
      </header>
      <div>
        <Progress value={progressPct} aria-label="Analysis progress" />
        <div className="mt-2 flex justify-between text-sm text-muted-foreground">
          <span>{progressPct}%</span>
          <span>~{remainingSeconds}s remaining</span>
        </div>
      </div>
      <ol className="space-y-2">
        {STEPS.map((step) => {
          const active = currentStep === step.key;
          const done = !active && progressPct >= step.pct;
          return (
            <li
              key={step.key}
              className="flex items-center gap-3"
              aria-current={active ? "step" : undefined}
            >
              <span className="inline-flex h-5 w-5 items-center justify-center">
                {active ? (
                  <Loader2
                    className="h-4 w-4 animate-spin"
                    aria-hidden="true"
                  />
                ) : done ? (
                  <Check
                    className="h-4 w-4 text-emerald-500"
                    aria-hidden="true"
                  />
                ) : (
                  <span
                    className="h-2 w-2 rounded-full bg-muted-foreground/30"
                    aria-hidden="true"
                  />
                )}
              </span>
              <span
                className={cn(
                  "text-sm",
                  active && "font-medium text-foreground",
                  !active && done && "text-muted-foreground",
                  !active && !done && "text-muted-foreground/70",
                )}
              >
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
