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

function DirectionBadge({ direction }: { direction: Theme["direction"] }) {
  const dir = DIRECTION_META[direction];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold border",
        direction === "positive" &&
          "bg-emerald-500/10 border-emerald-500/20 text-emerald-700 dark:text-emerald-400",
        direction === "negative" &&
          "bg-rose-500/10 border-rose-500/20 text-rose-700 dark:text-rose-400",
        direction === "uncertain" &&
          "bg-zinc-100 border-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:border-zinc-700 dark:text-zinc-400"
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", dir.dot)} aria-hidden />
      {dir.label}
    </span>
  );
}

function ThemeCard({
  theme: t,
  storyById,
  onSelect,
  hero = false,
}: {
  theme: Theme;
  storyById: ReadonlyMap<string, LifecycleStory>;
  onSelect: (id: string) => void;
  hero?: boolean;
}) {
  const preview = t.story_ids
    .map((sid) => storyById.get(sid)?.title)
    .filter(Boolean) as string[];
  const first = preview[0];

  return (
    <button
      type="button"
      onClick={() => onSelect(t.id)}
      className={clsx(
        "group glass-panel glass-card-hover rounded-2xl text-left glow-accent overflow-hidden relative w-full",
        hero ? "p-6" : "p-5",
        t.direction === "positive" &&
          "hover:shadow-emerald-500/5 dark:hover:shadow-emerald-500/5",
        t.direction === "negative" &&
          "hover:shadow-rose-500/5 dark:hover:shadow-rose-500/5"
      )}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <DirectionBadge direction={t.direction} />
        <span className="text-[10px] font-bold tabular-nums px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
          스토리 {t.story_ids.length} · 영향력 {(t.aggregate_score * 100).toFixed(0)}
        </span>
      </div>
      <h3
        className={clsx(
          "font-extrabold leading-snug text-zinc-900 dark:text-zinc-50 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors duration-200 mt-2",
          hero ? "text-xl" : "text-base"
        )}
      >
        {t.name}
      </h3>
      {t.description && (
        <p
          className={clsx(
            "mt-2 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300 font-medium",
            hero ? "line-clamp-3" : "line-clamp-2"
          )}
        >
          {t.description}
        </p>
      )}
      {first && (
        <div className="mt-3 py-1.5 px-2.5 rounded-lg bg-zinc-50/50 dark:bg-zinc-800/30 border border-zinc-100/50 dark:border-zinc-800/50">
          <p className="truncate text-xs font-semibold text-zinc-400 dark:text-zinc-500">
            <span className="text-indigo-500 dark:text-indigo-400 mr-1">대표</span>{" "}
            {first}
            {preview.length > 1 && (
              <span className="ml-1 text-zinc-400 dark:text-zinc-550 font-bold">
                외 {preview.length - 1}건
              </span>
            )}
          </p>
        </div>
      )}
      <div className="mt-4 inline-flex items-center text-xs font-bold text-zinc-400 dark:text-zinc-500 transition group-hover:text-indigo-500 dark:group-hover:text-indigo-400">
        스토리 보기{" "}
        <span className="ml-1 transform group-hover:translate-x-1 transition-transform">
          →
        </span>
      </div>
    </button>
  );
}

function SignalCard({
  theme: t,
  onSelect,
}: {
  theme: Theme;
  onSelect: (id: string) => void;
}) {
  const dir = DIRECTION_META[t.direction];
  const ticker = t.affected_tickers[0];
  return (
    <button
      type="button"
      onClick={() => onSelect(t.id)}
      className="group glass-panel glass-card-hover w-full rounded-xl p-3.5 text-left"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className={clsx("h-1.5 w-1.5 rounded-full", dir.dot)} aria-hidden />
        {ticker ? (
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            {ticker}
          </span>
        ) : (
          <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
            단독
          </span>
        )}
      </div>
      <p className="line-clamp-2 text-[13px] font-semibold leading-snug text-zinc-800 transition-colors group-hover:text-indigo-600 dark:text-zinc-100 dark:group-hover:text-indigo-400">
        {t.name}
      </p>
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-3 text-[10px] font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
      {children}
    </h3>
  );
}

export function ThemePicker({ themes, storyById, onSelectTheme }: Props) {
  if (themes.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-zinc-300 bg-white/40 py-16 text-center dark:border-zinc-700 dark:bg-zinc-900/40">
        <div className="text-3xl">🧭</div>
        <p className="mt-3 text-sm text-zinc-500">오늘 추출된 테마가 없습니다.</p>
      </div>
    );
  }

  const headline = themes.find((t) => t.tier === "headline");
  const major = themes.filter((t) => (t.tier ?? "major") === "major");
  const minor = themes.filter((t) => t.tier === "minor");

  return (
    <section>
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        무엇부터 볼까요?
      </h2>

      {/* 오늘의 헤드라인 — 히어로 카드 */}
      {headline && (
        <div className="mb-6">
          <SectionLabel>오늘의 헤드라인</SectionLabel>
          <ThemeCard
            theme={headline}
            storyById={storyById}
            onSelect={onSelectTheme}
            hero
          />
        </div>
      )}

      {/* 주요 테마 — 그리드 */}
      {major.length > 0 && (
        <div className="mb-6">
          {headline && <SectionLabel>주요 테마</SectionLabel>}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {major.map((t) => (
              <ThemeCard
                key={t.id}
                theme={t}
                storyById={storyById}
                onSelect={onSelectTheme}
              />
            ))}
          </div>
        </div>
      )}

      {/* 단독 시그널 — 어느 테마에도 안 묶인 스토리, 각자 실제 제목으로 */}
      {minor.length > 0 && (
        <div className="mt-6">
          <SectionLabel>단독 시그널</SectionLabel>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {minor.map((t) => (
              <SignalCard key={t.id} theme={t} onSelect={onSelectTheme} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
