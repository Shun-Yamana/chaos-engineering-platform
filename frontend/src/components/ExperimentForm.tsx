import { useState } from "react"
import { api, type StartPayload } from "../api"

interface Props {
  onBack: () => void
  onStarted: () => void
}

const SERVICES = ["service-b"]

const FAULT_META: Record<string, { label: string; desc: string; icon: string; accent: string; border: string }> = {
  // K8s Layer — Chaos Mesh
  pod_kill:          { label: "Pod Kill",      desc: "Force-terminates a running pod",              icon: "✂",  accent: "text-red-600",    border: "border-red-300 bg-red-50"      },
  cpu_stress:        { label: "CPU Stress",    desc: "Saturates CPU in the container",              icon: "⚡", accent: "text-orange-600", border: "border-orange-300 bg-orange-50" },
  memory_stress:     { label: "Memory Stress", desc: "Exhausts available pod memory",               icon: "▣",  accent: "text-purple-600", border: "border-purple-300 bg-purple-50" },
  http_error_inject: { label: "HTTP Error",    desc: "Returns 5xx at configurable rate",            icon: "⚠",  accent: "text-yellow-600", border: "border-yellow-300 bg-yellow-50" },
  network_latency:   { label: "Net Latency",   desc: "Injects latency via Chaos Mesh",              icon: "⏱", accent: "text-blue-600",   border: "border-blue-300 bg-blue-50"    },
  // Infra Layer — AWS FIS
  node_failure:      { label: "Node Failure",  desc: "Terminates EC2 node; tests K8s reschedule",   icon: "⊗",  accent: "text-stone-600",  border: "border-stone-400 bg-stone-50"  },
  az_isolation:      { label: "AZ Isolation",  desc: "Disconnects ap-northeast-1a subnet (120s)",   icon: "⊘",  accent: "text-cyan-700",   border: "border-cyan-300 bg-cyan-50"    },
}

const K8S_FAULTS   = ["pod_kill", "cpu_stress", "memory_stress", "http_error_inject", "network_latency"]
const INFRA_FAULTS = ["node_failure", "az_isolation"]

const DEFAULT_DURATION: Record<string, number> = {
  pod_kill:          60,
  cpu_stress:        300,
  memory_stress:     300,
  http_error_inject: 600,
  network_latency:   300,
  node_failure:      180,
  az_isolation:      240,
}

