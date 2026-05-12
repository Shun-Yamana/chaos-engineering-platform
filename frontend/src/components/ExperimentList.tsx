import { useEffect, useState } from "react"
import { api, type Experiment } from "../api"
import { StatusBadge } from "./StatusBadge"

interface Props {
  onSelect: (id: string) => void
  onNew: () => void
  refreshKey: number
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

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Chaos Experiments</h1>
        <button
          onClick={onNew}
          className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 transition-colors"
        >
          + New Experiment
        </button>
      </div>

      {loading && <p className="text-gray-500 text-sm">Loading...</p>}
      {error && <p className="text-red-500 text-sm">{error}</p>}

      {!loading && experiments.length === 0 && (
        <p className="text-gray-400 text-sm">No experiments yet.</p>
      )}

      <div className="space-y-2">
        {experiments.map(exp => (
          <button
            key={exp.experiment_id}
            onClick={() => onSelect(exp.experiment_id)}
            className="w-full text-left px-4 py-3 bg-white border border-gray-200 rounded-lg hover:border-gray-300 hover:shadow-sm transition-all"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <StatusBadge status={exp.status} />
                <span className="font-medium text-gray-900 text-sm">{exp.name}</span>
              </div>
              <span className="text-xs text-gray-400 font-mono">{exp.experiment_id.slice(0, 8)}</span>
            </div>
            <div className="mt-1 flex gap-4 text-xs text-gray-500">
              <span>{exp.target_service}</span>
              <span className="font-mono bg-gray-100 px-1 rounded">{exp.fault_type}</span>
              <span>{exp.started_at.slice(0, 19).replace("T", " ")} UTC</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
