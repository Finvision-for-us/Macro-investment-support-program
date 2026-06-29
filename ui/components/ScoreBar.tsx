/**
 * 점수 시각화 — 숫자가 메인, 막대는 보조.
 * 펼친 카드 메타 영역에서 사용.
 */
export function ScoreBar({ score, label = "영향력" }: { score: number; label?: string }) {
  const pct = Math.max(0, Math.min(100, Math.round(score * 100)));
  
  // 스코어별 그라데이션 색상 매핑
  const barGradient = 
    pct >= 80 
      ? "from-rose-500 to-purple-600" 
      : pct >= 50 
        ? "from-indigo-500 to-purple-500" 
        : "from-sky-400 to-indigo-500";

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-baseline gap-1">
        <span className="text-xs text-zinc-500 dark:text-zinc-400">{label}</span>
        <span className="text-sm font-bold tabular-nums text-zinc-800 dark:text-zinc-200">{pct}</span>
      </div>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-200/80 dark:bg-zinc-800/80">
        <div
          className={`h-full bg-gradient-to-r ${barGradient} transition-all duration-500 rounded-full`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

