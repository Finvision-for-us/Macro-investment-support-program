import { useEffect, useState, useRef, useCallback } from 'react'
import { RefreshCw, Loader2, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { storiesAPI } from '../../api'
import { TodayBoard } from '../pipeline/TodayBoard'

function topStories(stories, onlyDate) {
  return stories
    .filter(s => s.title.trim().length > 0)
    .filter(s => (onlyDate ? s.last_seen_date === onlyDate : true))
    .sort((a, b) => b.score !== a.score ? b.score - a.score : b.first_seen_date.localeCompare(a.first_seen_date))
}

function fmtElapsed(sec) {
  if (sec == null) return ''
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m > 0 ? `${m}분 ${s}초` : `${s}초`
}

export default function PipelineView() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const [job, setJob]   = useState(null)   // {status, elapsed, log:[]}
  const pollRef = useRef(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await storiesAPI.getLatest()
      setData(res.data)
    } catch (e) {
      setError(e.response?.status === 404
        ? '아직 스냅샷이 없습니다. 수집 최신화를 실행하세요.'
        : e.message || '불러오기 실패')
    } finally {
      setLoading(false)
    }
  }, [])

  const stopPolling = () => { clearInterval(pollRef.current); pollRef.current = null }

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const r = await storiesAPI.refreshStatus()
        setJob(r.data)
        if (r.data.status === 'done') {
          stopPolling()
          await load()
        } else if (r.data.status === 'error') {
          stopPolling()
        }
      } catch { /* 폴링 실패는 무시하고 다음 주기 재시도 */ }
    }, 2500)
  }, [load])

  // 최초 로드 + 이미 실행 중인 잡이 있으면 이어서 폴링
  useEffect(() => {
    load()
    storiesAPI.refreshStatus()
      .then(r => { if (r.data.status === 'running') { setJob(r.data); startPolling() } })
      .catch(() => {})
    return stopPolling
  }, [load, startPolling])

  const startRefresh = async () => {
    const ok = window.confirm(
      '전체 뉴스 수집 파이프라인을 재실행합니다.\n' +
      '수 분 소요되며 Gemini API 비용이 발생합니다.\n\n진행할까요?'
    )
    if (!ok) return
    try {
      await storiesAPI.refresh()
      setJob({ status: 'running', elapsed: 0, log: [] })
      startPolling()
    } catch (e) {
      if (e.response?.status === 409) {   // 이미 실행 중 — 폴링만 이어붙임
        startPolling()
      } else {
        alert('실행 실패: ' + (e.message || '알 수 없는 오류'))
      }
    }
  }

  const running = job?.status === 'running'
  const lastLog = job?.log?.length ? job.log[job.log.length - 1] : ''

  return (
    <div className="flex flex-col h-full">
      {/* 툴바 */}
      <div className="px-6 py-3 border-b border-slate-200 bg-white flex items-center justify-between shrink-0">
        <p className="text-xs text-slate-400">
          {data ? `생성: ${data.generated_at}` : '수집 파이프라인'}
        </p>
        <button
          onClick={startRefresh}
          disabled={running}
          className="flex items-center gap-1.5 text-xs font-semibold text-indigo-600 border border-indigo-200 rounded-lg px-3 py-1.5 hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          title="뉴스 수집 파이프라인 전체 재실행 (수 분 소요 · Gemini 비용 발생)"
        >
          <RefreshCw size={12} className={running ? 'animate-spin' : ''} />
          {running ? '수집 중…' : '수집 최신화'}
        </button>
      </div>

      {/* 진행 배너 */}
      {job && job.status !== 'idle' && (
        <div className={`px-6 py-2.5 border-b text-xs flex items-center gap-2 shrink-0 ${
          job.status === 'running' ? 'bg-indigo-50/60 border-indigo-100 text-indigo-700'
          : job.status === 'done'  ? 'bg-emerald-50/60 border-emerald-100 text-emerald-700'
          : 'bg-rose-50/60 border-rose-100 text-rose-700'
        }`}>
          {job.status === 'running' && <Loader2 size={13} className="animate-spin shrink-0" />}
          {job.status === 'done'    && <CheckCircle2 size={13} className="shrink-0" />}
          {job.status === 'error'   && <AlertTriangle size={13} className="shrink-0" />}
          <span className="font-semibold shrink-0">
            {job.status === 'running' && `수집 중 · ${fmtElapsed(job.elapsed)}`}
            {job.status === 'done'    && `완료 · ${fmtElapsed(job.elapsed)}`}
            {job.status === 'error'   && `실패${job.returncode != null ? ` (코드 ${job.returncode})` : ''}`}
          </span>
          {lastLog && (
            <span className="text-slate-500 font-mono truncate">— {lastLog}</span>
          )}
        </div>
      )}

      {/* 콘텐츠 */}
      <div className="flex-1 overflow-y-auto px-6 py-2">
        {loading && !data && (
          <div className="flex items-center justify-center h-full text-sm text-slate-400">로딩 중…</div>
        )}

        {!loading && error && !data && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <p className="text-sm text-slate-500">{error}</p>
            <button onClick={load} className="flex items-center gap-1.5 text-xs text-indigo-600 border border-indigo-200 rounded-lg px-3 py-1.5 hover:bg-indigo-50">
              <RefreshCw size={12} /> 다시 불러오기
            </button>
          </div>
        )}

        {data && (() => {
          const top = topStories(data.stories, data.date)
          if (top.length === 0) return (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <div className="text-4xl">📭</div>
              <p className="text-sm text-slate-500">표시할 스토리가 없습니다 (제목/내러티브 미생성).</p>
            </div>
          )
          return <TodayBoard data={data} topStories={top} />
        })()}
      </div>
    </div>
  )
}
