import { RippleSection } from './RippleSection'
import { ScoreBar } from './ScoreBar'
import { StateBadge } from './StateBadge'

const cx = (...c) => c.filter(Boolean).join(' ')

const PUBLISHER_LABELS = {
  sec_edgar: 'SEC', polygon_news: 'Polygon',
  rss_cnbc_top: 'CNBC', rss_cnbc_finance: 'CNBC', rss_cnbc_economy: 'CNBC',
  rss_marketwatch_topstories: 'MktWatch', rss_marketwatch_bulletins: 'MktWatch',
  rss_gnews_markets: 'GNews', rss_gnews_business: 'GNews', rss_gnews_macro: 'GNews',
  rss_yahoo_finance: 'Yahoo', rss_fed_press: 'Fed',
}
const formatPublisher = id => PUBLISHER_LABELS[id] ?? id.replace(/^rss_/, '').replace(/_/g, ' ')

const LEFT_BAR = {
  active:   'before:bg-emerald-500',
  evolving: 'before:bg-amber-500',
  resolved: 'before:bg-zinc-400',
}

export function StoryCard({ story: s, rank, selectedTickers, onTickerToggle, expanded, onToggleExpand }) {
  const canExpand = s.narrative_long?.trim().length > 0 || s.ripple_effects?.length > 0 || s.sources?.length > 0
  const headTickers = s.tickers.slice(0, 6)
  const restCount   = s.tickers.length - headTickers.length

  return (
    <article className={cx(
      'glass-panel glass-card-hover relative pl-8 pr-6 py-6 rounded-2xl overflow-hidden glow-accent',
      'before:absolute before:left-0 before:top-0 before:bottom-0 before:w-1.5',
      LEFT_BAR[s.state],
    )}>
      {/* 상단: 랭크 + kind + 상태 */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="px-2.5 py-0.5 text-[10px] font-bold font-mono tracking-wider rounded-md bg-zinc-100 text-zinc-500">
            # {String(rank).padStart(2, '0')}
          </span>
          {s.event_ids?.length > 1
            ? <span className="px-2 py-0.5 text-[10px] font-bold tracking-wider rounded-md bg-indigo-100 text-indigo-600">STORY</span>
            : <span className="px-2 py-0.5 text-[10px] font-bold tracking-wider rounded-md bg-amber-100 text-amber-600">SIGNAL</span>
          }
        </div>
        <StateBadge state={s.state} />
      </div>

      <h2 className="text-lg font-extrabold leading-snug text-zinc-900 hover:text-indigo-600 transition-colors duration-200">{s.title}</h2>

      {s.narrative_short && (
        <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-zinc-600 font-medium">{s.narrative_short}</p>
      )}

      {headTickers.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          {headTickers.map(t => {
            const on = selectedTickers.has(t)
            return (
              <button key={t} type="button" onClick={() => onTickerToggle(t)} aria-pressed={on}
                className={cx('rounded px-2.5 py-0.5 font-mono text-xs font-semibold transition-all duration-200 border',
                  on ? 'bg-indigo-600 border-indigo-600 text-white shadow-sm shadow-indigo-600/30'
                     : 'bg-zinc-100/80 border-zinc-200/50 text-zinc-600 hover:bg-indigo-50/50 hover:text-indigo-600 hover:border-indigo-200',
                )}>
                {t}
              </button>
            )
          })}
          {restCount > 0 && <span className="text-xs font-semibold text-zinc-400 ml-1">+{restCount}</span>}
        </div>
      )}

      {/* 펼침 영역 */}
      {expanded && (
        <div className="mt-6 border-t border-zinc-100/80 pt-5">
          <div className="mb-5 grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3">
            <ScoreBar score={s.score} />
            <div>
              <span className="text-zinc-400 font-semibold">처음 본 날</span>
              <div className="mt-0.5 text-sm font-bold tabular-nums text-zinc-800">{s.first_seen_date}</div>
            </div>
            {s.state === 'evolving' && s.similarity != null && (
              <div>
                <span className="text-zinc-400 font-semibold">어제 유사도</span>
                <div className="mt-0.5 text-sm font-bold tabular-nums text-zinc-800">{Math.round(s.similarity * 100)}%</div>
              </div>
            )}
          </div>

          {s.narrative_long && (
            <div className="mb-6">
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-indigo-500">상세 분석</h3>
              <p className="whitespace-pre-line text-sm leading-relaxed text-zinc-700">{s.narrative_long}</p>
            </div>
          )}

          {s.ripple_effects?.length > 0 && (
            <div className="border-t border-zinc-100/50 pt-4">
              <RippleSection ripples={s.ripple_effects} />
            </div>
          )}

          {s.sources?.length > 0 && (
            <div className="mt-5 border-t border-zinc-100/50 pt-4">
              <h3 className="mb-2.5 text-xs font-bold uppercase tracking-wider text-zinc-400">출처 뉴스</h3>
              <ul className="space-y-1.5">
                {s.sources.map((src, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs">
                    {src.publisher && (
                      <span className="mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold bg-zinc-100 text-zinc-500">
                        {formatPublisher(src.publisher)}
                      </span>
                    )}
                    <a href={src.url} target="_blank" rel="noopener noreferrer"
                      className="leading-snug text-zinc-600 hover:text-indigo-600 hover:underline transition-colors duration-150 line-clamp-2">
                      {src.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {canExpand && (
        <button type="button" onClick={onToggleExpand}
          className={cx('mt-5 w-full py-2.5 text-xs font-bold rounded-xl border transition-all duration-300',
            expanded
              ? 'bg-zinc-100 border-zinc-200 text-zinc-700 hover:bg-zinc-200'
              : 'bg-indigo-500/5 border-indigo-500/20 text-indigo-600 hover:bg-indigo-600 hover:border-indigo-600 hover:text-white shadow-sm',
          )}>
          {expanded ? '접기' : '상세 분석 · 파급효과 보기'}
        </button>
      )}
    </article>
  )
}
