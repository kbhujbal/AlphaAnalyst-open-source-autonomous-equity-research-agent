"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { HistoricalAnalysisZ } from "./historical";
import { isJobPending } from "./job-state";
import { ApiError, getJob, getMemo, startAnalysis, startHistoricalAnalysis, getHistoricalAnalysis } from "./client";

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


export function useStartHistoricalAnalysis() {
  return useMutation({ mutationFn: startHistoricalAnalysis, retry: false });
}

export function useHistoricalAnalysis(jobId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["historical-analysis", jobId],
    queryFn: async () => {
      const result = HistoricalAnalysisZ.parse(await getHistoricalAnalysis(jobId!));
      if (result.job_id !== jobId) throw new Error("Historical result belongs to another job.");
      return result;
    },
    enabled: Boolean(jobId) && enabled,
    retry: false,
  });
}
