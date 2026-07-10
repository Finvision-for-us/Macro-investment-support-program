const cx = (...c) => c.filter(Boolean).join(' ')

const TIER_META = {
  direct:   { label: '1차 · 직접', dot: 'bg-indigo-500', order: 0 },
  adjacent: { label: '2차 · 인접', dot: 'bg-sky-500',    order: 1 },
  macro:    { label: '3차 · 거시', dot: 'bg-amber-500',  order: 2 },
}

const DIR_META = {
  positive:  { glyph: '▲', cls: 'text-emerald-600' },
  negative:  { glyph: '▼', cls: 'text-rose-600' },
  uncertain: { glyph: '◆', cls: 'text-zinc-400' },
}

const HORIZON_LABEL = { '1w': '1주', '1m': '1개월', '1q': '1분기' }

export function RippleSection({ ripples }) {
  if (!ripples?.length) return null

  const byTier = new Map()
  for (const r of ripples) {
    const arr = byTier.get(r.tier) ?? []
    arr.push(r)
    byTier.set(r.tier, arr)
  }
  for (const arr of byTier.values()) arr.sort((a, b) => b.confidence - a.confidence)

  const tiers = Array.from(byTier.entries()).sort((a, b) => TIER_META[a[0]].order - TIER_META[b[0]].order)

  return (
    <div className="mt-6">
      <h3 className="mb-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">예상 파급효과</h3>
      <div className="space-y-5">
        {tiers.map(([tier, items]) => {
          const meta = TIER_META[tier]
          return (
            <div key={tier}>
              <div className="mb-3 flex items-center gap-2">
                <span className={cx('h-2.5 w-2.5 rounded-full', meta.dot)} aria-hidden />
                <span className="text-xs font-bold text-zinc-700">{meta.label}</span>
                <span className="text-[10px] font-medium text-zinc-400">· {items.length}건</span>
              </div>
              <ul className="space-y-4 border-l border-zinc-200 pl-5 ml-1 mb-4">
                {items.map((r, idx) => {
                  const dir = DIR_META[r.direction]
                  return (
                    <li key={`${tier}-${idx}`} className="text-sm">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={cx(
                          'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-bold border',
                          r.direction === 'positive' && 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
                          r.direction === 'negative' && 'bg-rose-500/10 border-rose-500/20 text-rose-600',
                          r.direction === 'uncertain' && 'bg-zinc-100 border-zinc-200 text-zinc-600',
                        )}>
                          <span aria-hidden className="mr-0.5">{dir.glyph}</span>
                          {r.target}
                        </span>
                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-400">
                          {HORIZON_LABEL[r.horizon]}
                        </span>
                        <span className="ml-auto text-[10px] font-bold tabular-nums px-1.5 py-0.5 rounded bg-indigo-500/5 text-indigo-500">
                          신뢰도 {Math.round(r.confidence * 100)}%
                        </span>
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-zinc-600 font-medium">{r.mechanism}</p>
                    </li>
                  )
                })}
              </ul>
            </div>
          )
        })}
      </div>
    </div>
  )
}
