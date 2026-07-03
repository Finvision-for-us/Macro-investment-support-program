import Link from "next/link";
import { notFound } from "next/navigation";

import { DIR_LABEL } from "@/lib/ingest2";
import { readIngest2Report } from "@/lib/ingest2-server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

interface Props {
  params: { rank: string };
}

export default async function RankedDetailPage({ params }: Props) {
  const rank = parseInt(params.rank, 10);
  if (isNaN(rank)) notFound();

  const report = await readIngest2Report();
  if (!report) notFound();

  const item = report.items.find((i) => i.rank === rank);
  if (!item) notFound();

  const isFailed = item.title.startsWith("(");
  const dirStyle = {
    positive: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
    negative: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
    uncertain: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 border-zinc-200/50 dark:border-zinc-700/50",
  }[item.direction];

  const leftBarColor = {
    negative: "bg-rose-500 dark:bg-rose-400",
    positive: "bg-emerald-500 dark:bg-emerald-400",
    uncertain: "bg-zinc-400 dark:bg-zinc-600",
  }[item.direction];

  return (
    <main className="pt-6 pb-16">
      {/* 뒤로가기 */}
      <Link
        href="/ingest2"
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors mb-8"
      >
        ← 목록으로
      </Link>

      {/* 헤더 */}
      <div className="flex items-start gap-4 mb-8">
        <div className={`mt-1 w-1.5 self-stretch rounded-full shrink-0 ${leftBarColor}`} />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <span className="px-2.5 py-0.5 text-[10px] font-bold font-mono rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
              #{String(item.rank).padStart(2, "0")}
            </span>
            <span className={item.kind === "STORY"
              ? "px-2 py-0.5 text-[10px] font-bold rounded bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20"
              : "px-2 py-0.5 text-[10px] font-bold rounded bg-violet-500/10 text-violet-600 dark:text-violet-400 border border-violet-500/20"
            }>
              {item.kind}
            </span>
            <span className={`px-2 py-0.5 text-[10px] font-bold rounded border ${dirStyle}`}>
              {DIR_LABEL[item.direction]}
            </span>
            {item.has_deep && (
              <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-sky-500/10 text-sky-500 dark:text-sky-400 border border-sky-500/20">
                딥 리서치
              </span>
            )}
          </div>
          <h1 className={`text-2xl font-extrabold leading-snug ${isFailed ? "text-zinc-400 italic" : "text-zinc-900 dark:text-zinc-50"}`}>
            {item.title}
          </h1>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl font-bold tabular-nums text-zinc-800 dark:text-zinc-200">
            {item.final_score.toFixed(3)}
          </div>
          <div className="text-[10px] text-zinc-400 mt-0.5">최종 점수</div>
        </div>
      </div>

      {/* 메타 그리드 */}
      <div className="grid grid-cols-3 gap-3 mb-8">
        {[
          ["영향도", item.impact.toFixed(3)],
          ["이벤트 수", `${item.n_events}건`],
          ["출처 수", `${item.n_sources}개`],
        ].map(([label, val]) => (
          <div key={label} className="glass-panel rounded-xl p-4">
            <div className="text-xs text-zinc-400 dark:text-zinc-500 font-semibold mb-1">{label}</div>
            <div className="text-lg font-bold tabular-nums text-zinc-800 dark:text-zinc-200">{val}</div>
          </div>
        ))}
      </div>

      {/* 티커 */}
      {item.tickers.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">관련 티커</h2>
          <div className="flex flex-wrap gap-2">
            {item.tickers.map((t) => (
              <span key={t} className="font-mono text-xs font-semibold px-2.5 py-1 rounded-lg bg-zinc-100/80 dark:bg-zinc-800/80 text-zinc-700 dark:text-zinc-300 border border-zinc-200/60 dark:border-zinc-700/60">
                {t}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 요약 */}
      {item.narrative_short && !isFailed && (
        <section className="mb-8">
          <h2 className="section-title">요약</h2>
          <p className="text-base leading-relaxed text-zinc-700 dark:text-zinc-300 font-medium">
            {item.narrative_short}
          </p>
        </section>
      )}

      {/* 인과 체인 */}
      {item.chain.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">인과 체인</h2>
          <div className="space-y-3">
            {item.chain.map((edge, i) => (
              <div key={i} className="glass-panel rounded-xl p-4">
                <div className="flex items-start gap-3 mb-2">
                  <div className="flex-1">
                    <div className="text-xs text-zinc-400 mb-0.5">원인</div>
                    <p className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">{edge.from}</p>
                  </div>
                  <span className="text-zinc-300 dark:text-zinc-600 text-lg self-center">→</span>
                  <div className="flex-1">
                    <div className="text-xs text-zinc-400 mb-0.5">결과</div>
                    <p className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">{edge.to}</p>
                  </div>
                </div>
                <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed mb-2">{edge.mechanism}</p>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-zinc-400">신뢰도 {Math.round(edge.confidence * 100)}%</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 font-mono text-zinc-500">{edge.inferred_by}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 상세 분석 */}
      {item.narrative_long && !isFailed && (
        <section className="mb-8">
          <h2 className="section-title">상세 분석</h2>
          <p className="whitespace-pre-line text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
            {item.narrative_long}
          </p>
        </section>
      )}

      {/* 랭킹 사유 */}
      {item.reasons.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">랭킹 사유</h2>
          <div className="flex flex-wrap gap-2">
            {item.reasons.map((r) => (
              <span key={r} className="px-2.5 py-1 rounded-lg font-mono text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
                {r}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 원본 이벤트 */}
      {item.events.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">원본 이벤트</h2>
          <ul className="space-y-2">
            {item.events.map((ev, i) => (
              <li key={i} className="glass-panel rounded-xl px-4 py-3">
                <a
                  href={ev.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-semibold text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  {ev.title}
                </a>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-400">
                  <span>{ev.publishers.join(", ")}</span>
                  <span>·</span>
                  <span>{new Date(ev.occurred_at).toLocaleString("ko-KR")}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 네비게이션 */}
      <div className="flex justify-between mt-12 pt-6 border-t border-zinc-200/60 dark:border-zinc-800/60">
        {rank > 1 ? (
          <Link href={`/ingest2/${rank - 1}`} className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
            ← #{rank - 1}
          </Link>
        ) : <span />}
        {rank < report.items.length ? (
          <Link href={`/ingest2/${rank + 1}`} className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">
            #{rank + 1} →
          </Link>
        ) : <span />}
      </div>
    </main>
  );
}
