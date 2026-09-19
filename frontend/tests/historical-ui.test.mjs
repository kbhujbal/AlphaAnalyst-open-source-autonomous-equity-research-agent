import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
register("./helpers/tsx-loader.mjs", import.meta.url);
const { HistoricalAnalysisZ, historicalFormSchema, utcToday, canLoadHistoricalResult, canLoadLiveMemo } = await import("../lib/api/historical.ts");
const { evidencePath } = await import("../lib/api/evidence-link.ts");
const { HistoricalResult } = await import("../components/historical/historical-result.tsx");
const fixture = JSON.parse(readFileSync(new URL("./fixtures/historical-analysis.json", import.meta.url), "utf8"));
const fresh = () => structuredClone(fixture);
const form = { ticker: " aapl ", as_of: "2024-06-30", lookback_days: "2192", max_filings: "96", max_archive_files: "0", refresh: false };

test("cutoff submission normalizes ticker, preserves the selected date and permits zero archive pages", () => {
  assert.deepEqual(historicalFormSchema("2025-01-01").parse(form), { ticker: "AAPL", as_of: "2024-06-30", lookback_days: 2192, max_filings: 96, max_archive_files: 0, refresh: false });
  assert.equal(utcToday(new Date("2025-01-01T00:30:00+05:30")), "2024-12-31");
});

test("invalid dates, future dates, fractional or excessive budgets never become API requests", () => {
  for (const override of [ { as_of: "2024-02-30" }, { as_of: "2023-02-29" }, { as_of: "2025-01-02" }, { as_of: "1993-12-31" },
    { as_of: "2024-06-30T00:00:00Z" }, { as_of: "" }, { ticker: "A1" }, { lookback_days: "14611" }, { max_filings: "0" },
    { max_filings: "1.5" }, { max_filings: "1e2" }, { max_archive_files: "101" }, { max_archive_files: "-1" } ]) {
    assert.equal(historicalFormSchema("2025-01-01").safeParse({ ...form, ...override }).success, false, JSON.stringify(override));
  }
  assert.equal(historicalFormSchema("2025-01-01").safeParse({ ...form, as_of: "2024-02-29" }).success, true);
});

test("failed historical jobs still fetch inspectable results and never fetch live memos", () => {
  for (const status of ["partial", "failed"]) {
    const job = { analysis_kind: "historical-financials", status };
    assert.equal(canLoadHistoricalResult(job), true);
    assert.equal(canLoadLiveMemo(job), false);
  }
  for (const status of ["queued", "running", "complete", "error"]) assert.equal(canLoadHistoricalResult({ analysis_kind: "historical-financials", status }), false);
  assert.equal(canLoadHistoricalResult({ status: "partial" }), false);
  assert.equal(canLoadLiveMemo({ status: "partial" }), true);
  assert.equal(canLoadHistoricalResult(undefined), false);
});

test("runtime contract rejects incomplete coverage fields, mismatched cutoffs and misleading complete status", () => {
  for (const mutate of [ r => { delete r.filing_coverage.coverage_complete; }, r => { delete r.financial_coverage_complete; },
    r => { delete r.missing_data; }, r => { r.status = "complete"; }, r => { r.filing_coverage.as_of = "2025-03-02"; },
    r => { r.request.ticker = "OTHER"; }, r => { r.calculations[0].trace.steps[0].value = 100; } ]) {
    const result = fresh(); mutate(result);
    assert.equal(HistoricalAnalysisZ.safeParse(result).success, false);
  }
});

test("actual result component shows cutoff, separate coverage counts, exact values and missing-data context", () => {
  const result = HistoricalAnalysisZ.parse(fresh());
  const html = renderToStaticMarkup(React.createElement(HistoricalResult, { result }));
  for (const expected of ["TEST historical financials", "Partial financial analysis", "2025-03-01", "2026-11-13", "Partial discovery", "Incomplete", "Archive pages loaded: 0", "Filings discovered: 3", "Returned: 1", "0000000123-25-000001", "2 observations extracted", "operating_cash_flow: no verified annual context", "100", "USD", "Download trace JSON", "Retained financial source passages", "/historical-analyses/" ]) assert.ok(html.includes(expected), expected);
  assert.ok(!html.includes("Complete for requested scope"));
});

test("failed result component keeps filing failures and does not manufacture facts or calculations", () => {
  const data = fresh();
  data.status = "failed"; data.financial_facts = []; data.calculations = []; data.retained_sources = [];
  data.extractions[0] = { ...data.extractions[0], status: "failed", observation_count: 0, error: "Amendment XBRL unavailable" };
  data.missing_data = [{ code: "unresolved_amendment", message: "All quantitative outputs withheld", accession_no: "0000000123-25-000001", metric: null }];
  const html = renderToStaticMarkup(React.createElement(HistoricalResult, { result: HistoricalAnalysisZ.parse(data) }));
  for (const expected of ["Financial analysis unavailable", "Extraction failed", "Amendment XBRL unavailable", "All quantitative outputs withheld", "Calculations unavailable", "Structured financial observations are unavailable"]) assert.ok(html.includes(expected), expected);
  assert.ok(!html.includes("Download trace JSON"));
});

test("missing discovery metadata is shown as unavailable rather than zero or complete coverage", () => {
  const data = fresh(); data.filing_coverage = null;
  const html = renderToStaticMarkup(React.createElement(HistoricalResult, { result: HistoricalAnalysisZ.parse(data) }));
  assert.ok(html.includes("Discovery metadata is unavailable. No coverage is assumed."));
  assert.ok(!html.includes("Archive pages loaded:"));
});

test("only result-scoped evidence URLs can link to the API origin", () => {
  const path = fixture.financial_facts[0].passages[0].snapshot_url;
  assert.equal(evidencePath(path), path);
  assert.equal(evidencePath(path.replace("historical-analyses", "memos")), path.replace("historical-analyses", "memos"));
  for (const bad of ["https://evil.example/api/v1/memos/x", "//evil.example", "/api/v1/historical-analyses/../../secrets", `${path}?redirect=evil`, `${path}/extra`, null]) assert.equal(evidencePath(bad), null);
});
