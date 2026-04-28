import { useState } from "react"
import { ExperimentList } from "./components/ExperimentList"
import { ExperimentForm } from "./components/ExperimentForm"
import { ExperimentDetail } from "./components/ExperimentDetail"

type View =
  | { type: "list" }
  | { type: "new" }
  | { type: "detail"; id: string }

export default function App() {
  const [view, setView] = useState<View>({ type: "list" })
  const [refreshKey, setRefreshKey] = useState(0)

  const refresh = () => setRefreshKey(k => k + 1)

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-3">
        <button
          onClick={() => { setView({ type: "list" }); refresh() }}
          className="flex items-center gap-2 hover:opacity-70 transition-opacity"
        >
          <span className="text-red-600 font-bold text-lg">⚡</span>
          <span className="font-semibold text-gray-900 text-sm">Chaos Platform</span>
        </button>
      </header>

      <main>
        {view.type === "list" && (
          <ExperimentList
            refreshKey={refreshKey}
            onSelect={id => setView({ type: "detail", id })}
            onNew={() => setView({ type: "new" })}
          />
        )}
        {view.type === "new" && (
          <ExperimentForm
            onBack={() => setView({ type: "list" })}
            onStarted={() => { setView({ type: "list" }); refresh() }}
          />
        )}
        {view.type === "detail" && (
          <ExperimentDetail
            experimentId={view.id}
            onBack={() => setView({ type: "list" })}
            onStopped={() => { setView({ type: "list" }); refresh() }}
          />
        )}
      </main>
    </div>
  )
}
