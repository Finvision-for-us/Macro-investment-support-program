"use client";

import clsx from "clsx";

import type { Theme } from "@/lib/stories";

interface Props {
  theme: Theme;
  storiesShown: number;
  onBackToPicker: () => void;
}

const DIRECTION_META: Record<
  Theme["direction"],
  { dot: string; label: string }
> = {
  positive: { dot: "bg-emerald-500", label: "호재 우세" },
  negative: { dot: "bg-rose-500", label: "악재 우세" },
  uncertain: { dot: "bg-zinc-400", label: "혼재" },
};

export function ThemeHeader({ theme, storiesShown, onBackToPicker }: Props) {
  const dir = DIRECTION_META[theme.direction];
  return (
    <section className="mb-8">
      <button
        type="button"
        onClick={onBackToPicker}
        className={clsx(
          "mb-5 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-zinc-200 bg-white/80 hover:bg-zinc-50 hover:text-indigo-600",
          "dark:border-zinc-800 dark:bg-zinc-900/80 dark:hover:bg-zinc-800 dark:hover:text-indigo-400",
          "text-xs font-bold text-zinc-500 transition-all duration-200 shadow-sm backdrop-blur-sm"
        )}
      >
        ← 모든 테마
      </button>
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        <span className={clsx(
          "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold border",
          theme.direction === "positive" && "bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-400",
          theme.direction === "negative" && "bg-rose-500/10 border-rose-500/20 text-rose-700 dark:text-rose-400",
          theme.direction === "uncertain" && "bg-zinc-100 border-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-400"
        )}>
          <span className={clsx("h-1.5 w-1.5 rounded-full", dir.dot)} aria-hidden />
          {dir.label}
        </span>
        <span className="text-zinc-300 dark:text-zinc-800">|</span>
        <span className="tabular-nums font-bold">스토리 {theme.story_ids.length}</span>
        <span className="text-zinc-300 dark:text-zinc-800">|</span>
        <span className="tabular-nums font-bold">
          영향력 {(theme.aggregate_score * 100).toFixed(0)}
        </span>
      </div>
      <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-zinc-900 dark:text-zinc-50">{theme.name}</h1>
      {theme.description && (
        <p className="mt-2.5 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400 font-medium">
          {theme.description}
        </p>
      )}
      {storiesShown !== theme.story_ids.length && (
        <p className="mt-3 text-xs text-zinc-400 font-bold">
          ({storiesShown}/{theme.story_ids.length} 표시 — 필터 적용 중)
        </p>
      )}
    </section>
  );
}
