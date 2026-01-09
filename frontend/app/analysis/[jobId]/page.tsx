"use client";

import { useParams } from "next/navigation";

import { ProgressView } from "@/components/analysis/progress-view";
import { Container } from "@/components/layout/container";
import { MemoView } from "@/components/memo/memo-view";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { useJobStatus, useMemo as useMemoQuery } from "@/lib/api/hooks";

export default function AnalysisPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId ?? null;

  const job = useJobStatus(jobId);
  const memoQuery = useMemoQuery(
    jobId,
    job.data?.status === "complete",
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

  if (job.data.status === "error") {
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

  if (job.data.status === "queued" || job.data.status === "running") {
    return (
      <Container className="py-12">
        <ProgressView
          progressPct={job.data.progress_pct}
          currentStep={job.data.current_step ?? null}
        />
      </Container>
    );
  }

  // status === "complete"
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
