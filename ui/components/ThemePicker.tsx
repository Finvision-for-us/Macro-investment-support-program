"use client";

import clsx from "clsx";

import type { LifecycleStory, Theme } from "@/lib/stories";

interface Props {
  themes: Theme[];
  /** story_id → story 매핑. 카드 미리보기에 첫 스토리 제목 표시용. */
  storyById: ReadonlyMap<string, LifecycleStory>;
  onSelectTheme: (themeId: string) => void;
}

const DIRECTION_META: Record<
  Theme["direction"],
  { dot: string; label: string }
> = {
  positive: { dot: "bg-emerald-500", label: "호재 우세" },
  negative: { dot: "bg-rose-500", label: "악재 우세" },
  uncertain: { dot: "bg-zinc-400", label: "혼재" },
};

export function ThemePicker({ themes, storyById, onSelectTheme }: Props) {
  if (themes.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-zinc-300 bg-white/40 py-16 text-center dark:border-zinc-700 dark:bg-zinc-900/40">
        <div className="text-3xl">🧭</div>
        <p className="mt-3 text-sm text-zinc-500">오늘 추출된 테마가 없습니다.</p>
      </div>
    );
  }

  return (
    <section>
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        무엇부터 볼까요?
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {themes.map((t) => {
          const dir = DIRECTION_META[t.direction];
          const preview = t.story_ids
            .map((sid) => storyById.get(sid)?.title)
            .filter(Boolean) as string[];
          const first = preview[0];
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onSelectTheme(t.id)}
              className={clsx(
                "group glass-panel glass-card-hover rounded-2xl p-5 text-left glow-accent overflow-hidden relative",
                t.direction === "positive" && "hover:shadow-emerald-500/5 dark:hover:shadow-emerald-500/5",
                t.direction === "negative" && "hover:shadow-rose-500/5 dark:hover:shadow-rose-500/5"
              )}
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className={clsx(
                  "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold border",
                  t.direction === "positive" && "bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-400",
                  t.direction === "negative" && "bg-rose-500/10 border-rose-500/20 text-rose-700 dark:text-rose-400",
                  t.direction === "uncertain" && "bg-zinc-100 border-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-400"
                )}>
                  <span className={clsx("h-1.5 w-1.5 rounded-full", dir.dot)} aria-hidden />
                  {dir.label}
                </span>
                <span className="text-[10px] font-bold tabular-nums px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
                  스토리 {t.story_ids.length} · 영향력 {(t.aggregate_score * 100).toFixed(0)}
                </span>
              </div>
              <h3 className="text-base font-extrabold leading-snug text-zinc-900 dark:text-zinc-50 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors duration-200 mt-2">
                {t.name}
              </h3>
              {t.description && (
                <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300 font-medium">
                  {t.description}
                </p>
              )}
              {first && (
                <div className="mt-3 py-1.5 px-2.5 rounded-lg bg-zinc-50/50 dark:bg-zinc-800/30 border border-zinc-100/50 dark:border-zinc-800/50">
                  <p className="truncate text-xs font-semibold text-zinc-400 dark:text-zinc-500">
                    <span className="text-indigo-500 dark:text-indigo-400 mr-1">대표</span> {first}
                    {preview.length > 1 && (
                      <span className="ml-1 text-zinc-400 dark:text-zinc-550 font-bold">외 {preview.length - 1}건</span>
                    )}
                  </p>
                </div>
              )}
              <div className="mt-4 inline-flex items-center text-xs font-bold text-zinc-400 dark:text-zinc-500 transition group-hover:text-indigo-500 dark:group-hover:text-indigo-400">
                스토리 보기 <span className="ml-1 transform group-hover:translate-x-1 transition-transform">→</span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
