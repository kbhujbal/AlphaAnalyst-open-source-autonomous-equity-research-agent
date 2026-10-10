import { z } from "zod";
import type { components } from "@/types/api";

export type CitationData = components["schemas"]["Citation"];
export type Memo = Required<components["schemas"]["Memo"]>;

const SourcePassageZ = z.object({
  document_id: z.string(), document_hash: z.string(), source_id: z.string(), source_url: z.string().url(),
  scope: z.enum(["normalized_filing", "transcript", "feed_excerpt", "provider_record"]),
  start: z.number().int().nonnegative(), end: z.number().int().positive(), text: z.string(), text_hash: z.string(),
  retrieved_at: z.string(), snapshot_url: z.string().nullable().optional(),
}) satisfies z.ZodType<components["schemas"]["SourcePassage"]>;

const ClaimAuditZ = z.object({
  claim_id: z.string(),
  section: z.enum(["executive_summary", "financial_snapshot", "recent_catalysts", "valuation", "earnings_call_tone_shift", "alt_data_signals", "bull_case", "bear_case", "risks"]),
  kind: z.enum(["quote", "financial", "calculation", "interpretation"]), proposed_text: z.string(), rendered_text: z.string().nullable().optional(),
  observation_id: z.string().nullable().optional(),
  calculation_id: z.string().nullable().optional(),
  calculation_output: z.string().nullable().optional(),
  citations: z.array(z.string()), status: z.enum(["verified", "needs_review", "rejected"]),
  checks: z.array(z.object({ field: z.string(), claimed: z.string().nullable(), expected: z.string().nullable(), passed: z.boolean() })).default([]),
  reasons: z.array(z.string()).default([]),
}) satisfies z.ZodType<components["schemas"]["ClaimAudit"]>;

const CitationZ = z.object({
  tag: z.string().regex(/^F[1-9]\d*$/).nullable().optional(),
  source_type: z.enum(["filing", "transcript", "news", "fact", "price", "macro", "estimates"]),
  source_id: z.string(),
  snippet: z.string(),
  passages: z.array(SourcePassageZ).default([]),
  calculation_id: z.string().nullable().optional(),
  observation_id: z.string().nullable().optional(),
}) satisfies z.ZodType<CitationData>;

export const FinancialObservationZ = z.object({
  ticker: z.string(), metric: z.string(), tag: z.string(), value: z.string(), unit: z.string(),
  currency: z.string().regex(/^[A-Z]{3}$/).nullable().optional(),
  period: z.object({
    kind: z.enum(["instant", "duration", "unknown"]),
    start: z.string().nullable().optional(), end: z.string().nullable().optional(),
    basis: z.enum(["annual", "quarterly", "ytd", "ttm", "unknown"]),
    fiscal_year: z.string().nullable().optional(), fiscal_period: z.string().nullable().optional(),
    start_method: z.enum(["reported", "prior_annual_end", "prior_quarter_end", "unknown"]),
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
  market_cap_context: z.object({
    basis: z.literal("current-profile-recent-7d-v1"), profile_document_id: z.string(), profile_hash: z.string(),
    profile_source_url: z.string().url(), profile_retrieved_at: z.string(),
  }).nullable().optional(),
  scale: z.literal(1), reported_decimals: z.string().nullable().optional(),
  quality_flags: z.array(z.string()).optional(),
}) satisfies z.ZodType<components["schemas"]["FinancialObservation"]>;

const DCFScenarioZ = z.object({
  name: z.string(), rationale: z.string(), wacc: z.string(), terminal_growth: z.string(),
  fcff_margin: z.string(), projection_years: z.number().int(),
});

const CalculationTraceZ = z.object({
  engine: z.literal("decimal-28-half-even-v1"),
  recipe: z.enum(["growth-v1", "net-margin-v1", "ocf-margin-v1", "dcf-fcff-v1", "comps-v1", "ttm-income-v1", "comps-ttm-v1", "q4-v1", "ttm-ytd-v1", "cashflow-difference-v1", "ttm-flex-v1", "comps-flex-v1"]),
  ticker: z.string(), bindings: z.record(z.array(z.string())), scenario: DCFScenarioZ.nullable().optional(),
  valuation_date: z.string().nullable().optional(), currency: z.string(), calculation_id: z.string(),
  steps: z.array(z.object({ node: z.string(), operation: z.string(), inputs: z.array(z.string()).default([]),
    value: z.string(), unit: z.string(), observation_id: z.string().nullable().optional(), note: z.string().nullable().optional() })),
  period_methods: z.record(z.enum(["quarters", "annual-ytd", "q4-plus-quarters", "cashflow-quarters"])).optional(),
  periods: z.record(z.object({
    kind: z.enum(["instant", "duration", "unknown"]), start: z.string().nullable().optional(), end: z.string().nullable().optional(),
    basis: z.enum(["annual", "quarterly", "ytd", "ttm", "unknown"]),
    start_method: z.enum(["reported", "prior_annual_end", "prior_quarter_end", "unknown"]),
  })).optional(),
  outputs: z.record(z.string()), warnings: z.array(z.string()).default([]),
}) satisfies z.ZodType<components["schemas"]["CalculationTrace"]>;

const CalculationRecordZ = z.object({
  trace: CalculationTraceZ,
  sources: z.array(z.object({ observation: FinancialObservationZ, passages: z.array(SourcePassageZ) })),
}) satisfies z.ZodType<components["schemas"]["CalculationRecord"]>;

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
  calculations: z.array(CalculationRecordZ).default([]),
  claim_audit: z.array(ClaimAuditZ).default([]),
  verification_version: z.literal("claim-v1").nullable().default(null),
  verification_issues: z.array(z.string()).default([]),
}) satisfies z.ZodType<Memo, z.ZodTypeDef, components["schemas"]["Memo"]>;

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
