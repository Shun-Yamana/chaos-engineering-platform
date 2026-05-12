const config: Record<string, { bg: string; text: string; dot: string; pulse: boolean }> = {
  running:   { bg: "bg-amber-50",  text: "text-amber-700",  dot: "bg-amber-500",  pulse: true },
  pending:   { bg: "bg-slate-100", text: "text-slate-500",  dot: "bg-slate-400",  pulse: true },
  completed: { bg: "bg-green-50",  text: "text-green-700",  dot: "bg-green-500",  pulse: false },
  stopped:   { bg: "bg-sky-50",    text: "text-sky-700",    dot: "bg-sky-400",    pulse: false },
  failed:    { bg: "bg-red-50",    text: "text-red-700",    dot: "bg-red-500",    pulse: false },
}

interface Props {
  status: string
  size?: "sm" | "md"
}

export function StatusBadge({ status, size = "sm" }: Props) {
  const c = config[status] ?? config.pending
  const cls = size === "md"
    ? "text-sm px-2.5 py-1 gap-1.5"
    : "text-xs px-2 py-0.5 gap-1"
  return (
    <span className={`inline-flex items-center rounded-full font-medium ${c.bg} ${c.text} ${cls}`}>
      <span className={`rounded-full shrink-0 ${c.dot} ${c.pulse ? "animate-pulse" : ""} ${size === "md" ? "w-2 h-2" : "w-1.5 h-1.5"}`} />
      {status}
    </span>
  )
}
