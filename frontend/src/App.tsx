import { useState, useEffect } from "react"
import { ExperimentList } from "./components/ExperimentList"
import { ExperimentForm } from "./components/ExperimentForm"
import { ExperimentDetail } from "./components/ExperimentDetail"
import { ProductDetailView } from "./components/ProductDetailView"
import { LoginPage } from "./components/LoginPage"
import { handleCallback, isAuthenticated, signOut } from "./auth"

type View =
  | { type: "list" }
  | { type: "new" }
  | { type: "detail"; id: string }
  | { type: "live" }

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
        .then(() => { window.history.replaceState({}, "", "/"); setAuthed(true) })
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
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <header className="bg-slate-900 border-b border-slate-800 px-6 py-3 flex items-center justify-between shrink-0">
        <button
          onClick={() => { setView({ type: "list" }); refresh() }}
          className="flex items-center gap-3 hover:opacity-80 transition-opacity"
        >
          <div className="w-8 h-8 bg-red-600 rounded-lg flex items-center justify-center text-white font-bold text-base shadow-sm">
            ⚡
          </div>
          <div className="text-left">
            <div className="text-white font-semibold text-sm leading-tight">Chaos Platform</div>
            <div className="text-slate-400 text-xs leading-tight">Experiment Control</div>
          </div>
        </button>

        <nav className="flex items-center gap-1">
          <button
            onClick={() => setView({ type: "live" })}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
              view.type === "live"
                ? "text-white bg-slate-700"
                : "text-slate-400 hover:text-white hover:bg-slate-800"
            }`}
          >
            Demo
          </button>
          <button
            onClick={() => { setView({ type: "list" }); refresh() }}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
              view.type !== "live"
                ? "text-white bg-slate-700"
                : "text-slate-400 hover:text-white hover:bg-slate-800"
            }`}
          >
            Experiments
          </button>
          {import.meta.env.VITE_GRAFANA_URL && (
            <a
              href={import.meta.env.VITE_GRAFANA_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-md hover:bg-slate-800 transition-colors flex items-center gap-1"
            >
              Grafana
              <svg className="w-2.5 h-2.5 opacity-60" fill="none" viewBox="0 0 10 10" stroke="currentColor" strokeWidth={1.5}>
                <path d="M1 9L9 1M9 1H3M9 1v6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </a>
          )}
          <button
            onClick={signOut}
            className="text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-md hover:bg-slate-800 transition-colors"
          >
            Sign out
          </button>
        </nav>
      </header>

      <main className="flex-1 w-full max-w-4xl mx-auto px-4 sm:px-6 py-6">
        {view.type === "live" && <ProductDetailView />}
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
