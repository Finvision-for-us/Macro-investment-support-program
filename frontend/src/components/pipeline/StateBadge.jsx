const cx = (...c) => c.filter(Boolean).join(' ')

const META = {
  active:   { label: '신규',   dot: 'bg-emerald-500 shadow-sm shadow-emerald-500/50', container: 'bg-emerald-500/10 text-emerald-700 border border-emerald-500/20', help: '오늘 새로 등장한 스토리.' },
  evolving: { label: '진행중', dot: 'bg-amber-500 shadow-sm shadow-amber-500/50',     container: 'bg-amber-500/10 text-amber-700 border border-amber-500/20',     help: '어제 본 스토리에 오늘 새 신호 합류.' },
  resolved: { label: '종결',   dot: 'bg-zinc-400',                                    container: 'bg-zinc-100 text-zinc-500 border border-zinc-200',               help: '마지막 신호 후 3일 이상 무신호.' },
}

export function StateBadge({ state }) {
  const m = META[state] ?? META.resolved
  return (
    <span title={m.help} className={cx('inline-flex cursor-help items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold tracking-wide', m.container)}>
      <span className={cx('h-1.5 w-1.5 rounded-full', m.dot)} aria-hidden />
      {m.label}
    </span>
  )
}
