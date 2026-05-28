import { useState, useEffect } from "react"
import { api } from "../api"

export function TrafficControl() {
  const [running, setRunning] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.getTraffic()
      .then(d => setRunning(d.running))
      .catch(() => setRunning(false))
  }, [])

  const toggle = async () => {
    if (running === null) return
    setLoading(true)
    try {
      const next = !running
      await api.setTraffic(next)
      setRunning(next)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 bg-slate-800 rounded-lg border border-slate-700">
      <div className="flex items-center gap-2">
        <div className={`w-2 h-2 rounded-full ${
          running === null ? "bg-slate-500" :
          running ? "bg-green-400 animate-pulse" : "bg-slate-500"
        }`} />
        <span className="text-xs text-slate-300 font-medium">
          Traffic
        </span>
        <span className={`text-xs font-semibold ${
          running ? "text-green-400" : "text-slate-500"
        }`}>
          {running === null ? "—" : running ? "ON" : "OFF"}
        </span>
      </div>
      <button
        onClick={toggle}
        disabled={loading || running === null}
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
          running ? "bg-green-500" : "bg-slate-600"
        }`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
            running ? "translate-x-4" : "translate-x-1"
          }`}
        />
      </button>
    </div>
  )
}
