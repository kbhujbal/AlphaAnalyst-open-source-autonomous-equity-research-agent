import { z } from "zod";
import type { components } from "@/types/api";
import { CalculationRecordZ, FilingReconciliationZ, FinancialObservationZ, SourcePassageZ } from "./memo-schema";

export function utcToday(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}

function calendarDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
    && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

const DateZ = z.string().refine(calendarDate, "Enter a valid calendar date (YYYY-MM-DD).");
const budget = (label: string, minimum: number, maximum: number) => z.string()
  .regex(/^\d+$/, `${label} must be a whole number.`).transform(Number)
  .pipe(z.number().int().min(minimum, `${label} must be at least ${minimum}.`).max(maximum, `${label} must be at most ${maximum}.`));

export function historicalFormSchema(today = utcToday()) {
  return z.object({
    ticker: z.string().trim().toUpperCase().regex(/^[A-Z]{1,5}$/, "Ticker must contain 1–5 letters."),
    as_of: DateZ.refine(value => value >= "1994-01-01" && value <= today,
      `Cutoff must be between 1994-01-01 and ${today} (UTC).`),
    lookback_days: budget("Lookback days", 1, 14610),
    max_filings: budget("Filing limit", 1, 400),
    max_archive_files: budget("Archive page limit", 0, 100),
    refresh: z.boolean(),
  });
}

const FilingZ = z.object({
  ticker: z.string(), filing_type: z.string(), filing_date: DateZ, accession_no: z.string(),
  raw_url: z.string().url(), primary_document: z.string().nullable().optional(), report_date: DateZ.nullable().optional(),
  accepted_at: z.string().nullable().optional(),
  source: z.object({ provider: z.string(), url: z.string(), fetched_at: z.string() }),
});
const CountZ = z.number().int().nonnegative();
const CalculationSourceZ = z.object({ observation: FinancialObservationZ, passages: z.array(SourcePassageZ) });

// Require coverage flags and warning arrays. Missing response fields must never
// acquire a positive coverage status through client defaults.
export const HistoricalAnalysisZ = z.object({
  job_id: z.string().uuid(), ticker: z.string(), as_of: DateZ, generated_at: z.string(),
  analysis_kind: z.literal("historical-financials"), scope: z.literal("sec-filing-financials-only"),
  cutoff_policy: z.literal("utc-end-of-day-filing-availability-v1"), status: z.enum(["partial", "failed"]),
  request: z.object({ ticker: z.string(), as_of: DateZ, lookback_days: CountZ, max_filings: CountZ, max_archive_files: CountZ,
    refresh: z.boolean(), dcf_scenario: z.object({ name: z.string(), rationale: z.string(), wacc: z.string(),
      terminal_growth: z.string(), fcff_margin: z.string(), projection_years: CountZ }).nullable().optional() }),
  filing_coverage: z.object({
    ticker: z.string(), as_of: DateZ, requested_start: DateZ.nullable(), forms: z.array(z.string()),
    coverage_complete: z.boolean(), archive_pages_available: CountZ, archive_pages_loaded: CountZ, filings_discovered: CountZ,
    filings: z.array(FilingZ), manifest_document_ids: z.array(z.string()), warnings: z.array(z.string()), unresolved_amendments: z.array(z.string()),
  }).nullable(),
  extractions: z.array(z.object({ accession_no: z.string(), filing_type: z.string(), status: z.enum(["extracted", "failed"]),
    observation_count: CountZ, error: z.string().nullable() })),
  financial_coverage_complete: z.boolean(), reconciliation: FilingReconciliationZ.nullable(),
  financial_facts: z.array(CalculationSourceZ), retained_sources: z.array(CalculationSourceZ),
  latest_filing_date: DateZ.nullable(), latest_financial_period_end: DateZ.nullable(), calculations: z.array(CalculationRecordZ),
  missing_data: z.array(z.object({ code: z.string(), message: z.string(), accession_no: z.string().nullable(), metric: z.string().nullable() })),
  warnings: z.array(z.string()),
}).superRefine((result, context) => {
  if (result.as_of !== result.request.as_of || result.ticker !== result.request.ticker
    || result.filing_coverage && (result.filing_coverage.as_of !== result.as_of || result.filing_coverage.ticker !== result.ticker)) {
    context.addIssue({ code: "custom", message: "Historical result and coverage do not match the requested ticker/cutoff." });
  }
}) satisfies z.ZodType<components["schemas"]["HistoricalAnalysisResponse"]>;

export type HistoricalAnalysis = z.infer<typeof HistoricalAnalysisZ>;

type Job = components["schemas"]["JobStatus"];
export function canLoadHistoricalResult(job: Job | undefined): boolean {
  return job?.analysis_kind === "historical-financials" && (job.status === "partial" || job.status === "failed");
}

export function canLoadLiveMemo(job: Job | undefined): boolean {
  return Boolean(job && job.analysis_kind !== "historical-financials" && (job.status === "partial" || job.status === "complete"));
}
