import type { components } from "@/types/api";

type Status = components["schemas"]["JobStatus"]["status"];

export function isJobPending(status: Status | undefined): boolean {
  return status === "queued" || status === "running";
}

export function isReportReady(status: Status | undefined): boolean {
  return status === "complete" || status === "partial";
}
