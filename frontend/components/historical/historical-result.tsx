"use client";

import { Badge } from "@/components/ui/badge";
import { Calculations } from "@/components/memo/calculations";
import { Citation } from "@/components/memo/citation";
import { FinancialFacts } from "@/components/memo/financial-facts";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiBaseUrl } from "@/lib/api/client";
import { evidencePath } from "@/lib/api/evidence-link";
import type { HistoricalAnalysis } from "@/lib/api/historical";

export function HistoricalResult({ result }: { result: HistoricalAnalysis }) {
  const coverage = result.filing_coverage;
  const extracted = new Map(result.extractions.map(item => [item.accession_no, item]));
  const selected = new Set(result.financial_facts.map(source => source.observation.observation_id));
  return <article className="min-w-0 space-y-8">
    <header className="space-y-3">
      <div className="flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold">{result.ticker} historical financials</h1>
        <Badge variant={result.status === "failed" ? "destructive" : "secondary"}>{result.status === "failed" ? "Financial analysis unavailable" : "Partial financial analysis"}</Badge></div>
      <p className="text-lg">Available by <strong>{result.as_of}, UTC end of day</strong></p>
      <p className="text-sm text-muted-foreground">Analysis generated: {result.generated_at}. Sources retrieved now and filtered by reported filing availability; this is not a contemporaneously archived point-in-time dataset.</p>
      <p className="text-sm text-muted-foreground">SEC financials only. Historical market data and full research memos are unavailable. Current ticker-to-CIK mapping does not establish historical issuer identity.</p>
    </header>

    <section aria-labelledby="missing-data-heading" className={`rounded-lg border p-5 ${result.status === "failed" ? "border-destructive/50" : "bg-muted/30"}`}>
      <h2 id="missing-data-heading" className="text-lg font-semibold">Missing data and limitations ({result.missing_data.length})</h2>
      {result.status === "failed" && <p className="mt-2 text-sm">Financial outputs could not be verified for this run. Review the coverage and failure reasons below.</p>}
      {result.missing_data.length ? <ul className="mt-3 space-y-3 text-sm">{result.missing_data.map((gap, index) => <li key={`${gap.code}-${index}`} className="break-words border-t pt-3">
        <p>{gap.message}</p><p className="mt-1 text-xs text-muted-foreground">{gap.code.replaceAll("_", " ")}{gap.metric ? ` · ${gap.metric.replaceAll("_", " ")}` : ""}{gap.accession_no ? ` · Accession ${gap.accession_no}` : ""}</p>
      </li>)}</ul> : <p className="mt-2 text-sm">No additional data gaps were reported. The SEC-only scope still applies.</p>}
      {result.warnings.length > 0 && <details className="mt-5 border-t pt-3"><summary className="cursor-pointer text-sm font-medium">Processing and calculation warnings ({result.warnings.length})</summary>
        <ul className="mt-3 list-disc space-y-2 break-words pl-5 text-sm">{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}
    </section>

    <section aria-labelledby="filing-coverage-heading" className="space-y-4">
      <h2 id="filing-coverage-heading" className="text-xl font-semibold">Filing coverage</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Summary label="Filing discovery" value={coverage ? coverage.coverage_complete ? "Complete for requested scope" : "Partial discovery" : "Unavailable"} />
        <Summary label="Supported financial coverage" value={result.financial_coverage_complete ? "Complete for supported metrics" : "Incomplete"} />
        <Summary label="Latest discovered filing" value={result.latest_filing_date ?? "Unavailable"} />
        <Summary label="Latest eligible financial period end" value={result.latest_financial_period_end ?? "Unavailable"} />
      </div>
      <p className="text-sm text-muted-foreground">Discovery completeness covers the requested filing-date interval and forms. It does not establish successful extraction, continuous financial periods or complete research coverage.</p>
      <p className="text-sm">Requested limits: {result.request.lookback_days} lookback days · {result.request.max_filings} filings · {result.request.max_archive_files} archive pages{result.request.refresh ? " · Refresh requested" : ""}</p>
      {coverage ? <>
        <p className="text-sm">Filing-date interval: {coverage.requested_start ?? "Unknown start"} to {coverage.as_of} · Forms: {coverage.forms.join(", ") || "Not reported"}</p>
        <p className="text-sm">Archive pages loaded: {coverage.archive_pages_loaded} of {coverage.archive_pages_available} advertised for this interval · Filings discovered: {coverage.filings_discovered} · Returned: {coverage.filings.length}</p>
        {coverage.warnings.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm">{coverage.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        {coverage.unresolved_amendments.length > 0 && <p className="break-words text-sm font-medium text-destructive">Unresolved amendments: {coverage.unresolved_amendments.join(", ")}</p>}
        {coverage.filings.length ? <Table><caption className="sr-only">Discovered filings and extraction results</caption><TableHeader><TableRow>
          <TableHead>Form / accession</TableHead><TableHead>Filed / accepted</TableHead><TableHead>Report date</TableHead><TableHead>Extraction</TableHead><TableHead>Source</TableHead>
        </TableRow></TableHeader><TableBody>{coverage.filings.map(filing => {
          const extraction = extracted.get(filing.accession_no);
          return <TableRow key={filing.accession_no}><TableCell className="align-top"><p className="font-medium">{filing.filing_type}</p><p className="mt-1 whitespace-nowrap text-xs">{filing.accession_no}</p></TableCell>
            <TableCell className="align-top text-xs"><p>{filing.filing_date}</p><p className="mt-1">Accepted: {filing.accepted_at ?? "Unknown"}</p></TableCell>
            <TableCell className="whitespace-nowrap align-top text-xs">{filing.report_date ?? "Unknown"}</TableCell>
            <TableCell className="max-w-xs align-top text-xs">{extraction ? <><p>{extraction.status === "extracted" ? `${extraction.observation_count} observations extracted` : "Extraction failed"}</p>{extraction.error && <p className="mt-1 break-words text-destructive">{extraction.error}</p>}</> : "No extraction result reported"}</TableCell>
            <TableCell className="align-top text-xs"><a href={filing.raw_url} target="_blank" rel="noopener noreferrer" className="underline">SEC filing</a></TableCell></TableRow>;
        })}</TableBody></Table> : <p className="text-sm">No supported filings were returned.</p>}
        {coverage.manifest_document_ids.length > 0 && <details className="rounded-lg border p-3 text-sm"><summary className="cursor-pointer">Discovery source snapshots ({coverage.manifest_document_ids.length})</summary>
          <p className="my-3 text-muted-foreground">Captured now. Raw manifests can list filings after the cutoff; those filings are excluded from the analysis inputs.</p>
          <ul className="space-y-2">{coverage.manifest_document_ids.map((id, index) => {
            const path = evidencePath(`/api/v1/historical-analyses/${result.job_id}/evidence/${id}`);
            return <li key={id} className="break-all">{path ? <a href={`${apiBaseUrl}${path}`} target="_blank" rel="noopener noreferrer" className="underline">Open discovery snapshot {index + 1}</a> : "Snapshot link unavailable"}</li>;
          })}</ul></details>}
      </> : <p className="text-sm">Discovery metadata is unavailable. No coverage is assumed.</p>}
    </section>

    <section aria-labelledby="historical-financials-heading"><h2 id="historical-financials-heading" className="mb-4 text-xl font-semibold">Verified financial contexts</h2>
      <FinancialFacts facts={result.financial_facts.map(source => source.observation)} />
      <p className="text-sm text-muted-foreground">Each row is a selected reported context, not a single current financial snapshot. Fiscal intervals, units and currencies are preserved.</p>
    </section>
    {result.calculations.length ? <Calculations records={result.calculations} /> : <section className="rounded-lg border p-4"><h2 className="font-semibold">Calculations unavailable</h2><p className="mt-2 text-sm text-muted-foreground">No verified calculation records were produced. Review missing data and processing warnings.</p></section>}
    {result.reconciliation && <details className="rounded-lg border p-4 text-sm"><summary className="cursor-pointer font-semibold">Filing version decisions ({result.reconciliation.decisions.length})</summary>
      <p className="mt-3 text-muted-foreground">Availability cutoff: {result.reconciliation.as_of}. Selection checks retained SEC contexts; changed reported values are not automatically a formal restatement diagnosis.</p>
      {result.reconciliation.decisions.map(decision => <section key={decision.context_id} className="mt-3 border-t pt-3">
        <p className="font-medium">{decision.metric.replaceAll("_", " ")}: {decision.status}</p><p>{decision.period.start ?? "As of"} to {decision.period.end}; {decision.reason}</p>
        {decision.versions.map(version => <p key={version.observation_id} className="mt-1 break-words text-xs">{version.value} {decision.unit} · {version.accession_no} · Filed {version.filing_date} · Accepted {version.accepted_at ?? "Unknown"} · {version.observation_id === decision.selected_observation_id ? "Selected" : "Retained alternative"}</p>)}
      </section>)}</details>}
    {result.retained_sources.length > 0 && <details className="rounded-lg border p-4 text-sm"><summary className="cursor-pointer font-semibold">Retained financial source passages ({result.retained_sources.length})</summary>
      {result.retained_sources.map((source, index) => <div key={source.observation.observation_id} className="mt-3 border-t pt-3">
        <p className="break-words">{source.observation.metric.replaceAll("_", " ")}: {source.observation.value} {source.observation.unit} · {source.observation.period.start ?? "As of"} to {source.observation.period.end} · {selected.has(source.observation.observation_id) ? "Selected context" : "Retained alternative"}
          <Citation id={`Historical ${index + 1}`} citation={{ source_type: "fact", source_id: source.observation.source.url, snippet: source.passages.map(p => p.text).join("\n"), passages: source.passages, observation_id: source.observation.observation_id }} /></p>
      </div>)}</details>}
    <p className="text-xs text-muted-foreground">Job {result.job_id} · Results are retained for 24 hours. Verified source and calculation records remain stored separately.</p>
  </article>;
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 rounded-lg border p-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 break-words text-sm font-semibold">{value}</p></div>;
}
