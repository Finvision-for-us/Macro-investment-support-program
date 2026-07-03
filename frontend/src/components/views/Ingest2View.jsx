const INGEST2_URL = 'http://localhost:3000/today'

export default function Ingest2View() {
  return (
    <div className="flex flex-col h-full">
      <div className="px-6 py-4 border-b border-slate-200 bg-white flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-lg font-bold text-slate-800">뉴스 수집 파이프라인</h2>
          <p className="text-xs text-slate-400 mt-0.5">run_ingest2_web.py 실행 결과 · 라이프사이클 스토리 + 테마</p>
        </div>
        <a
          href={INGEST2_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-indigo-600 hover:underline"
        >
          새 탭으로 열기 ↗
        </a>
      </div>
      <iframe
        src={INGEST2_URL}
        title="뉴스 수집 파이프라인"
        className="flex-1 w-full border-none"
      />
    </div>
  )
}
