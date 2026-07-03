"use client";

import type { Ingest2Report } from "@/lib/ingest2";
import { RankedCard } from "./RankedCard";

export function Ingest2Board({ report }: { report: Ingest2Report }) {
  const s = report.pipeline_stats;

  return (
    <main className="pt-6">
      <header className="mb-8">
        <div className="flex items-center gap-2">
          <span className="px-3 py-1 text-xs font-bold uppercase tracking-wider rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 shadow-sm">
            {report.generated_at.slice(0, 10)}
          </span>
          <span className="px-2.5 py-1 text-xs font-bold rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
            최근 {report.window_hours}h
          </span>
        </div>
        <h1 className="mt-4 text-4xl font-extrabold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-zinc-900 via-zinc-800 to-zinc-600 dark:from-zinc-50 dark:via-zinc-200 dark:to-zinc-400">
          오늘 볼 시장 재료
        </h1>
        <div className="mt-4 flex flex-wrap gap-2">
          {([
            ["클러스터", s.clusters_in.toLocaleString()],
            ["스토리", String(s.stories)],
            ["시그널", String(s.signals)],
            ["인과 엣지", String(s.edges)],
            ["딥 리서치", String(s.deep)],
          ] as [string, string][]).map(([label, val]) => (
            <div
              key={label}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/60 dark:bg-zinc-900/60 border border-zinc-200/60 dark:border-zinc-800/60 text-xs backdrop-blur-sm"
            >
              <span className="text-zinc-400 dark:text-zinc-500">{label}</span>
              <span className="font-bold text-zinc-700 dark:text-zinc-300">{val}</span>
            </div>
          ))}
        </div>
      </header>

      <section className="grid grid-cols-2 gap-4">
        {report.items.map((item) => (
          <RankedCard key={item.rank} item={item} />
        ))}
      </section>

      <footer className="mt-10 text-center text-[10px] text-zinc-400 dark:text-zinc-500 font-bold">
        생성 시각: {report.generated_at}
      </footer>
    </main>
  );
}
