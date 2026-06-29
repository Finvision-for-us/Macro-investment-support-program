import { TodayBoard } from "@/components/TodayBoard";
import { topStories } from "@/lib/stories";
import { readStoriesLatest } from "@/lib/stories-server";

// 데이터 파일이 매 batch 실행 후 새로 쓰이므로 캐시하지 않고 매 요청 새로 읽는다.
export const dynamic = "force-dynamic";
export const revalidate = 0;

function EmptyState({ reason }: { reason: "no-file" | "no-stories" }) {
  return (
    <main>
      <header className="mb-6 border-b border-zinc-200 pb-4 pt-4 dark:border-zinc-800">
        <h1 className="text-2xl font-bold">오늘의 스토리</h1>
      </header>
      <div className="mt-12 rounded-lg border border-dashed border-zinc-300 bg-white/40 py-16 text-center dark:border-zinc-700 dark:bg-zinc-900/40">
        <div className="text-4xl">{reason === "no-file" ? "🗂️" : "📭"}</div>
        {reason === "no-file" ? (
          <>
            <p className="mt-3 text-sm text-zinc-500">
              아직 lifecycle 스냅샷이 없습니다.
            </p>
            <p className="mt-3 text-xs text-zinc-400">
              백엔드에서 아래 명령을 실행한 뒤 새로고침:
            </p>
            <code className="mt-2 inline-block rounded bg-zinc-200 px-2 py-1 font-mono text-xs dark:bg-zinc-800">
              python -m src.cli lifecycle link &lt;label&gt;
            </code>
          </>
        ) : (
          <p className="mt-3 text-sm text-zinc-500">
            표시할 스토리가 없습니다 (제목/내러티브 미생성).
          </p>
        )}
      </div>
    </main>
  );
}

export default async function TodayPage() {
  const data = await readStoriesLatest();
  if (!data) return <EmptyState reason="no-file" />;

  // M3.5 수정: 오늘 신호 있는 스토리만 Top 10 후보 — carry-over 가 테마와 어긋나는 문제 방지
  const top = topStories(data.stories, 10, data.date);
  if (top.length === 0) return <EmptyState reason="no-stories" />;

  return <TodayBoard data={data} topStories={top} />;
}
