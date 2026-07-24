import { useMemo, useState } from 'react'
import { MacroPanel } from './MacroPanel'
import { StoryCard } from './StoryCard'
import { ThemeHeader } from './ThemeHeader'
import { ThemePicker } from './ThemePicker'

function countByState(stories) {
  const out = { active: 0, evolving: 0, resolved: 0 }
  for (const s of stories) out[s.state] = (out[s.state] ?? 0) + 1
  return out
}

export function TodayBoard({ data, topStories }) {
  const [selected, setSelected]           = useState(new Set())
  const [expanded, setExpanded]           = useState(new Set())
  const [selectedThemeId, setSelectedThemeId] = useState(null)

  const toggleTicker = t => setSelected(prev => {
    const next = new Set(prev)
    next.has(t) ? next.delete(t) : next.add(t)
    return next
  })
  const toggleExpand = id => setExpanded(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const storyById = useMemo(() => {
    const m = new Map()
    for (const s of topStories) m.set(s.story_id, s)
    return m
  }, [topStories])

  const selectedTheme = useMemo(() => data.themes.find(t => t.id === selectedThemeId) ?? null, [data.themes, selectedThemeId])

  const themeStories = useMemo(() => {
    if (!selectedTheme) return []
    const ids = new Set(selectedTheme.story_ids)
    let xs = topStories.filter(s => ids.has(s.story_id))
    if (selected.size > 0) xs = xs.filter(s => s.tickers.some(t => selected.has(t)))
    return xs
  }, [selectedTheme, topStories, selected])

  const counts = countByState(topStories)
  const backToPicker = () => { setSelectedThemeId(null); setSelected(new Set()); setExpanded(new Set()) }

  // 테마 선택 화면
  if (!selectedTheme) {
    return (
      <main className="pt-6">
        <header className="mb-10">
          <div className="flex items-center">
            <span className="px-3 py-1 text-xs font-bold uppercase tracking-wider rounded-full bg-indigo-500/10 text-indigo-600 border border-indigo-500/20 shadow-sm">
              {data.date}
            </span>
          </div>
          <h1 className="mt-4 text-4xl font-extrabold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-zinc-900 via-zinc-800 to-zinc-600">
            오늘의 스토리
          </h1>
          <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-zinc-500">
            <span className="font-bold px-2 py-0.5 rounded bg-zinc-100 text-zinc-600">{topStories.length}건</span>
            <span className="font-bold px-2 py-0.5 rounded bg-zinc-100 text-zinc-600">테마 {data.themes.filter(t => t.tier !== 'minor').length}개</span>
            {data.themes.some(t => t.tier === 'minor') && (
              <span className="font-bold px-2 py-0.5 rounded bg-zinc-100 text-zinc-600">단독 {data.themes.filter(t => t.tier === 'minor').length}</span>
            )}
            <span className="h-3 w-px bg-zinc-300" />
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20 font-bold">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />신규 {counts.active}
            </span>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-600 border border-amber-500/20 font-bold">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />진행중 {counts.evolving}
            </span>
          </div>
        </header>

        <MacroPanel events={data.macro_events} asOf={data.date} />
        <ThemePicker themes={data.themes} storyById={storyById} onSelectTheme={setSelectedThemeId} />

        <footer className="mt-16 text-center text-[10px] text-zinc-400 font-bold">생성 시각: {data.generated_at}</footer>
      </main>
    )
  }

  // 테마 상세 화면
  return (
    <main className="pt-8">
      <ThemeHeader theme={selectedTheme} storiesShown={themeStories.length} onBackToPicker={backToPicker} />

      {selected.size > 0 && (
        <div className="sticky top-0 z-10 -mx-5 mb-6 flex flex-wrap items-center gap-2 border-b border-zinc-200 bg-zinc-50/90 px-5 py-3 text-xs backdrop-blur">
          <span className="font-medium text-zinc-500">티커</span>
          {[...selected].sort().map(t => (
            <button key={t} type="button" onClick={() => toggleTicker(t)}
              className="rounded-md bg-zinc-900 px-2 py-0.5 font-mono text-white transition hover:bg-zinc-700">
              {t} ×
            </button>
          ))}
          <button type="button" onClick={() => setSelected(new Set())} className="ml-auto text-zinc-500 underline-offset-2 hover:underline">
            모두 해제
          </button>
        </div>
      )}

      {themeStories.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 bg-white/40 py-16 text-center">
          <div className="text-3xl">🔍</div>
          <p className="mt-3 text-sm text-zinc-500">선택한 티커 조건에 맞는 스토리가 없습니다.</p>
          <button type="button" onClick={() => setSelected(new Set())} className="mt-3 text-xs font-medium text-zinc-600 underline-offset-2 hover:underline">
            티커 필터 해제
          </button>
        </div>
      ) : (
        <section className="space-y-4">
          {themeStories.map((s, i) => (
            <StoryCard key={s.story_id} story={s} rank={i + 1}
              selectedTickers={selected} onTickerToggle={toggleTicker}
              expanded={expanded.has(s.story_id)} onToggleExpand={() => toggleExpand(s.story_id)} />
          ))}
        </section>
      )}

      <footer className="mt-12 text-center text-[10px] text-zinc-400">생성: {data.generated_at}</footer>
    </main>
  )
}
