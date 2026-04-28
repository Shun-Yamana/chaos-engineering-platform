import { useState } from "react"
import { api, type StartPayload } from "../api"

interface Props {
  onBack: () => void
  onStarted: () => void
}

const FAULT_TYPES = ["pod_kill", "cpu_stress", "memory_stress", "http_error_inject", "network_latency"]
const SERVICES = ["service-a", "service-b"]

export function ExperimentForm({ onBack, onStarted }: Props) {
  const [form, setForm] = useState({
    name: "",
    service: "service-a",
    namespace: "default",
    fault_type: "network_latency",
    duration: 60,
    latency_ms: 2000,
    fault_rate: 0.5,
    error_rate_threshold: 0.05,
    burn_rate_threshold: 2.0,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const set = (key: string, value: string | number) =>
    setForm(f => ({ ...f, [key]: value }))

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
        ...(form.fault_type === "network_latency" && { latency_ms: form.latency_ms }),
        ...(form.fault_type === "http_error_inject" && { fault_rate: form.fault_rate }),
      },
      slo: {
        error_rate_threshold: form.error_rate_threshold,
        burn_rate_threshold: form.burn_rate_threshold,
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

  return (
    <div className="p-6 max-w-xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={onBack} className="text-gray-400 hover:text-gray-600 text-sm">← Back</button>
        <h1 className="text-2xl font-bold text-gray-900">New Experiment</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Name (optional)">
          <input
            type="text"
            placeholder="e.g. network-latency-test"
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

        <Field label="Fault Type">
          <select value={form.fault_type} onChange={e => set("fault_type", e.target.value)} className="input">
            {FAULT_TYPES.map(f => <option key={f}>{f}</option>)}
          </select>
        </Field>

        <Field label="Duration (seconds)">
          <input
            type="number"
            min={10}
            max={600}
            value={form.duration}
            onChange={e => set("duration", Number(e.target.value))}
            className="input"
          />
        </Field>

        {form.fault_type === "network_latency" && (
          <Field label="Latency (ms)">
            <input
              type="number"
              min={100}
              max={5000}
              value={form.latency_ms}
              onChange={e => set("latency_ms", Number(e.target.value))}
              className="input"
            />
          </Field>
        )}

        {form.fault_type === "http_error_inject" && (
          <Field label="Fault Rate (0.0 – 1.0)">
            <input
              type="number"
              min={0.1}
              max={1.0}
              step={0.1}
              value={form.fault_rate}
              onChange={e => set("fault_rate", Number(e.target.value))}
              className="input"
            />
          </Field>
        )}

        <div className="border-t pt-4">
          <p className="text-xs text-gray-500 mb-3 font-medium uppercase tracking-wide">SLO Thresholds</p>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Error Rate Threshold">
              <input
                type="number"
                min={0.01}
                max={1.0}
                step={0.01}
                value={form.error_rate_threshold}
                onChange={e => set("error_rate_threshold", Number(e.target.value))}
                className="input"
              />
            </Field>
            <Field label="Burn Rate Threshold">
              <input
                type="number"
                min={1.0}
                max={10.0}
                step={0.5}
                value={form.burn_rate_threshold}
                onChange={e => set("burn_rate_threshold", Number(e.target.value))}
                className="input"
              />
            </Field>
          </div>
        </div>

        {error && <p className="text-red-500 text-sm">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full py-2.5 bg-red-600 text-white font-medium rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
        >
          {submitting ? "Starting..." : "Start Experiment"}
        </button>
      </form>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {children}
    </div>
  )
}
