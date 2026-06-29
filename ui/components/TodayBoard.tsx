"use client";

import { useMemo, useState } from "react";

import type { LifecycleStory, StoriesLatest } from "@/lib/stories";
import { countByState } from "@/lib/stories";
import { MacroPanel } from "./MacroPanel";
import { StoryCard } from "./StoryCard";
import { ThemeHeader } from "./ThemeHeader";
import { ThemePicker } from "./ThemePicker";

interface Props {
  data: StoriesLatest;
  topStories: LifecycleStory[];
}

export function TodayBoard({ data, topStories }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedThemeId, setSelectedThemeId] = useState<string | null>(null);

  const toggleTicker = (t: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // story_id → story 매핑 (picker 미리보기용)
  const storyById = useMemo(() => {
    const m = new Map<string, LifecycleStory>();
    for (const s of topStories) m.set(s.story_id, s);
    return m;
  }, [topStories]);

  const selectedTheme = useMemo(
    () => data.themes.find((t) => t.id === selectedThemeId) ?? null,
    [data.themes, selectedThemeId]
  );

  // 선택된 테마의 스토리들 (티커 필터 추가 적용)
  const themeStories = useMemo(() => {
    if (!selectedTheme) return [];
    const ids = new Set(selectedTheme.story_ids);
    let xs = topStories.filter((s) => ids.has(s.story_id));
    if (selected.size > 0)
      xs = xs.filter((s) => s.tickers.some((t) => selected.has(t)));
    return xs;
  }, [selectedTheme, topStories, selected]);

  const counts = countByState(topStories);
  const backToPicker = () => {
    setSelectedThemeId(null);
    setSelected(new Set());
    setExpanded(new Set());
  };

  // ───────── 모드 1: 테마 선택 화면 ─────────
  if (!selectedTheme) {
    return (
      <main className="pt-6">
        <header className="mb-10">
          <div className="flex items-center">
            <span className="px-3 py-1 text-xs font-bold uppercase tracking-wider rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 shadow-sm backdrop-blur-sm">
              {data.date}
            </span>
          </div>
          <h1 className="mt-4 text-4xl font-extrabold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-zinc-900 via-zinc-800 to-zinc-600 dark:from-zinc-50 dark:via-zinc-200 dark:to-zinc-400">
            오늘의 스토리
          </h1>
          <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-zinc-500 dark:text-zinc-400">
            <span className="font-bold px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300">
              {topStories.length}건
            </span>
            <span className="font-bold px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300">
              테마 {data.themes.length}개
            </span>
            <span className="h-3 w-px bg-zinc-300 dark:bg-zinc-700" />
            <span 
              title="오늘 새로 등장"
              className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-bold"
            >
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/50" />
              신규 {counts.active}
            </span>
            <span 
              title="어제부터 이어지는 중"
              className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 font-bold"
            >
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500 shadow-sm shadow-amber-500/50" />
              진행중 {counts.evolving}
            </span>
          </div>
        </header>

        <MacroPanel events={data.macro_events} />

        <ThemePicker
          themes={data.themes}
          storyById={storyById}
          onSelectTheme={setSelectedThemeId}
        />

        <footer className="mt-16 text-center text-[10px] text-zinc-400 dark:text-zinc-500 font-bold">
          생성 시각: {data.generated_at}
        </footer>
      </main>
    );
  }

  // ───────── 모드 2: 테마 상세 (선택된 테마의 스토리들) ─────────
  return (
    <main className="pt-8">
      <ThemeHeader
        theme={selectedTheme}
        storiesShown={themeStories.length}
        onBackToPicker={backToPicker}
      />

      {/* 티커 필터 (선택 시만 표시) */}
      {selected.size > 0 && (
        <div className="sticky top-0 z-10 -mx-5 mb-6 flex flex-wrap items-center gap-2 border-b border-zinc-200 bg-zinc-50/90 px-5 py-3 text-xs backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90 sm:-mx-6 sm:px-6">
          <span className="font-medium text-zinc-500">티커</span>
          {[...selected].sort().map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => toggleTicker(t)}
              className="rounded-md bg-zinc-900 px-2 py-0.5 font-mono text-white transition hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              title="클릭해서 제거"
            >
              {t} ×
            </button>
          ))}
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="ml-auto text-zinc-500 underline-offset-2 hover:underline"
          >
            모두 해제
          </button>
        </div>
      )}

      {themeStories.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-zinc-300 bg-white/40 py-16 text-center dark:border-zinc-700 dark:bg-zinc-900/40">
          <div className="text-3xl">🔍</div>
          <p className="mt-3 text-sm text-zinc-500">
            선택한 티커 조건에 맞는 스토리가 없습니다.
          </p>
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="mt-3 text-xs font-medium text-zinc-600 underline-offset-2 hover:underline dark:text-zinc-400"
          >
            티커 필터 해제
          </button>
        </div>
      ) : (
        <section className="space-y-4">
          {themeStories.map((s, i) => (
            <StoryCard
              key={s.story_id}
              story={s}
              rank={i + 1}
              selectedTickers={selected}
              onTickerToggle={toggleTicker}
              expanded={expanded.has(s.story_id)}
              onToggleExpand={() => toggleExpand(s.story_id)}
            />
          ))}
        </section>
      )}

      <footer className="mt-12 text-center text-[10px] text-zinc-400">
        생성: {data.generated_at}
      </footer>
    </main>
  );
}
