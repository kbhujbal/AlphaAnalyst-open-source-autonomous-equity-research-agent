import { z } from "zod";
import type { components } from "@/types/api";

export type CitationData = components["schemas"]["Citation"];
export type Memo = Required<components["schemas"]["Memo"]>;

const CitationZ = z.object({
  tag: z.string().regex(/^F[1-9]\d*$/).nullable().optional(),
  source_type: z.enum(["filing", "transcript", "news", "fact", "price", "macro", "estimates"]),
  source_id: z.string(),
  snippet: z.string(),
}) satisfies z.ZodType<CitationData>;

export const MemoZ = z.object({
  ticker: z.string(),
  as_of: z.string(),
  executive_summary: z.string(),
  financial_snapshot: z.string(),
  recent_catalysts: z.string(),
  valuation: z.string(),
  earnings_call_tone_shift: z.string(),
  alt_data_signals: z.string(),
  bull_case: z.string(),
  bear_case: z.string(),
  risks: z.string(),
  citations: z.array(CitationZ).default([]),
}) satisfies z.ZodType<Memo, z.ZodTypeDef, components["schemas"]["Memo"]>;

export const FinancialObservationZ = z.object({
  ticker: z.string(), metric: z.string(), tag: z.string(), value: z.string(), unit: z.string(),
  currency: z.string().regex(/^[A-Z]{3}$/).nullable().optional(),
  period: z.object({
    kind: z.enum(["instant", "duration", "unknown"]),
    start: z.string().nullable().optional(), end: z.string().nullable().optional(),
    basis: z.enum(["annual", "quarterly", "ytd", "ttm", "unknown"]),
    fiscal_year: z.string().nullable().optional(), fiscal_period: z.string().nullable().optional(),
    start_method: z.enum(["reported", "prior_annual_end", "unknown"]),
    start_evidence: z.object({ period_end: z.string(), locator: z.string(), payload_hash: z.string() }).nullable().optional(),
  }),
  dimensions: z.record(z.string()).optional(),
  source: z.object({
    provider: z.string(), url: z.string().url(), fetched_at: z.string(), locator: z.string(),
    document_url: z.string().url().nullable().optional(), accession_no: z.string().nullable().optional(),
    filing_date: z.string().nullable().optional(), accepted_at: z.string().nullable().optional(),
  }),
  payload_hash: z.string().regex(/^[a-f0-9]{64}$/),
  observation_id: z.string().regex(/^[a-f0-9]{64}$/),
  scale: z.literal(1), reported_decimals: z.string().nullable().optional(),
  quality_flags: z.array(z.string()).optional(),
}) satisfies z.ZodType<components["schemas"]["FinancialObservation"]>;

export const MemoResponseZ = z.object({
  ticker: z.string(),
  generated_at: z.string(),
  sections: MemoZ,
  exports: z.object({ pdf: z.string().nullable().optional(), excel: z.string().nullable().optional() }),
  cost_usd: z.string(),
  llm_calls: z.number().int().nonnegative(),
  status: z.enum(["complete", "partial"]),
  financial_facts: z.array(FinancialObservationZ).default([]),
  warnings: z.array(z.string()).default([]),
}) satisfies z.ZodType<components["schemas"]["MemoResponse"]>;

export function buildTagMap(memo: Memo): Map<string, CitationData> {
  const map = new Map<string, CitationData>();
  for (const citation of memo.citations) {
    if (citation.tag) map.set(citation.tag, citation);
  }
  return map;
}
