import { useEffect, useState } from "react"
import { api, type Experiment } from "../api"
import { StatusBadge } from "./StatusBadge"

interface Props {
  onSelect: (id: string) => void
  onNew: () => void
  refreshKey: number
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
  network_latency:   "Net Latency",
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export function ExperimentList({ onSelect, onNew, refreshKey }: Props) {
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    setLoading(true)
    api.listExperiments()
      .then(r => setExperiments(r.experiments))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [refreshKey])

  const running   = experiments.filter(e => e.status === "running").length
  const completed = experiments.filter(e => e.status === "completed").length
  const failed    = experiments.filter(e => e.status === "failed").length

  return (
    <div>
      {/* Page header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Experiments</h1>
          <p className="text-sm text-slate-500 mt-0.5">Inject faults and observe system resilience</p>
        </div>
        <button
          onClick={onNew}
          className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 active:bg-red-800 transition-colors shadow-sm"
        >
          <span className="text-base leading-none">+</span>
          New Experiment
        </button>
      </div>

      {/* Stats bar */}
      {experiments.length > 0 && (
        <div className="grid grid-cols-4 gap-3 mb-5">
          {[
            { label: "Total",     value: experiments.length, color: "text-slate-700" },
            { label: "Running",   value: running,            color: "text-amber-600" },
            { label: "Completed", value: completed,          color: "text-green-600" },
            { label: "Failed",    value: failed,             color: "text-red-600"   },
          ].map(s => (
            <div key={s.label} className="bg-white rounded-xl border border-slate-200 px-4 py-3">
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-slate-500 mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* List */}
      {loading && (
        <div className="flex items-center gap-2 text-slate-400 text-sm py-8 justify-center">
          <span className="animate-spin">⟳</span> Loading…
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-red-600 text-sm">{error}</div>
      )}

      {!loading && experiments.length === 0 && (
        <div className="text-center py-16">
          <div className="text-4xl mb-3">🔬</div>
          <p className="text-slate-700 font-medium">No experiments yet</p>
          <p className="text-slate-400 text-sm mt-1 mb-4">Start your first chaos experiment</p>
          <button
            onClick={onNew}
            className="px-5 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 transition-colors"
          >
            + New Experiment
          </button>
        </div>
      )}

      <div className="space-y-2">
        {experiments.map(exp => (
          <button
            key={exp.experiment_id}
            onClick={() => onSelect(exp.experiment_id)}
            className="w-full text-left bg-white border border-slate-200 rounded-xl px-4 py-3.5 hover:border-slate-300 hover:shadow-sm transition-all group"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <StatusBadge status={exp.status} />
                <span className="font-medium text-slate-900 text-sm truncate group-hover:text-red-600 transition-colors">
                  {exp.name || exp.experiment_id.slice(0, 8)}
                </span>
              </div>
              <span className="text-xs text-slate-400 font-mono shrink-0">{exp.experiment_id.slice(0, 8)}</span>
            </div>
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <span className="text-xs text-slate-500">{exp.target_service}</span>
              <span className="text-slate-300">·</span>
              <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${FAULT_COLORS[exp.fault_type] ?? "bg-slate-100 text-slate-600"}`}>
                {FAULT_LABELS[exp.fault_type] ?? exp.fault_type}
              </span>
              <span className="text-slate-300">·</span>
              <span className="text-xs text-slate-400">{relativeTime(exp.started_at)}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
