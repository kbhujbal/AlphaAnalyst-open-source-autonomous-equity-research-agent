"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useStartAnalysis } from "@/lib/api/hooks";

const TICKER_PATTERN = /^[A-Z]{1,5}$/;
const TickerZ = z.string().regex(TICKER_PATTERN);

const STORAGE_KEY = "alphaanalyst:recent-tickers";
const MAX_RECENT = 5;

function readRecent(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((s): s is string => typeof s === "string" && TICKER_PATTERN.test(s))
      .slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}

function writeRecent(values: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
  } catch {
    // localStorage may be unavailable (private mode, quota); ignore.
  }
}

export function TickerSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [recent, setRecent] = useState<string[]>([]);
  const mutation = useStartAnalysis();

  useEffect(() => {
    setRecent(readRecent());
  }, []);

  async function submit(rawTicker: string): Promise<void> {
    const ticker = rawTicker.trim().toUpperCase();
    const result = TickerZ.safeParse(ticker);
    if (!result.success) {
      toast.error("Invalid ticker", {
        description: "Use 1-5 uppercase letters (e.g., TSLA, AAPL).",
      });
      return;
    }
    try {
      const res = await mutation.mutateAsync({ ticker });
      const next = [ticker, ...recent.filter((t) => t !== ticker)].slice(
        0,
        MAX_RECENT,
      );
      setRecent(next);
      writeRecent(next);
      router.push(`/analysis/${res.job_id}`);
    } catch (err) {
      const description =
        err instanceof ApiError
          ? typeof err.detail === "string"
            ? err.detail
            : err.message
          : err instanceof Error
            ? err.message
            : "Unknown error";
      toast.error("Could not start analysis", { description });
    }
  }

  const disabled = mutation.isPending;

  return (
    <div className="mx-auto max-w-md space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit(value);
        }}
        className="flex gap-2"
      >
        <Input
          placeholder="Enter ticker (e.g., TSLA)"
          value={value}
          onChange={(e) => setValue(e.target.value.toUpperCase())}
          maxLength={5}
          autoFocus
          aria-label="Ticker"
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
        />
        <Button
          type="submit"
          disabled={disabled || value.length === 0}
        >
          {disabled ? "Starting…" : "Analyze"}
        </Button>
      </form>
      {recent.length > 0 ? (
        <div className="flex flex-wrap items-center justify-center gap-2 text-sm">
          <span className="text-muted-foreground">Recent:</span>
          {recent.map((ticker) => (
            <Badge
              key={ticker}
              variant="secondary"
              className="cursor-pointer hover:bg-secondary/80"
              role="button"
              tabIndex={0}
              onClick={() => void submit(ticker)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  void submit(ticker);
                }
              }}
            >
              {ticker}
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}
