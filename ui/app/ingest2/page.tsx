import { Ingest2Board } from "@/components/Ingest2Board";
import { readIngest2Report } from "@/lib/ingest2-server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

function EmptyState() {
  return (
    <main>
      <header className="mb-6 border-b border-zinc-200 pb-4 pt-4 dark:border-zinc-800">
        <h1 className="text-2xl font-bold">오늘 볼 시장 재료</h1>
      </header>
      <div className="mt-12 rounded-lg border border-dashed border-zinc-300 bg-white/40 py-16 text-center dark:border-zinc-700 dark:bg-zinc-900/40">
        <div className="text-4xl">📭</div>
        <p className="mt-3 text-sm text-zinc-500">
          아직 ingest2 리포트가 없습니다.
        </p>
        <p className="mt-2 text-xs text-zinc-400">
          아래 명령을 실행한 뒤 새로고침:
        </p>
        <code className="mt-2 inline-block rounded bg-zinc-200 px-2 py-1 font-mono text-xs dark:bg-zinc-800">
          python -m ingest2.candidates.run_live
        </code>
      </div>
    </main>
  );
}

export default async function Ingest2Page() {
  const report = await readIngest2Report();
  if (!report) return <EmptyState />;
  return <Ingest2Board report={report} />;
}
