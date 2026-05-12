import { useEffect, useRef, useState } from "react"
import { api, type Experiment } from "../api"
import { StatusBadge } from "./StatusBadge"

interface Props {
  experimentId: string
  onBack: () => void
  onStopped: () => void
}

const FAULT_COLORS: Record<string, string> = {
  pod_kill:          "bg-red-100 text-red-700",
  cpu_stress:        "bg-orange-100 text-orange-700",
  memory_stress:     "bg-purple-100 text-purple-700",
  http_error_inject: "bg-yellow-100 text-yellow-800",
  network_latency:   "bg-blue-100 text-blue-700",
}

const FAULT_LABELS: Record<string, string> = {
  pod_kill:          "Pod Kill",
  cpu_stress:        "CPU Stress",
  memory_stress:     "Memory Stress",
  http_error_inject: "HTTP Error",
  network_latency:   "Network Latency",
}

function fmt(iso: string) {
  return iso.slice(0, 19).replace("T", " ") + " UTC"
}

export function ExperimentDetail({ experimentId, onBack, onStopped }: Props) {
  const [exp, setExp] = useState<Experiment | null>(null)
  const [loading, setLoading] = useState(true)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState("")
  const [elapsed, setElapsed] = useState(0)

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timerRef   = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    const fetchData = () => {
      api.getExperiment(experimentId)
        .then(data => {
          if (cancelled) return
          setExp(data)
          if (data.status !== "running" && data.status !== "pending") {
            if (intervalRef.current) clearInterval(intervalRef.current)
            if (timerRef.current)   clearInterval(timerRef.current)
          }
        })
        .catch(e => { if (!cancelled) setError(e.message) })
        .finally(() => { if (!cancelled) setLoading(false) })
    }

    fetchData()
    intervalRef.current = setInterval(fetchData, 10_000)

    return () => {
      cancelled = true
      if (intervalRef.current) clearInterval(intervalRef.current)
      if (timerRef.current)   clearInterval(timerRef.current)
    }
  }, [experimentId])

  // Elapsed timer for running experiments
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (exp?.status === "running" && exp.started_at) {
      const tick = () => setElapsed(Math.floor((Date.now() - new Date(exp.started_at!).getTime()) / 1000))
      tick()
      timerRef.current = setInterval(tick, 1000)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [exp?.status, exp?.started_at])

  const handleStop = async () => {
    setStopping(true)
    try {
      await api.stopExperiment(experimentId)
      onStopped()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to stop")
      setStopping(false)
    }
  }

  if (loading) return (
    <div className="flex items-center gap-2 text-slate-400 text-sm py-8 justify-center">
      <span className="animate-spin">⟳</span> Loading…
    </div>
  )
  if (error) return (
    <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-red-600 text-sm">{error}</div>
  )
  if (!exp) return null

  const isActive  = exp.status === "running" || exp.status === "pending"
  const progress  = exp.status === "running"
    ? Math.min(100, (elapsed / exp.duration_seconds) * 100)
    : exp.status === "completed" ? 100 : 0

  return (
    <div className="max-w-2xl space-y-4">
      {/* Header card */}
      <div className="bg-white border border-slate-200 rounded-xl p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-slate-900 truncate">{exp.name}</h1>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <StatusBadge status={exp.status} size="md" />
              <span className={`text-xs font-medium px-2 py-0.5 rounded ${FAULT_COLORS[exp.fault_type] ?? "bg-slate-100 text-slate-600"}`}>
                {FAULT_LABELS[exp.fault_type] ?? exp.fault_type}
              </span>
              <span className="text-xs text-slate-400">{exp.target_service}</span>
            </div>
          </div>
          <span className="text-xs text-slate-400 font-mono shrink-0 bg-slate-50 px-2 py-1 rounded">
            {exp.experiment_id.slice(0, 8)}
          </span>
        </div>

        {/* Progress bar (running) */}
        {exp.status === "running" && (
          <div className="mt-4">
            <div className="flex justify-between text-xs text-slate-500 mb-1">
              <span>{elapsed}s elapsed</span>
              <span>{exp.duration_seconds}s total</span>
            </div>
            <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-500 rounded-full transition-all duration-1000"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {exp.status === "completed" && (
          <div className="mt-4">
            <div className="h-2 bg-green-100 rounded-full overflow-hidden">
              <div className="h-full bg-green-500 rounded-full w-full" />
            </div>
          </div>
        )}
      </div>

      {/* Details */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 bg-slate-50">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Details</p>
        </div>
        <table className="w-full text-sm">
          <tbody>
            {[
              ["Target Service",   exp.target_service],
              ["Namespace",        exp.namespace],
              ["Duration",         `${exp.duration_seconds}s`],
              ["Started",          fmt(exp.started_at)],
              ...(exp.stopped_at  ? [["Stopped",      fmt(exp.stopped_at)]]             : []),
              ...(exp.stop_reason ? [["Stop Reason",  exp.stop_reason]]                 : []),
              ...(exp.latency_ms  != null ? [["Latency",     `${exp.latency_ms}ms`]]    : []),
              ...(exp.fault_rate  != null ? [["Fault Rate",  `${(exp.fault_rate * 100).toFixed(0)}%`]] : []),
            ].map(([k, v]) => (
              <tr key={k} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-2.5 text-slate-500 text-xs w-36">{k}</td>
                <td className="px-4 py-2.5 text-slate-900 font-mono text-xs">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Experiment ID */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 flex items-center justify-between">
        <span className="text-xs text-slate-400">Experiment ID</span>
        <span className="text-xs font-mono text-slate-600">{exp.experiment_id}</span>
      </div>

      {/* Stop button */}
      {isActive && (
        <button
          onClick={handleStop}
          disabled={stopping}
          className={`w-full py-3 font-semibold rounded-xl transition-all text-sm ${
            stopping
              ? "bg-slate-200 text-slate-400 cursor-not-allowed"
              : "bg-slate-900 text-white hover:bg-slate-700 active:bg-black"
          }`}
        >
          {stopping ? (
            <span className="flex items-center justify-center gap-2">
              <span className="animate-spin">⟳</span> Stopping…
            </span>
          ) : "Stop Experiment"}
        </button>
      )}
    </div>
  )
}
