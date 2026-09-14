import type { MemoResponse } from "@/lib/api/client";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function FinancialFacts({ facts }: { facts: NonNullable<MemoResponse["financial_facts"]> }) {
  if (!facts.length) {
    return <p className="mb-6 text-sm text-muted-foreground">Structured financial observations are unavailable for this report.</p>;
  }
  return (
    <section className="mb-8" aria-label="Financial observations and provenance">
      <h3 className="mb-2 text-sm font-semibold">Reported financial observations</h3>
      <p className="mb-3 text-sm text-muted-foreground">Values are in the units shown. Unknown dates or currencies have not been assumed.</p>
      <Table>
        <TableHeader><TableRow>
          <TableHead>Metric</TableHead><TableHead>Value / unit</TableHead><TableHead>Reporting period</TableHead><TableHead>Source and provenance</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {facts.map((fact) => (
            <TableRow key={fact.observation_id}>
              <TableCell className="align-top">{fact.metric.replaceAll("_", " ")}</TableCell>
              <TableCell className="align-top tabular-nums">
                <div>{fact.value} {fact.unit}</div>
                <div className="text-xs text-muted-foreground">Currency: {fact.currency ?? (fact.unit === "ratio" || fact.unit === "shares" ? "Not applicable" : "Unknown")}</div>
              </TableCell>
              <TableCell className="align-top text-xs">
                <div>{fact.period.kind} / {fact.period.basis}</div>
                <div>{fact.period.kind === "duration" ? `${fact.period.start ?? "Unknown start"} to ${fact.period.end ?? "Unknown end"}` : fact.period.end ?? "Unknown date"}</div>
                {fact.period.start_method === "prior_annual_end" ? <p>Start derived from the preceding fiscal year end.</p> : null}
                {(fact.quality_flags ?? []).map((flag) => <p key={flag} className="mt-1 text-muted-foreground">{flag}</p>)}
              </TableCell>
              <TableCell className="max-w-sm align-top text-xs">
                <div>{fact.source.provider}</div>
                <a href={fact.source.url} target="_blank" rel="noopener noreferrer" className="underline">Provider record</a>
                {fact.source.document_url ? <a href={fact.source.document_url} target="_blank" rel="noopener noreferrer" className="ml-3 underline">Original document</a> : null}
                <div>Filed: {fact.source.filing_date ?? "Unknown"}</div>
                <div>Retrieved: {fact.source.fetched_at}</div>
                <details className="mt-1 break-all">
                  <summary className="cursor-pointer">Evidence identifiers</summary>
                  <div>Locator: {fact.source.locator}</div>
                  <div>Accession: {fact.source.accession_no ?? "Unavailable"}</div>
                  <div>Accepted: {fact.source.accepted_at ?? "Unknown timezone or unavailable"}</div>
                  {fact.period.start_evidence ? <div>Prior fiscal boundary: {fact.period.start_evidence.period_end}; record {fact.period.start_evidence.locator}; hash {fact.period.start_evidence.payload_hash}</div> : null}
                  <div>Observation: {fact.observation_id}</div>
                  <div>Payload hash: {fact.payload_hash}</div>
                </details>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}
