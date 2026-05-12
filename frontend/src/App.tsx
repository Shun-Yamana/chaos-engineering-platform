import { useState, useEffect } from "react"
import { ExperimentList } from "./components/ExperimentList"
import { ExperimentForm } from "./components/ExperimentForm"
import { ExperimentDetail } from "./components/ExperimentDetail"
import { LoginPage } from "./components/LoginPage"
import { handleCallback, isAuthenticated, signOut } from "./auth"

type View =
  | { type: "list" }
  | { type: "new" }
  | { type: "detail"; id: string }

export default function App() {
  const [authed, setAuthed] = useState(false)
  const [authLoading, setAuthLoading] = useState(true)
  const [view, setView] = useState<View>({ type: "list" })
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get("code")

    if (code) {
      handleCallback(code)
        .then(() => {
          window.history.replaceState({}, "", "/")
          setAuthed(true)
        })
        .catch(console.error)
        .finally(() => setAuthLoading(false))
    } else {
      setAuthed(isAuthenticated())
      setAuthLoading(false)
    }
  }, [])

  const refresh = () => setRefreshKey(k => k + 1)

  if (authLoading) return null

  if (!authed) return <LoginPage />

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <button
          onClick={() => { setView({ type: "list" }); refresh() }}
          className="flex items-center gap-2 hover:opacity-70 transition-opacity"
        >
          <span className="text-red-600 font-bold text-lg">⚡</span>
          <span className="font-semibold text-gray-900 text-sm">Chaos Platform</span>
        </button>
        <button
          onClick={signOut}
          className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
        >
          Sign out
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
