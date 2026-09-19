"use client";

import Link from "next/link";
import { canLoadLiveMemo } from "@/lib/api/historical";
import { useParams } from "next/navigation";

import { ProgressView } from "@/components/analysis/progress-view";
import { Container } from "@/components/layout/container";
import { MemoView } from "@/components/memo/memo-view";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { isJobPending } from "@/lib/api/job-state";
import { useJobStatus, useMemo as useMemoQuery } from "@/lib/api/hooks";

export default function AnalysisPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId ?? null;

  const job = useJobStatus(jobId);
  const memoQuery = useMemoQuery(
    jobId,
    canLoadLiveMemo(job.data),
  );

  if (job.isLoading) {
    return (
      <Container className="py-12">
        <LoadingSpinner label="Loading analysis…" />
      </Container>
    );
  }

  if (job.isError) {
    return (
      <Container className="py-12">
        <ErrorState
          title="Could not load this job"
          description={job.error.message}
          onRetry={() => job.refetch()}
        />
      </Container>
    );
  }

  if (!job.data) {
    return (
      <Container className="py-12">
        <ErrorState
          title="Job not found"
          description={`No job with id ${jobId ?? "(missing)"} was found.`}
        />
      </Container>
    );
  }

  if (job.data.analysis_kind === "historical-financials") {
    return <Container className="py-12"><h1 className="text-2xl font-semibold">Historical financial analysis</h1>
      <Link className="mt-4 inline-block underline" href={`/historical/${jobId}`}>View historical progress, filing coverage and results</Link></Container>;
  }

  if (job.data.status === "failed" || job.data.status === "error") {
    return (
      <Container className="py-12">
        <ErrorState
          title="Analysis failed"
          description={
            job.data.error ?? "The pipeline encountered an unrecoverable error."
          }
        />
      </Container>
    );
  }

  if (isJobPending(job.data.status)) {
    return (
      <Container className="py-12">
        <ProgressView
          progressPct={job.data.progress_pct}
          currentStep={job.data.current_step ?? null}
        />
      </Container>
    );
  }

  // Both complete and partial reports have readable memos.
  if (memoQuery.isLoading) {
    return (
      <Container className="py-12">
        <LoadingSpinner label="Loading memo…" />
      </Container>
    );
  }

  if (memoQuery.isError) {
    return (
      <Container className="py-12">
        <ErrorState
          title="Could not load memo"
          description={memoQuery.error.message}
          onRetry={() => memoQuery.refetch()}
        />
      </Container>
    );
  }

  if (!memoQuery.data) {
    return (
      <Container className="py-12">
        <LoadingSpinner label="Loading memo…" />
      </Container>
    );
  }

  return (
    <Container className="py-8">
      <MemoView response={memoQuery.data} />
    </Container>
  );
}
