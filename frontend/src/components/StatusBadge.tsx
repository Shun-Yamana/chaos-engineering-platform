const colors: Record<string, string> = {
  running:   "bg-yellow-100 text-yellow-800",
  completed: "bg-green-100 text-green-800",
  stopped:   "bg-blue-100 text-blue-800",
  failed:    "bg-red-100 text-red-800",
  pending:   "bg-gray-100 text-gray-600",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${colors[status] ?? colors.pending}`}>
      {status}
    </span>
  )
}
