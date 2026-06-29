"use client";

import clsx from "clsx";

import type { LifecycleStory } from "@/lib/stories";
import { RippleSection } from "./RippleSection";
import { ScoreBar } from "./ScoreBar";
import { StateBadge } from "./StateBadge";

interface Props {
  story: LifecycleStory;
  rank: number;
  selectedTickers: ReadonlySet<string>;
  onTickerToggle: (ticker: string) => void;
  expanded: boolean;
  onToggleExpand: () => void;
}

export function StoryCard({
  story: s,
  rank,
  selectedTickers,
  onTickerToggle,
  expanded,
  onToggleExpand,
}: Props) {
  const canExpand =
    s.narrative_long.trim().length > 0 || s.ripple_effects.length > 0;
  const headTickers = s.tickers.slice(0, 6);
  const restCount = s.tickers.length - headTickers.length;

  const leftBarColor = {
    active: "before:bg-emerald-500 dark:before:bg-emerald-400",
    evolving: "before:bg-amber-500 dark:before:bg-amber-400",
    resolved: "before:bg-zinc-400 dark:before:bg-zinc-600",
  }[s.state];

  return (
    <article
      className={clsx(
        "glass-panel glass-card-hover relative pl-8 pr-6 py-6 rounded-2xl overflow-hidden glow-accent",
        "before:absolute before:left-0 before:top-0 before:bottom-0 before:w-1.5",
        leftBarColor
      )}
    >
      {/* 상단: 랭크 + 상태 */}
      <div className="mb-3 flex items-center justify-between">
        <span className="px-2.5 py-0.5 text-[10px] font-bold font-mono tracking-wider rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
          # {String(rank).padStart(2, "0")}
        </span>
        <StateBadge state={s.state} />
      </div>

      {/* 제목 — 가장 큰 시각 자산 */}
      <h2 className="text-lg font-extrabold leading-snug text-zinc-900 dark:text-zinc-50 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors duration-200">
        {s.title}
      </h2>

      {/* narrative_short — 카드의 본문 */}
      {s.narrative_short && (
        <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-zinc-600 dark:text-zinc-300 font-medium">
          {s.narrative_short}
        </p>
      )}

      {/* 티커 칩 — 깔끔한 한 줄 */}
      {headTickers.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          {headTickers.map((t) => {
            const on = selectedTickers.has(t);
            return (
              <button
                key={t}
                type="button"
                onClick={() => onTickerToggle(t)}
                aria-pressed={on}
                className={clsx(
                  "rounded px-2.5 py-0.5 font-mono text-xs font-semibold transition-all duration-200 border",
                  on
                    ? "bg-indigo-600 border-indigo-600 text-white shadow-sm shadow-indigo-600/30"
                    : "bg-zinc-100/80 border-zinc-200/50 text-zinc-600 hover:bg-indigo-50/50 hover:text-indigo-600 hover:border-indigo-200 dark:bg-zinc-800/80 dark:border-zinc-700/50 dark:text-zinc-400 dark:hover:bg-indigo-950/20 dark:hover:text-indigo-400 dark:hover:border-indigo-900/50"
                )}
              >
                {t}
              </button>
            );
          })}
          {restCount > 0 && (
            <span className="text-xs font-semibold text-zinc-400 ml-1">+{restCount}</span>
          )}
        </div>
      )}

      {/* 펼침 영역 */}
      {expanded && (
        <div className="mt-6 border-t border-zinc-100/80 pt-5 dark:border-zinc-800/80 animate-fadeIn">
          {/* 메타 그리드 — 펼친 상태에서만 노출 */}
          <div className="mb-5 grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3">
            <ScoreBar score={s.score} />
            <div>
              <span className="text-zinc-400 font-semibold">처음 본 날</span>
              <div className="mt-0.5 text-sm font-bold tabular-nums text-zinc-800 dark:text-zinc-200">
                {s.first_seen_date}
              </div>
            </div>
            {s.state === "evolving" && s.similarity !== null && (
              <div>
                <span className="text-zinc-400 font-semibold">어제 유사도</span>
                <div className="mt-0.5 text-sm font-bold tabular-nums text-zinc-800 dark:text-zinc-200">
                  {Math.round(s.similarity * 100)}%
                </div>
              </div>
            )}
          </div>

          {/* narrative_long */}
          {s.narrative_long && (
            <div className="mb-6">
              <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-indigo-500 dark:text-indigo-400">
                상세 분석
              </h3>
              <p className="whitespace-pre-line text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                {s.narrative_long}
              </p>
            </div>
          )}

          {/* ripple effects */}
          {s.ripple_effects.length > 0 && (
            <div className="border-t border-zinc-100/50 pt-4 dark:border-zinc-850">
              <RippleSection ripples={s.ripple_effects} />
            </div>
          )}
        </div>
      )}

      {/* 펼침 토글 — 카드 하단 풀폭 버튼 */}
      {canExpand && (
        <button
          type="button"
          onClick={onToggleExpand}
          className={clsx(
            "mt-5 w-full py-2.5 text-xs font-bold rounded-xl border transition-all duration-300",
            expanded
              ? "bg-zinc-100 border-zinc-200 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-700"
              : "bg-indigo-500/5 border-indigo-500/20 text-indigo-600 hover:bg-indigo-600 hover:border-indigo-600 hover:text-white dark:bg-indigo-500/10 dark:border-indigo-500/20 dark:text-indigo-400 dark:hover:bg-indigo-500 dark:hover:text-white shadow-sm hover:shadow-indigo-500/10"
          )}
        >
          {expanded ? "접기" : "상세 분석 · 파급효과 보기"}
        </button>
      )}
    </article>
  );
}
