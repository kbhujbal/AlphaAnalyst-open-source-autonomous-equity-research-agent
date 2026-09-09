import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { MemoZ, buildTagMap } from "../lib/api/memo-schema.ts";
import { isJobPending, isReportReady } from "../lib/api/job-state.ts";

const demo = JSON.parse(readFileSync(new URL("../fixtures/demo-memo.json", import.meta.url), "utf8"));

test("partial reports are readable and terminal", () => {
  assert.equal(isReportReady("partial"), true);
  assert.equal(isJobPending("partial"), false);
  for (const state of ["failed", "error"]) {
    assert.equal(isReportReady(state), false);
    assert.equal(isJobPending(state), false);
  }
  assert.equal(isJobPending("queued"), true);
  assert.equal(isJobPending("running"), true);
  assert.equal(isReportReady("complete"), true);
  assert.equal(isReportReady(undefined), false);
});

test("offline fixture renders supported memo data without live exports", () => {
  const memo = MemoZ.parse(demo.sections);
  assert.equal(memo.ticker, "DEMO");
  assert.equal(demo.status, "partial");
  assert.ok(demo.warnings.length);
  assert.deepEqual(demo.exports, { pdf: null, excel: null });
  assert.equal(buildTagMap(memo).get("F1").source_id, "demo://fictional-research-note");
});

test("sparse citation tags keep their actual identities", () => {
  const memo = MemoZ.parse({
    ...demo.sections,
    executive_summary: "Supported [F9].",
    citations: [{ ...demo.sections.citations[0], tag: "F9" }],
  });
  const map = buildTagMap(memo);
  assert.equal(map.has("F1"), false);
  assert.equal(map.get("F9").tag, "F9");
});

test("legacy citations are never assigned guessed fact numbers", () => {
  const { tag, ...citation } = demo.sections.citations[0];
  const memo = MemoZ.parse({ ...demo.sections, citations: [citation] });
  assert.equal(buildTagMap(memo).size, 0);
});

test("missing citation arrays normalize but malformed citations fail", () => {
  assert.deepEqual(MemoZ.parse({ ...demo.sections, citations: undefined }).citations, []);
  assert.equal(MemoZ.safeParse({ ...demo.sections, citations: [{ tag: "F0" }] }).success, false);
});

test("financial observation contracts preserve non-USD units and reporting context", async () => {
  const { FinancialObservationZ, MemoResponseZ } = await import("../lib/api/memo-schema.ts");
  const fact = {
    ticker: "DEMO", metric: "eps", tag: "epsDiluted", value: "1.25", unit: "EUR/share", currency: "EUR",
    period: { kind: "duration", start: "2023-10-01", end: "2023-12-31", basis: "quarterly", start_method: "reported", start_evidence: null },
    dimensions: {}, scale: 1, payload_hash: "a".repeat(64), observation_id: "b".repeat(64),
    source: { provider: "fmp", url: "https://example.test/statement", fetched_at: "2024-01-01T00:00:00Z", locator: "/0/epsDiluted" },
  };
  assert.equal(FinancialObservationZ.parse(fact).unit, "EUR/share");
  const response = MemoResponseZ.parse({ ...demo, financial_facts: [fact] });
  assert.equal(response.financial_facts[0].currency, "EUR");
  assert.equal(response.financial_facts[0].period.start, "2023-10-01");
  assert.equal("raw_payload" in response.financial_facts[0], false);
});

test("claim audits and exact source passages survive runtime validation", () => {
  const text = "Exact source passage with revenue of USD 100.";
  const passage = { document_id: "a".repeat(64), document_hash: "b".repeat(64), source_id: "filing-1", source_url: "https://example.test/filing", scope: "normalized_filing", start: 10, end: 10 + text.length, text, text_hash: "c".repeat(64), retrieved_at: "2026-01-01T00:00:00Z", snapshot_url: "/api/v1/memos/job/evidence/doc" };
  const memo = MemoZ.parse({ ...demo.sections, verification_version: "claim-v1", citations: [{ ...demo.sections.citations[0], passages: [passage] }], claim_audit: [{ claim_id: "C1", section: "executive_summary", kind: "quote", proposed_text: text, rendered_text: `“${text}”`, citations: ["F1"], status: "verified" }] });
  assert.equal(memo.verification_version, "claim-v1");
  assert.equal(memo.claim_audit[0].status, "verified");
  assert.equal(memo.citations[0].passages[0].text, text);
  assert.equal(memo.citations[0].passages[0].snapshot_url, passage.snapshot_url);
});

test("legacy reports cannot acquire verification status through defaults", () => {
  const memo = MemoZ.parse(demo.sections);
  assert.equal(memo.verification_version, null);
  assert.deepEqual(memo.claim_audit, []);
  assert.deepEqual(memo.citations[0].passages, []);
});


test("calculation contracts retain exact decimals, recipe identities and source passages", () => {
  const record = JSON.parse(readFileSync(new URL("./fixtures/calculation-record.json", import.meta.url), "utf8"));
  const memo = MemoZ.parse({ ...demo.sections, calculations: [record] });
  assert.equal(memo.calculations[0].trace.calculation_id, record.trace.calculation_id);
  assert.equal(memo.calculations[0].trace.steps[0].value, "100");
  assert.equal(memo.calculations[0].sources[0].passages[0].text, record.sources[0].passages[0].text);
  assert.equal(MemoZ.parse(demo.sections).calculations.length, 0);
  record.trace.recipe = "unregistered-formula";
  assert.equal(MemoZ.safeParse({ ...demo.sections, calculations: [record] }).success, false);
});
