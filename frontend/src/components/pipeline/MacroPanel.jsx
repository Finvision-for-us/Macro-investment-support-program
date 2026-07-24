const cx = (...c) => c.filter(Boolean).join(' ')

function formatValue(v, unit) {
  if (unit === '%' || unit === '%p') return `${v.toFixed(2)}%`
  if (unit?.startsWith('$')) return `$${v.toFixed(2)}`
  if (unit === '엔/달러') return `${v.toFixed(2)}엔`
  return `${v.toFixed(2)}`
}

function formatDelta(change, prev, unit) {
  if (unit === '%' || unit === '%p') return `${change >= 0 ? '+' : ''}${change.toFixed(2)}%p`
  const pct = prev !== 0 ? (change / prev) * 100 : 0
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

// 관측일과 스냅샷 기준일(asOf)의 차이를 "최근 갱신일" 라벨로 — 브라우저 시계가 아니라
// 데이터 기준일로 계산해야 타임라인이 어긋나지 않는다.
function updatedLabel(observedIso, asOf) {
  const mmdd = (observedIso || '').slice(5, 10)  // MM-DD
  if (!observedIso || !asOf) return mmdd
  const o = new Date(observedIso.slice(0, 10))
  const a = new Date(asOf)
  const days = Math.round((a - o) / 86_400_000)
  const rel = days <= 0 ? '오늘' : days === 1 ? '어제' : `${days}일 전`
  return `갱신 ${mmdd} · ${rel}`
}

export function MacroPanel({ events, asOf }) {
  if (!events?.length) return null

  // fetch_latest_events 는 시리즈당 1건이지만, 방어적으로 시리즈별 최신 1건만 유지
  const seen = new Set()
  const dedup = []
  for (const e of [...events].sort((a, b) => (b.observed_at || '').localeCompare(a.observed_at || ''))) {
    if (seen.has(e.series_id)) continue
    seen.add(e.series_id)
    dedup.push(e)
  }

  return (
    <section className="mb-8">
      <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-zinc-500">오늘의 거시 · 최신 관측치</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {dedup.slice(0, 8).map((m) => {
          const isUp = m.change >= 0
          return (
            <article key={m.id} className="glass-panel glass-card-hover rounded-xl p-4 flex flex-col justify-between" title={m.summary_ko}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-bold text-zinc-800">{m.series_label_ko}</span>
                <span className="text-[10px] font-medium tabular-nums text-zinc-400 shrink-0">{updatedLabel(m.observed_at, asOf)}</span>
              </div>
              <div className="mt-3 flex items-center justify-between">
                <div className="flex items-baseline gap-2">
                  <span className="text-xl font-extrabold tabular-nums tracking-tight text-zinc-900">{formatValue(m.value, m.unit)}</span>
                  <span className={cx('text-xs font-bold tabular-nums px-2 py-0.5 rounded-md border', isUp ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600' : 'bg-rose-500/10 border-rose-500/20 text-rose-600')}>
                    {isUp ? '▲' : '▼'} {formatDelta(m.change, m.prev_value, m.unit)}
                  </span>
                </div>
                <span className="text-[10px] font-bold tabular-nums px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-500">
                  {m.sigma_z > 0 ? '+' : ''}{m.sigma_z.toFixed(1)}σ
                </span>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
