import clsx from "clsx";

import type { MacroEvent } from "@/lib/stories";

export function MacroPanel({ events }: { events: MacroEvent[] }) {
  if (events.length === 0) return null;

  // 동일 series 의 중복은 최신만 (sigma_z 절댓값 큰 순)
  const seen = new Set<string>();
  const dedup: MacroEvent[] = [];
  for (const e of [...events].sort(
    (a, b) => Math.abs(b.sigma_z) - Math.abs(a.sigma_z)
  )) {
    if (seen.has(e.series_id)) continue;
    seen.add(e.series_id);
    dedup.push(e);
  }

  return (
    <section className="mb-8">
      <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
        오늘의 거시 · 1σ 이상 변화
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {dedup.slice(0, 6).map((m) => {
          const isUp = m.change >= 0;
          return (
            <article
              key={m.id}
              className="glass-panel glass-card-hover rounded-xl p-4 flex flex-col justify-between"
              title={m.summary_ko}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-bold text-zinc-800 dark:text-zinc-200">
                  {m.series_label_ko}
                </span>
                <span className="text-[10px] font-medium tabular-nums text-zinc-400">
                  {m.observed_at.slice(0, 10)}
                </span>
              </div>
              <div className="mt-3 flex items-center justify-between">
                <div className="flex items-baseline gap-2">
                  <span className="text-xl font-extrabold tabular-nums tracking-tight text-zinc-900 dark:text-zinc-50">
                    {formatValue(m.value, m.unit)}
                  </span>
                  <span
                    className={clsx(
                      "text-xs font-bold tabular-nums px-2 py-0.5 rounded-md border",
                      isUp
                        ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                        : "bg-rose-500/10 border-rose-500/20 text-rose-600 dark:text-rose-400"
                    )}
                  >
                    {isUp ? "▲" : "▼"} {formatDelta(m.change, m.prev_value, m.unit)}
                  </span>
                </div>
                <span className="text-[10px] font-bold tabular-nums px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
                  {m.sigma_z > 0 ? "+" : ""}{m.sigma_z.toFixed(1)}σ
                </span>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function formatValue(v: number, unit: string): string {
  if (unit === "%" || unit === "%p") return `${v.toFixed(2)}%`;
  if (unit.startsWith("$")) return `$${v.toFixed(2)}`;
  if (unit === "엔/달러") return `${v.toFixed(2)}엔`;
  if (unit === "index") return v.toFixed(2);
  return `${v.toFixed(2)}`;
}

function formatDelta(change: number, prev: number, unit: string): string {
  if (unit === "%" || unit === "%p") {
    const sign = change >= 0 ? "+" : "";
    return `${sign}${change.toFixed(2)}%p`;
  }
  const pct = prev !== 0 ? (change / prev) * 100 : 0;
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}
