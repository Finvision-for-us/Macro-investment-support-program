const cx = (...c) => c.filter(Boolean).join(' ')

const DIRECTION_META = {
  positive:  { dot: 'bg-emerald-500', label: '호재 우세' },
  negative:  { dot: 'bg-rose-500',    label: '악재 우세' },
  uncertain: { dot: 'bg-zinc-400',    label: '혼재' },
}

export function ThemeHeader({ theme, storiesShown, onBackToPicker }) {
  const dir = DIRECTION_META[theme.direction]
  return (
    <section className="mb-8">
      <button type="button" onClick={onBackToPicker}
        className="mb-5 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-zinc-200 bg-white/80 hover:bg-zinc-50 hover:text-indigo-600 text-xs font-bold text-zinc-500 transition-all duration-200 shadow-sm">
        ← 모든 테마
      </button>
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        <span className={cx(
          'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold border',
          theme.direction === 'positive' && 'bg-emerald-500/10 border-emerald-500/20 text-emerald-700',
          theme.direction === 'negative' && 'bg-rose-500/10 border-rose-500/20 text-rose-700',
          theme.direction === 'uncertain' && 'bg-zinc-100 border-zinc-200 text-zinc-600',
        )}>
          <span className={cx('h-1.5 w-1.5 rounded-full', dir.dot)} aria-hidden />
          {dir.label}
        </span>
        <span className="text-zinc-300">|</span>
        <span className="tabular-nums font-bold">스토리 {theme.story_ids.length}</span>
        <span className="text-zinc-300">|</span>
        <span className="tabular-nums font-bold">영향력 {(theme.aggregate_score * 100).toFixed(0)}</span>
      </div>
      <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-zinc-900">{theme.name}</h1>
      {theme.description && (
        <p className="mt-2.5 text-sm leading-relaxed text-zinc-600 font-medium">{theme.description}</p>
      )}
      {storiesShown !== theme.story_ids.length && (
        <p className="mt-3 text-xs text-zinc-400 font-bold">({storiesShown}/{theme.story_ids.length} 표시 — 필터 적용 중)</p>
      )}
    </section>
  )
}
