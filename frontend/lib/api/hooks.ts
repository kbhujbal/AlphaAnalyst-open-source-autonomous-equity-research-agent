"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { isJobPending } from "./job-state";
import { ApiError, getJob, getMemo, startAnalysis } from "./client";

export function useStartAnalysis() {
  return useMutation({ mutationFn: startAnalysis, retry: false });
}

export function useJobStatus(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId!),
    enabled: Boolean(jobId),
    staleTime: 0,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return isJobPending(status) ? 2000 : false;
    },
  });
}

export function useMemo(jobId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["memo", jobId],
    queryFn: () => getMemo(jobId!),
    enabled: Boolean(jobId) && enabled,
    retry: false,
  });
}
