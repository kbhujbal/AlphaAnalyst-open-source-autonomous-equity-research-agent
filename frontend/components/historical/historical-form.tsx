"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useStartHistoricalAnalysis } from "@/lib/api/hooks";
import { historicalFormSchema, utcToday } from "@/lib/api/historical";

export function HistoricalForm() {
  const router = useRouter();
  const mutation = useStartHistoricalAnalysis();
  const [today, setToday] = useState("");
  const [form, setForm] = useState({ ticker: "", as_of: "", lookback_days: "2192", max_filings: "96", max_archive_files: "10", refresh: false });
  const [errors, setErrors] = useState<string[]>([]);
  const errorBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const date = utcToday();
    setToday(date);
    setForm(previous => ({ ...previous, as_of: date }));
  }, []);
  useEffect(() => { if (errors.length) errorBox.current?.focus(); }, [errors]);

  async function submit() {
    if (mutation.isPending) return;
    const parsed = historicalFormSchema().safeParse(form);
    if (!parsed.success) {
      setErrors(parsed.error.issues.map(issue => issue.message));
      return;
    }
    setErrors([]);
    try {
      const result = await mutation.mutateAsync(parsed.data);
      router.push(`/historical/${result.job_id}`);
    } catch (error) {
      const detail = error instanceof ApiError ? error.detail : null;
      const message = typeof detail === "string" ? detail : Array.isArray(detail)
        ? detail.map(item => typeof item?.msg === "string" ? item.msg : "Check the request fields.").join(" ")
        : error instanceof Error ? error.message : "Could not start historical analysis.";
      setErrors([message]);
    }
  }

  return <form className="space-y-6" noValidate onSubmit={event => { event.preventDefault(); void submit(); }} aria-label="Start historical financial analysis">
    {errors.length > 0 && <div ref={errorBox} tabIndex={-1} role="alert" className="rounded-lg border border-destructive p-4 text-sm text-destructive">
      <p className="font-semibold">Could not start historical analysis</p><ul className="mt-2 list-disc pl-5">{errors.map((error, index) => <li key={index}>{error}</li>)}</ul>
    </div>}
    <fieldset disabled={mutation.isPending} className="space-y-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <label className="space-y-2 text-sm font-medium">US ticker
          <Input className="mt-2" name="ticker" placeholder="e.g., AAPL" value={form.ticker} maxLength={5} autoComplete="off" spellCheck={false}
            onChange={event => setForm(previous => ({ ...previous, ticker: event.target.value.toUpperCase() }))} required />
        </label>
        <label className="space-y-2 text-sm font-medium">Availability cutoff (UTC)
          <Input className="mt-2" name="as_of" type="date" min="1994-01-01" max={today || undefined} value={form.as_of}
            aria-describedby="cutoff-help" onInput={event => { const value = event.currentTarget.value; setForm(previous => ({ ...previous, as_of: value })); }} onChange={event => setForm(previous => ({ ...previous, as_of: event.target.value }))} required />
        </label>
      </div>
      <p id="cutoff-help" className="text-sm text-muted-foreground">Include filings available by the end of this UTC date. This is the availability date, not the fiscal year end. Sources are retrieved when the analysis runs.</p>
      <details className="rounded-lg border p-4 text-sm">
        <summary className="cursor-pointer font-medium">Filing retrieval limits</summary>
        <p className="mt-3 text-muted-foreground">Look back from the cutoff by filing date. Limits include original filings and amendments; capped or unavailable coverage is disclosed in the result.</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {([ ["lookback_days", "Lookback days", 1, 14610], ["max_filings", "Filing limit", 1, 400], ["max_archive_files", "Archive page limit", 0, 100] ] as const).map(([key, label, min, max]) =>
            <label key={key} className="font-medium">{label}<Input className="mt-2" name={key} type="number" min={min} max={max} step={1} value={form[key]}
              onChange={event => setForm(previous => ({ ...previous, [key]: event.target.value }))} required /></label>)}
        </div>
        <label className="mt-4 flex items-center gap-2"><input type="checkbox" checked={form.refresh} onChange={event => setForm(previous => ({ ...previous, refresh: event.target.checked }))} />Refresh discovery and filing extraction</label>
      </details>
      <Button type="submit" disabled={mutation.isPending} className="w-full sm:w-auto">{mutation.isPending ? "Starting historical analysis…" : "Analyze historical financials"}</Button>
    </fieldset>
    <p role="status" className="text-sm text-muted-foreground">{mutation.isPending ? "Submitting your cutoff and limits. Please wait." : "Results include coverage and data gaps. Job links are available for 24 hours."}</p>
  </form>;
}
