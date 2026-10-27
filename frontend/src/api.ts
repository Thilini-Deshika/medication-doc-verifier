export type StageStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Stage {
  name: string;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  detail: Record<string, unknown> | null;
}

export interface Job {
  id: string;
  status: JobStatus;
  current_stage: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  document: { id: string; filename: string; title: string; page_count: number | null };
  reference: { id: string; title: string };
  stages: Stage[];
  optimistic?: boolean;
}

export interface Citation {
  id: string;
  chunk_id: string;
  role: "supporting" | "contradicting";
  parameter: string | null;
  quote: string;
  page_start: number;
  page_end: number;
  section_path: string;
  passage_text: string;
  reference_id: string;
}

export interface ParameterCheck {
  parameter: string;
  prescribed: string | null;
  reference: string | null;
  result: "match" | "mismatch" | "not_addressed";
  severity: "high" | "moderate" | "low" | "none";
  explanation: string;
}

export interface Verification {
  status: "supported" | "contradicted" | "unsupported";
  severity: "high" | "moderate" | "low" | "none";
  explanation: string;
  matched_monograph: string | null;
  name_match: string | null;
  parameter_checks: ParameterCheck[];
  citations: Citation[];
}

export interface Medication {
  id: string;
  drug_name: string;
  dose_value: number | null;
  dose_unit: string | null;
  route: string | null;
  frequency: string | null;
  timing: string | null;
  duration: string | null;
  indication: string | null;
  source_page: number | null;
  source_quote: string | null;
  verification: Verification | null;
}

export interface CriticalPoint {
  id: string;
  rank: number;
  category: string;
  priority: number;
  text: string;
  source_quote: string;
  source_page: number | null;
  related_drug: string | null;
}

export interface JobResult {
  job_id: string;
  status: JobStatus;
  summary: string | null;
  critical_points: CriticalPoint[];
  medications: Medication[];
}

export interface Reference {
  id: string;
  title: string;
  page_count: number;
}

export interface User {
  id: string;
  email: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => request<User>("/api/auth/me"),
  login: (email: string, password: string) => request<User>("/api/auth/login", json("POST", { email, password })),
  signup: (email: string, password: string) => request<User>("/api/auth/signup", json("POST", { email, password })),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  references: () => request<Reference[]>("/api/references"),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  result: (id: string) => request<JobResult>(`/api/jobs/${id}/result`),
  retry: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  upload: (file: File, referenceId: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("reference_id", referenceId);
    return request<{ duplicate: boolean; job: Job }>("/api/jobs", { method: "POST", body: form });
  },
};

export const documentUrl = (jobId: string) => `/api/jobs/${jobId}/document`;
export const referenceUrl = (referenceId: string) => `/api/references/${referenceId}/file`;

export const MAX_UPLOAD_MB = 15;
export const STAGE_ORDER = ["parsed", "chunked", "embedded", "summarized", "key_points", "entities", "verified", "done"];
