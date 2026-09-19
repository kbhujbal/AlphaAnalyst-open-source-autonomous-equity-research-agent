"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Container } from "@/components/layout/container";
import { HistoricalResult } from "@/components/historical/historical-result";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Progress } from "@/components/ui/progress";
import { ApiError } from "@/lib/api/client";
import { useHistoricalAnalysis, useJobStatus } from "@/lib/api/hooks";
import { canLoadHistoricalResult } from "@/lib/api/historical";
import { isJobPending } from "@/lib/api/job-state";

export default function HistoricalJobPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const job = useJobStatus(jobId ?? null);
  const result = useHistoricalAnalysis(jobId ?? null, canLoadHistoricalResult(job.data));
  let content;
  if (job.isLoading) content = <LoadingSpinner label="Loading historical job…" />;
  else if (job.isError) content = <ErrorState title="Historical job unavailable" description={job.error instanceof ApiError && job.error.status === 404
    ? "This job was not found or has expired. Historical job links are retained for 24 hours." : job.error.message} onRetry={() => job.refetch()} />;
  else if (!job.data) content = <ErrorState title="Historical job not found" />;
  else if (job.data.analysis_kind !== "historical-financials") content = <ErrorState title="This is a live analysis job" description={<Link className="underline" href={`/analysis/${jobId}`}>Open the live analysis</Link>} />;
  else if (isJobPending(job.data.status)) content = <section className="mx-auto max-w-xl space-y-5" aria-live="polite">
    <h1 className="text-2xl font-semibold">{job.data.status === "queued" ? "Historical analysis queued" : "Analyzing historical filings"}</h1>
    <p>Availability cutoff: <strong>{job.data.as_of ?? "Unavailable"} (UTC end of day)</strong></p>
    <p className="text-sm text-muted-foreground">Discovering filings, checking retained evidence and calculating supported financial metrics. Filing coverage and missing data will appear when processing finishes.</p>
    <Progress value={job.data.progress_pct} aria-label="Historical processing progress" />
    <p className="text-sm text-muted-foreground">{job.data.progress_pct}% processing progress. This is not a coverage score. You can return to this link within 24 hours.</p>
  </section>;
  else if (!canLoadHistoricalResult(job.data)) content = <ErrorState title="Historical job could not finish" description={job.data.error ?? "An unexpected job state was returned."} onRetry={() => job.refetch()} />;
  else if (result.isLoading || result.isFetching && !result.data) content = <LoadingSpinner label="Loading filing coverage and historical results…" />;
  else if (result.isError) content = <ErrorState title="Historical result unavailable" description={result.error instanceof ApiError && result.error.status === 404
    ? "The result is not available yet or has expired. Try again; results are retained for 24 hours." : "The historical result could not be loaded or validated. Try again; no financial data has been substituted."} onRetry={() => result.refetch()} />;
  else if (result.data && result.data.as_of === job.data.as_of) content = <HistoricalResult result={result.data} />;
  else content = <ErrorState title="Historical result does not match this job" description="The cutoff could not be confirmed. No financial result is displayed." onRetry={() => result.refetch()} />;

  return <Container className="space-y-6 py-8"><Link href="/historical" className="inline-block text-sm underline underline-offset-4">New historical analysis</Link>{content}</Container>;
}
