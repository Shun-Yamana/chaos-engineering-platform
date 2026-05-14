import { useState, useEffect, useRef, useCallback } from "react"

interface ProductData {
  product_id: string
  name: string
  price: number | null
  stock: number | null
  rating: number | null
  review_count: number | null
  review_summary: string
  recommendation_reason: string
  updated_at: string | null
}

interface ResilienceMeta {
  source: "fresh" | "stale_cache" | "fallback"
  cache_age_seconds: number | null
}

interface AggregateResponse {
  product: ProductData
  resilience: ResilienceMeta
}

const SERVICE_A_BASE = import.meta.env.VITE_SERVICE_A_ENDPOINT ?? ""

export function ProductDetailView() {
  const [data, setData]       = useState<AggregateResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`${SERVICE_A_BASE}/aggregate/products/p-001`, {
        signal: AbortSignal.timeout(3000),
      })
      if (!res.ok) throw new Error()
      const body: AggregateResponse = await res.json()
      setData(body)
    } catch {
      // エラー時もデータを保持（stale cache は service-a 側で処理済み）
      setData(prev => prev ?? null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    poll()
    timerRef.current = setInterval(poll, 1000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [poll])

  if (loading) return (
    <div className="flex items-center justify-center py-24 text-slate-300 text-sm">
      <span className="animate-spin mr-2 text-xl">⟳</span>
    </div>
  )

  // service-a/b が両方ダウンしてレスポンスなし
  if (!data) return <ErrorPage />

  const { product: p, resilience: r } = data

  if (r.source === "fallback") return <FallbackPage />

  return (
    <div className="max-w-lg mx-auto space-y-6 py-2">
      <ProductCard product={p} />
      {r.source === "stale_cache" && (
        <StaleNotice ageSeconds={r.cache_age_seconds} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

function ProductCard({ product: p }: { product: ProductData }) {
  return (
    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
      {/* Image placeholder */}
      <div className="h-48 bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center">
        <span className="text-6xl select-none">⌨️</span>
      </div>

      <div className="p-6 space-y-5">
        {/* Name + price */}
        <div>
          <p className="text-xs text-slate-400 mb-1">{p.product_id}</p>
          <h1 className="text-2xl font-bold text-slate-900 leading-snug">{p.name}</h1>
          <div className="flex items-center gap-3 mt-2">
            {p.price != null && (
              <span className="text-2xl font-bold text-slate-900">
                ¥{p.price.toLocaleString()}
              </span>
            )}
            {p.rating != null && (
              <span className="text-sm text-amber-500">
                ★{p.rating}
                {p.review_count != null && (
                  <span className="text-slate-400 font-normal ml-1 text-xs">
                    ({p.review_count.toLocaleString()}件)
                  </span>
                )}
              </span>
            )}
          </div>
        </div>

        {/* Stock */}
        {p.stock != null && (
          <div>
            {p.stock <= 5 ? (
              <span className="text-xs font-medium text-red-600 bg-red-50 px-2.5 py-1 rounded-full">
                残り{p.stock}個
              </span>
            ) : (
              <span className="text-xs text-slate-500 bg-slate-50 px-2.5 py-1 rounded-full">
                在庫あり（{p.stock}個）
              </span>
            )}
          </div>
        )}

        {/* Divider */}
        <hr className="border-slate-100" />

        {/* Review summary */}
        <div>
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
            カスタマーレビュー
          </p>
          <p className="text-sm text-slate-600 leading-relaxed">{p.review_summary}</p>
        </div>

        {/* Recommendation */}
        {p.recommendation_reason && (
          <div className="bg-blue-50 rounded-xl px-4 py-3">
            <p className="text-xs font-semibold text-blue-700 mb-0.5">おすすめポイント</p>
            <p className="text-sm text-blue-800">{p.recommendation_reason}</p>
          </div>
        )}

        {/* CTA */}
        <div className="flex gap-3 pt-1">
          <button className="flex-1 bg-slate-900 text-white text-sm font-semibold py-3 rounded-xl hover:bg-slate-700 transition-colors">
            カートに追加
          </button>
          <button className="flex-1 border border-slate-300 text-slate-700 text-sm font-semibold py-3 rounded-xl hover:bg-slate-50 transition-colors">
            今すぐ購入
          </button>
        </div>
      </div>
    </div>
  )
}

function StaleNotice({ ageSeconds }: { ageSeconds: number | null }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-3">
      <span className="text-amber-500 text-base mt-0.5 shrink-0">⚠</span>
      <div>
        <p className="text-sm font-medium text-amber-800">キャッシュされた情報を表示しています</p>
        {ageSeconds != null && (
          <p className="text-xs text-amber-600 mt-0.5">最終更新: {ageSeconds}秒前</p>
        )}
      </div>
    </div>
  )
}

function FallbackPage() {
  return (
    <div className="max-w-lg mx-auto py-2">
      <div className="bg-white border border-slate-200 rounded-2xl p-10 text-center space-y-4">
        <div className="text-5xl">🛒</div>
        <div>
          <h2 className="text-lg font-bold text-slate-700">商品情報を一時的に取得できません</h2>
          <p className="text-sm text-slate-400 mt-2">しばらくしてから再度お試しください</p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="text-sm text-slate-500 border border-slate-200 px-4 py-2 rounded-lg hover:bg-slate-50 transition-colors"
        >
          再読み込み
        </button>
      </div>
    </div>
  )
}

function ErrorPage() {
  return (
    <div className="max-w-lg mx-auto py-2">
      <div className="bg-white border border-slate-200 rounded-2xl p-10 text-center space-y-3">
        <div className="text-5xl">🛒</div>
        <h2 className="text-lg font-bold text-slate-700">商品情報を一時的に取得できません</h2>
        <p className="text-sm text-slate-400">しばらくしてから再度お試しください</p>
      </div>
    </div>
  )
}
