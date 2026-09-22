import assert from "node:assert/strict";
import test, { after } from "node:test";

const originalFetch = globalThis.fetch;
let respond;
globalThis.fetch = (...args) => respond(...args);
const { startAnalysis, getJob, getMemo, startHistoricalAnalysis, getHistoricalAnalysis, ApiError } = await import("../lib/api/client.ts");
after(() => { globalThis.fetch = originalFetch; });

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

test("analysis submits the ticker once to the configured API", async () => {
  let calls = 0;
  respond = async (request) => {
    calls++;
    assert.equal(request.method, "POST");
    assert.equal(new URL(request.url).pathname, "/api/v1/analyze");
    assert.deepEqual(await request.json(), { ticker: "AAPL" });
    return json({ job_id: "job-1", status: "queued" }, 202);
  };
  assert.equal((await startAnalysis({ ticker: "AAPL" })).job_id, "job-1");
  assert.equal(calls, 1);
});

test("configuration failures preserve the backend's actionable detail", async () => {
  respond = async () => json({ detail: "Live analysis is not configured. Set: FMP_API_KEY" }, 503);
  await assert.rejects(startAnalysis({ ticker: "AAPL" }), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 503);
    assert.match(error.message, /FMP_API_KEY/);
    return true;
  });
});

test("partial job and memo states pass through unchanged", async () => {
  respond = async (request) => json(new URL(request.url).pathname.includes("/jobs/")
    ? { job_id: "job-1", status: "partial", progress_pct: 100, warnings: ["Missing transcripts"] }
    : { status: "partial", exports: { pdf: null, excel: null }, warnings: ["Missing transcripts"] });
  assert.equal((await getJob("job-1")).status, "partial");
  const memo = await getMemo("job-1");
  assert.equal(memo.exports.pdf, null);
  assert.deepEqual(memo.warnings, ["Missing transcripts"]);
});

test("a missing job produces an API error rather than empty success", async () => {
  respond = async () => json({ detail: "job not found" }, 404);
  await assert.rejects(getJob("missing"), (error) => error instanceof ApiError && error.status === 404);
});


test("historical analysis submits an explicit cutoff and preserves coverage gaps", async () => {
  respond = async (request) => {
    assert.equal(new URL(request.url).pathname, "/api/v1/historical-analyses");
    assert.deepEqual(await request.json(), { ticker: "AAPL", as_of: "2024-06-01", max_filings: 20 });
    return json({ job_id: "historical-1", status: "queued", result_url: "/api/v1/historical-analyses/historical-1" }, 202);
  };
  assert.equal((await startHistoricalAnalysis({ ticker: "AAPL", as_of: "2024-06-01", max_filings: 20 })).job_id, "historical-1");
  respond = async (request) => {
    assert.equal(new URL(request.url).pathname, "/api/v1/historical-analyses/historical-1");
    return json({ status: "partial", as_of: "2024-06-01", filing_coverage: { coverage_complete: false },
      missing_data: [{ code: "partial_filing_coverage", message: "Archive page unavailable" }] });
  };
  const result = await getHistoricalAnalysis("historical-1");
  assert.equal(result.status, "partial");
  assert.equal(result.filing_coverage.coverage_complete, false);
  assert.equal(result.missing_data[0].code, "partial_filing_coverage");
});
