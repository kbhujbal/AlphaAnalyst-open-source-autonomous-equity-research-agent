import createClient from "openapi-fetch";
import type { components, paths } from "@/types/api";

export type MemoResponse = components["schemas"]["MemoResponse"];
export type JobStatus = components["schemas"]["JobStatus"];
type AnalyzeRequest = components["schemas"]["AnalyzeRequest"];

export const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const client = createClient<paths>({ baseUrl: apiBaseUrl });

export class ApiError extends Error {
  public status: number;
  public detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
    this.name = "ApiError";
  }
}

function unwrap<T>(data: T | undefined, error: unknown, response: Response): T {
  if (!response.ok || data === undefined) {
    const detail = typeof error === "object" && error !== null && "detail" in error
      ? error.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return data;
}

export async function startAnalysis(body: AnalyzeRequest) {
  const { data, error, response } = await client.POST("/api/v1/analyze", { body });
  return unwrap(data, error, response);
}

export async function getJob(jobId: string): Promise<JobStatus> {
  const { data, error, response } = await client.GET("/api/v1/jobs/{job_id}", {
    params: { path: { job_id: jobId } },
  });
  return unwrap(data, error, response);
}

export async function getMemo(jobId: string): Promise<MemoResponse> {
  const { data, error, response } = await client.GET("/api/v1/memos/{job_id}", {
    params: { path: { job_id: jobId } },
  });
  return unwrap(data, error, response);
}


export type HistoricalAnalysisRequest = components["schemas"]["HistoricalAnalysisRequest-Input"];
export type HistoricalAnalysisResponse = components["schemas"]["HistoricalAnalysisResponse"];

export async function startHistoricalAnalysis(body: HistoricalAnalysisRequest) {
  const { data, error, response } = await client.POST("/api/v1/historical-analyses", { body });
  return unwrap(data, error, response);
}

export async function getHistoricalAnalysis(jobId: string): Promise<HistoricalAnalysisResponse> {
  const { data, error, response } = await client.GET("/api/v1/historical-analyses/{job_id}", {
    params: { path: { job_id: jobId } },
  });
  return unwrap(data, error, response);
}
