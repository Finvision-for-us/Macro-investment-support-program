const cx = (...c) => c.filter(Boolean).join(' ')

const DIRECTION_META = {
  positive:  { dot: 'bg-emerald-500', label: '호재 우세' },
  negative:  { dot: 'bg-rose-500',    label: '악재 우세' },
  uncertain: { dot: 'bg-zinc-400',    label: '혼재' },
}

function DirectionBadge({ direction }) {
  const dir = DIRECTION_META[direction]
  return (
    <span className={cx(
      'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold border',
      direction === 'positive' && 'bg-emerald-500/10 border-emerald-500/20 text-emerald-700',
      direction === 'negative' && 'bg-rose-500/10 border-rose-500/20 text-rose-700',
      direction === 'uncertain' && 'bg-zinc-100 border-zinc-200 text-zinc-600',
    )}>
      <span className={cx('h-1.5 w-1.5 rounded-full', dir.dot)} aria-hidden />
      {dir.label}
    </span>
  )
}

function ThemeCard({ theme: t, storyById, onSelect, hero = false }) {
  const preview = t.story_ids.map(sid => storyById.get(sid)?.title).filter(Boolean)
  const first = preview[0]
  return (
    <button type="button" onClick={() => onSelect(t.id)}
      className={cx('group glass-panel glass-card-hover rounded-2xl text-left glow-accent overflow-hidden relative w-full', hero ? 'p-6' : 'p-5')}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <DirectionBadge direction={t.direction} />
        <span className="text-[10px] font-bold tabular-nums px-2 py-0.5 rounded-md bg-zinc-100 text-zinc-500">
          스토리 {t.story_ids.length} · 영향력 {(t.aggregate_score * 100).toFixed(0)}
        </span>
      </div>
      <h3 className={cx('font-extrabold leading-snug text-zinc-900 group-hover:text-indigo-600 transition-colors duration-200 mt-2', hero ? 'text-xl' : 'text-base')}>
        {t.name}
      </h3>
      {t.description && (
        <p className={cx('mt-2 text-sm leading-relaxed text-zinc-600 font-medium', hero ? 'line-clamp-3' : 'line-clamp-2')}>{t.description}</p>
      )}
      {first && (
        <div className="mt-3 py-1.5 px-2.5 rounded-lg bg-zinc-50/50 border border-zinc-100/50">
          <p className="truncate text-xs font-semibold text-zinc-400">
            <span className="text-indigo-500 mr-1">대표</span> {first}
            {preview.length > 1 && <span className="ml-1 text-zinc-400 font-bold">외 {preview.length - 1}건</span>}
          </p>
        </div>
      )}
      <div className="mt-4 inline-flex items-center text-xs font-bold text-zinc-400 transition group-hover:text-indigo-500">
        스토리 보기 <span className="ml-1 transform group-hover:translate-x-1 transition-transform">→</span>
      </div>
    </button>
  )
}

function SignalCard({ theme: t, onSelect }) {
  const dir = DIRECTION_META[t.direction]
  const ticker = t.affected_tickers[0]
  return (
    <button type="button" onClick={() => onSelect(t.id)}
      className="group glass-panel glass-card-hover w-full rounded-xl p-3.5 text-left">
      <div className="mb-2 flex items-center gap-2">
        <span className={cx('h-1.5 w-1.5 rounded-full', dir.dot)} aria-hidden />
        {ticker
          ? <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-zinc-500">{ticker}</span>
          : <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">단독</span>
        }
      </div>
      <p className="line-clamp-2 text-[13px] font-semibold leading-snug text-zinc-800 transition-colors group-hover:text-indigo-600">
        {t.name}
      </p>
    </button>
  )
}

function SectionLabel({ children }) {
  return <h3 className="mb-3 text-[10px] font-bold uppercase tracking-wider text-zinc-400">{children}</h3>
}

export function ThemePicker({ themes, storyById, onSelectTheme }) {
  if (!themes?.length) {
    return (
      <div className="rounded-2xl border border-dashed border-zinc-300 bg-white/40 py-16 text-center">
        <div className="text-3xl">🧭</div>
        <p className="mt-3 text-sm text-zinc-500">오늘 추출된 테마가 없습니다.</p>
      </div>
    )
  }

  const headline = themes.find(t => t.tier === 'headline')
  const major    = themes.filter(t => (t.tier ?? 'major') === 'major')
  const minor    = themes.filter(t => t.tier === 'minor')

  return (
    <section>
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">무엇부터 볼까요?</h2>
      {headline && (
        <div className="mb-6">
          <SectionLabel>오늘의 헤드라인</SectionLabel>
          <ThemeCard theme={headline} storyById={storyById} onSelect={onSelectTheme} hero />
        </div>
      )}
      {major.length > 0 && (
        <div className="mb-6">
          {headline && <SectionLabel>주요 테마</SectionLabel>}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {major.map(t => <ThemeCard key={t.id} theme={t} storyById={storyById} onSelect={onSelectTheme} />)}
          </div>
        </div>
      )}
      {minor.length > 0 && (
        <div className="mt-6">
          <SectionLabel>단독 시그널</SectionLabel>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {minor.map(t => <SignalCard key={t.id} theme={t} onSelect={onSelectTheme} />)}
          </div>
        </div>
      )}
    </section>
  )
}