export function ExperimentForm({ onBack: _onBack, onStarted }: Props) {
  const [form, setForm] = useState({
    name: "",
    service: "service-b",
    namespace: "default",
    fault_type: "network_latency",
    duration: DEFAULT_DURATION["network_latency"],
    latency_ms: 500,
    fault_rate: 0.5,
    error_rate_threshold: 0.05,
    burn_rate_threshold: 2.0,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const set = (key: string, value: string | number) =>
    setForm(f => ({
      ...f,
      [key]: value,
      ...(key === "fault_type" && { duration: DEFAULT_DURATION[value as string] ?? 300 }),
    }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError("")

    const payload: StartPayload = {
      name: form.name || `${form.fault_type}-${form.service}-${Date.now()}`,
      target: { namespace: form.namespace, service: form.service },
      fault: {
        type: form.fault_type,
        duration: form.duration,
        ...(form.fault_type === "network_latency"   && { latency_ms:  form.latency_ms }),
        ...(form.fault_type === "http_error_inject" && { fault_rate:  form.fault_rate }),
      },
      slo: {
        error_rate_threshold: form.error_rate_threshold,
        burn_rate_threshold:  form.burn_rate_threshold,
      },
    }

    try {
      await api.startExperiment(payload)
      onStarted()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start experiment")
    } finally {
      setSubmitting(false)
    }
  }

  const meta = FAULT_META[form.fault_type]

  return (
    <div className="max-w-2xl">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-slate-900">New Experiment</h1>
        <p className="text-sm text-slate-500 mt-0.5">Configure and launch a fault injection experiment</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">

        {/* Name */}
        <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-4">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Basic Info</p>
          <Field label="Experiment Name">
            <input
              type="text"
              placeholder="e.g. network-latency-canary"
              value={form.name}
              onChange={e => set("name", e.target.value)}
              className="input"
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Service">
              <select value={form.service} onChange={e => set("service", e.target.value)} className="input">
                {SERVICES.map(s => <option key={s}>{s}</option>)}
              </select>
            </Field>
            <Field label="Namespace">
              <input
                type="text"
                value={form.namespace}
                onChange={e => set("namespace", e.target.value)}
                className="input"
              />
            </Field>
          </div>
        </div>

        {/* Fault Type */}
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Fault Type</p>

          <p className="text-xs text-slate-400 mb-2">K8s Layer — Chaos Mesh</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
            {K8S_FAULTS.map(key => {
              const m = FAULT_META[key]
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => set("fault_type", key)}
                  className={`text-left px-3 py-2.5 rounded-lg border-2 transition-all ${
                    form.fault_type === key ? `${m.border} border-opacity-100` : "border-slate-200 bg-white hover:border-slate-300"
                  }`}
                >
                  <div className={`text-lg leading-none mb-1 ${form.fault_type === key ? m.accent : "text-slate-400"}`}>{m.icon}</div>
                  <div className={`text-xs font-semibold ${form.fault_type === key ? "text-slate-900" : "text-slate-600"}`}>{m.label}</div>
                  <div className="text-xs text-slate-400 mt-0.5 leading-snug">{m.desc}</div>
                </button>
              )
            })}
          </div>

          <p className="text-xs text-slate-400 mb-2 border-t border-slate-100 pt-3">Infrastructure Layer — AWS FIS</p>
          <div className="grid grid-cols-2 gap-2">
            {INFRA_FAULTS.map(key => {
              const m = FAULT_META[key]
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => set("fault_type", key)}
                  className={`text-left px-3 py-2.5 rounded-lg border-2 transition-all ${
                    form.fault_type === key ? `${m.border} border-opacity-100` : "border-slate-200 bg-white hover:border-slate-300"
                  }`}
                >
                  <div className={`text-lg leading-none mb-1 ${form.fault_type === key ? m.accent : "text-slate-400"}`}>{m.icon}</div>
                  <div className={`text-xs font-semibold ${form.fault_type === key ? "text-slate-900" : "text-slate-600"}`}>{m.label}</div>
                  <div className="text-xs text-slate-400 mt-0.5 leading-snug">{m.desc}</div>
                </button>
              )
            })}
          </div>
        </div>

        {/* Parameters */}
        <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-4">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Parameters</p>

          <Field label={`Duration — ${form.duration}s`}>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={INFRA_FAULTS.includes(form.fault_type) ? 120 : 10} max={600} step={10}
                value={form.duration}
                onChange={e => set("duration", Number(e.target.value))}
                className="flex-1 accent-red-600"
              />
              <input
                type="number"
                min={INFRA_FAULTS.includes(form.fault_type) ? 120 : 10} max={600}
                value={form.duration}
                onChange={e => set("duration", Number(e.target.value))}
                className="input w-20 text-center"
              />
            </div>
            {form.fault_type === "az_isolation" && (
              <p className="text-xs text-slate-400 mt-1.5">FIS template isolates subnet for 120s; duration sets total monitoring window.</p>
            )}
            {form.fault_type === "node_failure" && (
              <p className="text-xs text-slate-400 mt-1.5">EC2 node terminates immediately; duration covers K8s rescheduling observation.</p>
            )}
          </Field>

          {form.fault_type === "network_latency" && (
            <Field label={`Latency — ${form.latency_ms}ms`}>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={100} max={5000} step={100}
                  value={form.latency_ms}
                  onChange={e => set("latency_ms", Number(e.target.value))}
                  className="flex-1 accent-blue-600"
                />
                <input
                  type="number"
                  min={100} max={5000}
                  value={form.latency_ms}
                  onChange={e => set("latency_ms", Number(e.target.value))}
                  className="input w-20 text-center"
                />
              </div>
            </Field>
          )}

          {form.fault_type === "http_error_inject" && (
            <Field label={`Fault Rate — ${(form.fault_rate * 100).toFixed(0)}%`}>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={0.1} max={1.0} step={0.1}
                  value={form.fault_rate}
                  onChange={e => set("fault_rate", Number(e.target.value))}
                  className="flex-1 accent-yellow-500"
                />
                <input
                  type="number"
                  min={0.1} max={1.0} step={0.1}
                  value={form.fault_rate}
                  onChange={e => set("fault_rate", Number(e.target.value))}
                  className="input w-20 text-center"
                />
              </div>
            </Field>
          )}
        </div>

        {/* SLO */}
        <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-4">
          <div>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">SLO Thresholds</p>
            <p className="text-xs text-slate-400 mt-0.5">Experiment auto-stops if these thresholds are breached</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Error Rate">
              <div className="relative">
                <input
                  type="number"
                  min={0.01} max={1.0} step={0.01}
                  value={form.error_rate_threshold}
                  onChange={e => set("error_rate_threshold", Number(e.target.value))}
                  className="input pr-8"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400 pointer-events-none">
                  {(form.error_rate_threshold * 100).toFixed(0)}%
                </span>
              </div>
            </Field>
            <Field label="Burn Rate">
              <input
                type="number"
                min={1.0} max={10.0} step={0.5}
                value={form.burn_rate_threshold}
                onChange={e => set("burn_rate_threshold", Number(e.target.value))}
                className="input"
              />
            </Field>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-red-600 text-sm">{error}</div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className={`w-full py-3 font-semibold rounded-xl transition-all shadow-sm text-sm ${
            submitting
              ? "bg-slate-300 text-slate-500 cursor-not-allowed"
              : "bg-red-600 text-white hover:bg-red-700 active:bg-red-800"
          }`}
        >
          {submitting ? (
            <span className="flex items-center justify-center gap-2">
              <span className="animate-spin">⟳</span> Starting…
            </span>
          ) : (
            `Start ${meta?.label ?? "Experiment"}`
          )}
        </button>
      </form>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1.5">{label}</label>
      {children}
    </div>
  )
}
