import { getToken } from "./auth"

const BASE = import.meta.env.VITE_API_ENDPOINT ?? ""
if (!BASE && import.meta.env.PROD) {
  console.error("[api] VITE_API_ENDPOINT is not set — all API calls will fail")
}

export interface CriterionResult {
  criterion: string
  value: number | null
  threshold: string
  pass: boolean | null
  note?: string
  ttr_actual_seconds?: number
  ttr_limit_seconds?: number
}

export interface EvaluationDetails {
  phase_a_absorption: CriterionResult[]
  phase_b_recovery: CriterionResult[]
  safety_net: {
    auto_stopper_fired: boolean
    expected: boolean
    pass: boolean
  }
}

export interface Experiment {
  experiment_id: string
  name: string
  target_service: string
  namespace: string
  fault_type: string
  duration_seconds: number
  status: "pending" | "running" | "completed" | "stopped" | "failed"
  started_at: string
  stopped_at?: string
  stop_reason?: string
  latency_ms?: number
  fault_rate?: number
  error_rate_threshold?: number
  burn_rate_threshold?: number
  final_error_rate?: number
  final_burn_rate?: number
  evaluation_result?: "pass" | "fail"
  evaluation_details?: EvaluationDetails
  evaluated_at?: string
}

export interface StartPayload {
  name: string
  target: { namespace: string; service: string }
  fault: {
    type: string
    duration: number
    latency_ms?: number
    fault_rate?: number
  }
  slo: {
    error_rate_threshold: number
    burn_rate_threshold: number
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (body) headers["Content-Type"] = "application/json"
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error ?? `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  listExperiments: (service?: string) =>
    req<{ experiments: Experiment[] }>("GET", `/experiments${service ? `?service=${service}` : ""}`),
  getExperiment: (id: string) =>
    req<Experiment>("GET", `/experiments/${id}`),
  startExperiment: (payload: StartPayload) =>
    req<{ experiment_id: string; status: string }>("POST", "/experiments", payload),
  stopExperiment: (id: string) =>
    req<{ experiment_id: string; status: string }>("DELETE", `/experiments/${id}`, { reason: "manual" }),
}
