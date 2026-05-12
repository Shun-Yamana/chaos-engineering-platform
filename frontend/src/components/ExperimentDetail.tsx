import { useEffect, useRef, useState } from "react"
import { api, type Experiment } from "../api"
import { StatusBadge } from "./StatusBadge"

interface Props {
  experimentId: string
  onBack: () => void
  onStopped: () => void
}

export function ExperimentDetail({ experimentId, onBack, onStopped }: Props) {
  const [exp, setExp] = useState<Experiment | null>(null)
  const [loading, setLoading] = useState(true)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState("")

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    const fetchData = () => {
      api.getExperiment(experimentId)
        .then(data => {
          if (cancelled) return
          setExp(data)
          if (data.status !== "running" && data.status !== "pending") {
            if (intervalRef.current) clearInterval(intervalRef.current)
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
    }
  }, [experimentId])

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

  if (loading) return <div className="p-6 text-gray-500 text-sm">Loading...</div>
  if (error) return <div className="p-6 text-red-500 text-sm">{error}</div>
  if (!exp) return null

  const rows: [string, string][] = [
    ["Experiment ID", exp.experiment_id],
    ["Service", exp.target_service],
    ["Namespace", exp.namespace],
    ["Fault Type", exp.fault_type],
    ["Duration", `${exp.duration_seconds}s`],
    ["Started", exp.started_at.slice(0, 19).replace("T", " ") + " UTC"],
    ...(exp.stopped_at ? [["Stopped", exp.stopped_at.slice(0, 19).replace("T", " ") + " UTC"] as [string, string]] : []),
    ...(exp.stop_reason ? [["Stop Reason", exp.stop_reason] as [string, string]] : []),
    ...(exp.latency_ms != null ? [["Latency", `${exp.latency_ms}ms`] as [string, string]] : []),
    ...(exp.fault_rate != null ? [["Fault Rate", String(exp.fault_rate)] as [string, string]] : []),
  ]

  return (
    <div className="p-6 max-w-xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={onBack} className="text-gray-400 hover:text-gray-600 text-sm">← Back</button>
        <h1 className="text-2xl font-bold text-gray-900">{exp.name}</h1>
        <StatusBadge status={exp.status} />
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden mb-4">
        <table className="w-full text-sm">
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k} className="border-b border-gray-100 last:border-0">
                <td className="px-4 py-2.5 text-gray-500 w-40">{k}</td>
                <td className="px-4 py-2.5 text-gray-900 font-mono text-xs">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(exp.status === "running" || exp.status === "pending") && (
        <button
          onClick={handleStop}
          disabled={stopping}
          className="w-full py-2.5 bg-gray-900 text-white font-medium rounded-lg hover:bg-gray-700 disabled:opacity-50 transition-colors"
        >
          {stopping ? "Stopping..." : "Stop Experiment"}
        </button>
      )}
    </div>
  )
}
