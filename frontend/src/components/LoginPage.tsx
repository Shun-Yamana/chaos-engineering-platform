import { login } from "../auth"

export function LoginPage() {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="bg-white border border-gray-200 rounded-xl p-10 w-full max-w-sm text-center shadow-sm">
        <span className="text-red-600 font-bold text-3xl">⚡</span>
        <h1 className="text-xl font-bold text-gray-900 mt-2">Chaos Platform</h1>
        <p className="text-sm text-gray-500 mt-1 mb-8">Sign in to manage chaos experiments</p>
        <button
          onClick={login}
          className="w-full py-2.5 bg-red-600 text-white font-medium rounded-lg hover:bg-red-700 transition-colors"
        >
          Sign in
        </button>
      </div>
    </div>
  )
}
