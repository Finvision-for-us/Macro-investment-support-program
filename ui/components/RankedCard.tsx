"use client";

import clsx from "clsx";
import Link from "next/link";

import type { RankedItem } from "@/lib/ingest2";
import { DIR_LABEL } from "@/lib/ingest2";

export function RankedCard({ item }: { item: RankedItem }) {
  const isFailed = item.title.startsWith("(");
  const barPct = Math.min(100, Math.round((item.final_score / 1.5) * 100));

  const leftBarColor = {
    negative: "before:bg-rose-500 dark:before:bg-rose-400",
    positive: "before:bg-emerald-500 dark:before:bg-emerald-400",
    uncertain: "before:bg-zinc-400 dark:before:bg-zinc-600",
  }[item.direction];

  const barColor =
    barPct >= 80
      ? "from-rose-500 to-purple-600"
      : barPct >= 50
      ? "from-indigo-500 to-purple-500"
      : "from-sky-400 to-indigo-500";

  return (
    <Link
      href={`/ingest2/${item.rank}`}
      className={clsx(
        "glass-panel glass-card-hover relative pl-5 pr-4 py-5 rounded-2xl overflow-hidden glow-accent flex flex-col gap-3 group",
        "before:absolute before:left-0 before:top-0 before:bottom-0 before:w-1"
        , leftBarColor
      )}
    >
      {/* 상단: 랭크 + 배지 */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="px-2 py-0.5 text-[10px] font-bold font-mono tracking-wider rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
          #{String(item.rank).padStart(2, "0")}
        </span>
        <KindBadge kind={item.kind} />
        <DirBadge direction={item.direction} />
        {item.has_deep && (
          <span className="px-1.5 py-0.5 text-[9px] font-bold rounded bg-sky-500/10 text-sky-500 dark:text-sky-400 border border-sky-500/20">
            DEEP
          </span>
        )}
      </div>

      {/* 제목 */}
      <h2
        className={clsx(
          "text-sm font-bold leading-snug line-clamp-3 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors",
          isFailed
            ? "text-zinc-400 italic"
            : "text-zinc-900 dark:text-zinc-50"
        )}
      >
        {item.title}
      </h2>

      {/* 티커 */}
      {item.tickers.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {item.tickers.slice(0, 4).map((t) => (
            <span
              key={t}
              className="font-mono text-[9px] font-semibold px-1.5 py-0.5 rounded bg-zinc-100/80 dark:bg-zinc-800/80 text-zinc-500 dark:text-zinc-400 border border-zinc-200/50 dark:border-zinc-700/50"
            >
              {t}
            </span>
          ))}
          {item.tickers.length > 4 && (
            <span className="text-[9px] text-zinc-400 self-center">
              +{item.tickers.length - 4}
            </span>
          )}
        </div>
      )}

      {/* 점수 바 */}
      <div className="flex items-center gap-2 mt-auto pt-1">
        <div className="flex-1 h-1 rounded-full bg-zinc-200/80 dark:bg-zinc-800 overflow-hidden">
          <div
            className={`h-full rounded-full bg-gradient-to-r ${barColor}`}
            style={{ width: `${barPct}%` }}
          />
        </div>
        <span className="text-[11px] font-bold tabular-nums text-zinc-600 dark:text-zinc-300 shrink-0">
          {item.final_score.toFixed(3)}
        </span>
      </div>
    </Link>
  );
}

function KindBadge({ kind }: { kind: "STORY" | "SIGNAL" }) {
  return kind === "STORY" ? (
    <span className="px-1.5 py-0.5 text-[9px] font-bold rounded bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20">
      STORY
    </span>
  ) : (
    <span className="px-1.5 py-0.5 text-[9px] font-bold rounded bg-violet-500/10 text-violet-600 dark:text-violet-400 border border-violet-500/20">
      SIGNAL
    </span>
  );
}

function DirBadge({ direction }: { direction: "positive" | "negative" | "uncertain" }) {
  const styles = {
    positive: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
    negative: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
    uncertain: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 border-zinc-200/50 dark:border-zinc-700/50",
  }[direction];
  return (
    <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded border ${styles}`}>
      {DIR_LABEL[direction]}
    </span>
  );
}
