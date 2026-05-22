import { useState, useEffect, useRef, useCallback } from "react"

// ── Types ──────────────────────────────────────────────────────────────────

interface ReviewData {
  product_id: string
  rating_distribution: Record<string, number>
  top_keywords: string[]
  recommendations: string[]
  recommendation_reason: string
  _source?: "fresh"
}

interface ProductData {
  product_id: string
  name: string
  price: number
  stock: number
  rating: number
  review_count: number
  review_summary: string
  recommendation_reason: string
  updated_at: string
  reviews: ReviewData | null
}

interface InventoryData {
  product_id: string
  stock: number
  price: number
  sale_price: number | null
  available: boolean
  warehouse: string
  restocked_at: string
}

interface ResilienceMeta {
  product_source: "fresh" | "stale_cache" | "fallback"
  inventory_source: "fresh" | "fallback" | "circuit_open"
  stale: boolean
  fallback: boolean
  inventory_available: boolean
  cache_age_seconds: number | null
  service_a_latency_ms: number | null
  service_b_latency_ms: number | null
  service_d_latency_ms: number | null
  circuit_state: "closed" | "open" | "half_open"
  inventory_circuit_state: "closed" | "open" | "half_open"
}

interface AggregateResponse {
  product: ProductData
  inventory: InventoryData | null
  resilience: ResilienceMeta
}

// ── Constants ──────────────────────────────────────────────────────────────

const SERVICE_A_BASE = import.meta.env.VITE_SERVICE_A_ENDPOINT ?? "http://localhost:8080"

const PRODUCTS = [
  { id: "p-001", label: "Keyboard",  icon: "⌨️" },
  { id: "p-002", label: "Chair",     icon: "🪑" },
  { id: "p-003", label: "Monitor",   icon: "🖥️" },
] as const

type ProductId = typeof PRODUCTS[number]["id"]

// ── Root component ─────────────────────────────────────────────────────────

