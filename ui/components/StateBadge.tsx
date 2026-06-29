import clsx from "clsx";

import type { LifecycleState } from "@/lib/stories";

const META: Record<
  LifecycleState,
  { label: string; dot: string; container: string; help: string }
> = {
  active: {
    label: "신규",
    dot: "bg-emerald-500 shadow-sm shadow-emerald-500/50",
    container: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20",
    help: "오늘 새로 등장한 스토리.",
  },
  evolving: {
    label: "진행중",
    dot: "bg-amber-500 shadow-sm shadow-amber-500/50",
    container: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/20",
    help: "어제 본 스토리에 오늘 새 신호 합류.",
  },
  resolved: {
    label: "종결",
    dot: "bg-zinc-400 dark:bg-zinc-500",
    container: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800/60 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/50",
    help: "마지막 신호 후 3일 이상 무신호.",
  },
};

export function StateBadge({ state }: { state: LifecycleState }) {
  const m = META[state];
  return (
    <span
      title={m.help}
      className={clsx(
        "inline-flex cursor-help items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold tracking-wide transition-all duration-200",
        m.container
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", m.dot)} aria-hidden />
      {m.label}
    </span>
  );
}