export function ProductDetailView() {
  const [selected, setSelected] = useState<ProductId>("p-001")
  const [data, setData]         = useState<AggregateResponse | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(false)
  const [tick, setTick]         = useState(0)           // blink indicator
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const poll = useCallback(async (id: ProductId) => {
    try {
      const res = await fetch(`${SERVICE_A_BASE}/aggregate/products/${id}`, {
        signal: AbortSignal.timeout(4000),
      })
      if (!res.ok) throw new Error()
      const body: AggregateResponse = await res.json()
      setData(body)
      setError(false)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
      setTick(t => t + 1)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    setData(null)
    poll(selected)
    timerRef.current = setInterval(() => poll(selected), 2000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [poll, selected])

  return (
    <div className="space-y-4">
      {/* Product tab bar */}
      <div className="flex items-center justify-between">
        <div className="flex gap-1 bg-slate-100 rounded-xl p-1">
          {PRODUCTS.map(p => (
            <button
              key={p.id}
              onClick={() => setSelected(p.id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                selected === p.id
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              <span>{p.icon}</span>
              <span>{p.label}</span>
            </button>
          ))}
        </div>

        {/* Live indicator */}
        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <span
            key={tick}
            className="w-1.5 h-1.5 rounded-full bg-green-400 animate-ping"
            style={{ animationDuration: "0.6s", animationIterationCount: 1 }}
          />
          <span>Live</span>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20 text-slate-400 text-sm gap-2">
          <span className="animate-spin text-lg">⟳</span>
        </div>
      )}

      {!loading && error && !data && <ServiceDownBanner />}

      {!loading && data && (
        <>
          {data.resilience.stale && (
            <StaleNotice ageSeconds={data.resilience.cache_age_seconds} />
          )}
          {data.resilience.fallback ? (
            <FallbackBanner />
          ) : (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <ProductCard product={data.product} />
                <div className="space-y-4">
                  <InventoryPanel inventory={data.inventory} resilience={data.resilience} />
                  <ReviewsPanel reviews={data.product.reviews} />
                </div>
              </div>
              <TopologyHealth resilience={data.resilience} reviews={data.product.reviews} />
            </>
          )}
        </>
      )}
    </div>
  )
}

// ── Product card ───────────────────────────────────────────────────────────

function ProductCard({ product: p }: { product: ProductData }) {
  const icon = p.product_id === "p-001" ? "⌨️" : p.product_id === "p-002" ? "🪑" : "🖥️"

  return (
    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
      <div className="h-40 bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center">
        <span className="text-6xl select-none">{icon}</span>
      </div>

      <div className="p-5 space-y-4">
        <div>
          <p className="text-xs text-slate-400 font-mono">{p.product_id}</p>
          <h2 className="text-lg font-bold text-slate-900 leading-snug mt-0.5">{p.name}</h2>
          <div className="flex items-center gap-3 mt-1.5">
            {p.price != null && (
              <span className="text-xl font-bold text-slate-900">¥{p.price.toLocaleString()}</span>
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

        <hr className="border-slate-100" />

        <div>
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
            カスタマーレビュー
          </p>
          <p className="text-sm text-slate-600 leading-relaxed">{p.review_summary}</p>
        </div>

        {p.recommendation_reason && (
          <div className="bg-blue-50 rounded-xl px-3 py-2.5">
            <p className="text-xs font-semibold text-blue-700 mb-0.5">おすすめポイント</p>
            <p className="text-sm text-blue-800">{p.recommendation_reason}</p>
          </div>
        )}

        <div className="flex gap-2 pt-0.5">
          <button className="flex-1 bg-slate-900 text-white text-sm font-semibold py-2.5 rounded-xl hover:bg-slate-700 transition-colors">
            カートに追加
          </button>
          <button className="flex-1 border border-slate-200 text-slate-700 text-sm font-semibold py-2.5 rounded-xl hover:bg-slate-50 transition-colors">
            今すぐ購入
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Inventory panel (service-d) ────────────────────────────────────────────

function InventoryPanel({
  inventory: inv,
  resilience: r,
}: {
  inventory: InventoryData | null
  resilience: ResilienceMeta
}) {
  const unavailable = !r.inventory_available

  return (
    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
          在庫・価格
          <span className="ml-1.5 font-mono font-normal text-slate-400 normal-case">service-d</span>
        </p>
        <SourceBadge source={r.inventory_source} />
      </div>

      {unavailable ? (
        <div className="px-4 py-4 flex items-center gap-2 text-slate-400 text-sm">
          <span>⚠</span>
          <span>在庫情報を取得できません</span>
        </div>
      ) : inv ? (
        <div className="px-4 py-3 space-y-2.5">
          {/* Availability */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">在庫状況</span>
            {inv.available ? (
              inv.stock <= 5 ? (
                <span className="text-xs font-semibold text-red-600 bg-red-50 px-2 py-0.5 rounded-full">
                  残り{inv.stock}個
                </span>
              ) : (
                <span className="text-xs font-semibold text-green-700 bg-green-50 px-2 py-0.5 rounded-full">
                  在庫あり（{inv.stock}個）
                </span>
              )
            ) : (
              <span className="text-xs font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                在庫なし
              </span>
            )}
          </div>

          {/* Price */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">価格</span>
            <div className="text-right">
              {inv.sale_price != null ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400 line-through">
                    ¥{inv.price.toLocaleString()}
                  </span>
                  <span className="text-sm font-bold text-red-600">
                    ¥{inv.sale_price.toLocaleString()}
                  </span>
                  <span className="text-xs font-semibold text-red-600 bg-red-50 px-1.5 py-0.5 rounded">
                    SALE
                  </span>
                </div>
              ) : (
                <span className="text-sm font-semibold text-slate-800">
                  ¥{inv.price.toLocaleString()}
                </span>
              )}
            </div>
          </div>

          {/* Warehouse */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">倉庫</span>
            <span className="text-xs font-mono text-slate-600">{inv.warehouse}</span>
          </div>

          {/* Restocked at */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">最終入荷</span>
            <span className="text-xs text-slate-400">{inv.restocked_at.slice(0, 10)}</span>
          </div>
        </div>
      ) : null}
    </div>
  )
}

// ── Reviews panel (service-c via service-b) ────────────────────────────────

function ReviewsPanel({ reviews: rev }: { reviews: ReviewData | null }) {
  if (!rev) {
    return (
      <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
        <div className="px-4 py-2.5 border-b border-slate-100 bg-slate-50">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            レビュー詳細
            <span className="ml-1.5 font-mono font-normal text-slate-400 normal-case">service-c</span>
          </p>
        </div>
        <div className="px-4 py-4 text-sm text-slate-400 flex items-center gap-2">
          <span>⚠</span>
          <span>レビュー情報を取得できません</span>
        </div>
      </div>
    )
  }

  const dist = rev.rating_distribution
  const total = Object.values(dist).reduce((a, b) => a + b, 0)

  return (
    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
          レビュー詳細
          <span className="ml-1.5 font-mono font-normal text-slate-400 normal-case">service-c</span>
        </p>
        <SourceBadge source={rev._source ?? null} />
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* Rating distribution */}
        <div className="space-y-1">
          {["5", "4", "3", "2", "1"].map(star => {
            const count = dist[star] ?? 0
            const pct   = total > 0 ? (count / total) * 100 : 0
            return (
              <div key={star} className="flex items-center gap-2">
                <span className="text-xs text-amber-400 w-3 shrink-0">{"★".repeat(Number(star))}</span>
                <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-amber-400 rounded-full"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-xs text-slate-400 w-8 text-right">{count}</span>
              </div>
            )
          })}
        </div>

        {/* Top keywords */}
        <div>
          <p className="text-xs text-slate-400 mb-1.5">よく言及されるキーワード</p>
          <div className="flex flex-wrap gap-1">
            {rev.top_keywords.map(kw => (
              <span key={kw} className="text-xs bg-slate-50 border border-slate-200 text-slate-600 px-2 py-0.5 rounded-full">
                {kw}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Topology health bar ────────────────────────────────────────────────────

function TopologyHealth({
  resilience: r,
  reviews,
}: {
  resilience: ResilienceMeta
  reviews: ReviewData | null
}) {
  const reviewSource = reviews?._source ?? null

  return (
    <div className="bg-white border border-slate-200 rounded-xl px-4 py-3">
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Service Topology Health
      </p>
      <div className="flex items-start gap-2 flex-wrap">

        {/* service-a */}
        <ServiceNode
          name="service-a"
          label="Aggregator"
          latencyMs={r.service_a_latency_ms}
          source="fresh"
          circuit={null}
        />

        <Arrow />

        {/* Brace open */}
        <div className="flex flex-col gap-2">
          {/* service-b branch */}
          <div className="flex items-center gap-2">
            <ServiceNode
              name="service-b"
              label="Product DB"
              latencyMs={r.service_b_latency_ms}
              source={r.product_source}
              circuit={r.circuit_state}
            />
            <Arrow />
            <ServiceNode
              name="service-c"
              label="Reviews"
              latencyMs={null}
              source={reviewSource}
              circuit={null}
            />
          </div>

          {/* service-d branch */}
          <div className="flex items-center gap-2">
            <ServiceNode
              name="service-d"
              label="Inventory"
              latencyMs={r.service_d_latency_ms}
              source={r.inventory_source}
              circuit={r.inventory_circuit_state}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function Arrow() {
  return <span className="text-slate-300 text-xs mt-2.5">→</span>
}

function ServiceNode({
  name,
  label,
  latencyMs,
  source,
  circuit,
}: {
  name: string
  label: string
  latencyMs: number | null
  source: string | null
  circuit: string | null
}) {
  const isHealthy = source === "fresh"
  const isDegraded = source === "stale_cache"
  const isDown = source === "fallback" || source === "circuit_open"

  const borderColor = isDown
    ? "border-red-200 bg-red-50"
    : isDegraded
    ? "border-amber-200 bg-amber-50"
    : "border-green-200 bg-green-50"

  const dotColor = isDown
    ? "bg-red-500"
    : isDegraded
    ? "bg-amber-400"
    : isHealthy
    ? "bg-green-500"
    : "bg-slate-300"

  const latColor =
    latencyMs == null ? "text-slate-400"
    : latencyMs < 100  ? "text-green-600"
    : latencyMs < 300  ? "text-amber-600"
    : "text-red-600"

  return (
    <div className={`rounded-lg border px-2.5 py-1.5 min-w-[80px] ${borderColor}`}>
      <div className="flex items-center gap-1.5 mb-0.5">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColor}`} />
        <span className="text-xs font-semibold text-slate-700">{name}</span>
      </div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="flex items-center justify-between mt-1 gap-2">
        {latencyMs != null && (
          <span className={`text-xs font-mono ${latColor}`}>{latencyMs}ms</span>
        )}
        {circuit && (
          <span className={`text-xs ${
            circuit === "closed" ? "text-green-600"
            : circuit === "open" ? "text-red-600"
            : "text-amber-600"
          }`}>
            CB:{circuit === "closed" ? "✓" : circuit === "open" ? "✕" : "~"}
          </span>
        )}
      </div>
    </div>
  )
}

// ── Source badge ───────────────────────────────────────────────────────────

function SourceBadge({ source }: { source: string | null }) {
  if (!source || source === "fresh") {
    return (
      <span className="text-xs font-medium text-green-700 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded-full">
        fresh
      </span>
    )
  }
  if (source === "stale_cache") {
    return (
      <span className="text-xs font-medium text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded-full">
        stale
      </span>
    )
  }
  if (source === "fallback" || source === "circuit_open") {
    return (
      <span className="text-xs font-medium text-red-700 bg-red-50 border border-red-200 px-1.5 py-0.5 rounded-full">
        {source === "circuit_open" ? "CB open" : "fallback"}
      </span>
    )
  }
  return null
}

// ── Banner components ──────────────────────────────────────────────────────

function StaleNotice({ ageSeconds }: { ageSeconds: number | null }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-center gap-3">
      <span className="text-amber-500 shrink-0">⚠</span>
      <div>
        <p className="text-sm font-medium text-amber-800">キャッシュされた商品情報を表示しています</p>
        {ageSeconds != null && (
          <p className="text-xs text-amber-600 mt-0.5">最終更新: {ageSeconds}秒前</p>
        )}
      </div>
    </div>
  )
}

function FallbackBanner() {
  return (
    <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 flex items-center gap-3">
      <span className="text-red-500 shrink-0">✕</span>
      <div>
        <p className="text-sm font-medium text-red-800">商品サービスが応答していません</p>
        <p className="text-xs text-red-600 mt-0.5">フォールバックデータを表示しています</p>
      </div>
    </div>
  )
}

function ServiceDownBanner() {
  return (
    <div className="bg-white border border-slate-200 rounded-2xl p-10 text-center space-y-3">
      <div className="text-4xl">⚡</div>
      <h2 className="text-base font-bold text-slate-700">service-a に接続できません</h2>
      <p className="text-sm text-slate-400">サービスが起動しているか確認してください</p>
      <p className="text-xs text-slate-300 font-mono">{SERVICE_A_BASE}</p>
    </div>
  )
}
