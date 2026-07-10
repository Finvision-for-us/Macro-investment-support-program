import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, ExternalLink, Radio, ChevronDown, ChevronUp } from 'lucide-react'
import { telegramAPI } from '../../api'

function relativeTime(isoStr) {
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (diff < 60)   return `${diff}초 전`
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`
  return `${Math.floor(diff / 86400)}일 전`
}

function MessageCard({ item }) {
  const [expanded, setExpanded] = useState(false)
  const lines = item.text.split('\n').filter(Boolean)
  const title = lines[0].replace(/\*\*/g, '').trim()
  const body  = lines.slice(1).join('\n').trim()
  const isLong = body.split('\n').length > 4 || body.length > 300

  return (
    <div
      className="bg-white border border-slate-100 rounded-xl px-4 py-3 hover:border-indigo-200 hover:shadow-sm transition-all cursor-pointer select-none"
      onClick={() => isLong && setExpanded(e => !e)}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-slate-800 leading-snug flex-1">{title}</p>
        <div className="flex items-center gap-1 shrink-0 mt-0.5" onClick={e => e.stopPropagation()}>
          {item.url && (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-500 hover:text-indigo-700"
              title="원문 보기"
            >
              <ExternalLink size={13} />
            </a>
          )}
          <a
            href={item.permalink}
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-300 hover:text-slate-500"
            title="텔레그램 원문"
          >
            <ExternalLink size={13} />
          </a>
        </div>
      </div>

      {body && (
        <p className={`mt-1.5 text-xs text-slate-500 leading-relaxed whitespace-pre-line ${expanded ? '' : 'line-clamp-4'}`}>
          {body}
        </p>
      )}

      <div className="mt-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-300">{item.channel_name}</span>
          <span className="text-[10px] text-slate-300">·</span>
          <span className="text-[10px] text-slate-400">{relativeTime(item.date)}</span>
        </div>
        {isLong && (
          <span className="text-[10px] text-indigo-400 flex items-center gap-0.5">
            {expanded ? <><ChevronUp size={11} />접기</> : <><ChevronDown size={11} />더 보기</>}
          </span>
        )}
      </div>
    </div>
  )
}

export default function TelegramFeedView() {
  const [items, setItems]       = useState([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)
  const [hours, setHours]       = useState(24)
  const [lastFetch, setLastFetch] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await telegramAPI.getFeed(hours, 100)
      setItems(res.data.items || [])
      setLastFetch(new Date())
    } catch (e) {
      setError(e.message || '불러오기 실패')
    } finally {
      setLoading(false)
    }
  }, [hours])

  useEffect(() => { load() }, [load])

  // 60초마다 자동 갱신
  useEffect(() => {
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [load])

  return (
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="px-6 py-4 border-b border-slate-200 bg-white flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <Radio size={16} className="text-indigo-500" />
          <div>
            <h2 className="text-lg font-bold text-slate-800">텔레그램 속보</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              미국 주식 인사이더 · {lastFetch ? `${relativeTime(lastFetch.toISOString())} 갱신` : ''}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* 시간 범위 선택 */}
          <select
            value={hours}
            onChange={e => setHours(Number(e.target.value))}
            className="text-xs border border-slate-200 rounded-lg px-2 py-1.5 text-slate-600 bg-white"
          >
            <option value={6}>최근 6시간</option>
            <option value={24}>최근 24시간</option>
            <option value={48}>최근 48시간</option>
            <option value={72}>최근 72시간</option>
          </select>

          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs text-indigo-600 border border-indigo-200 rounded-lg px-3 py-1.5 hover:bg-indigo-50 disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            새로고침
          </button>
        </div>
      </div>

      {/* 피드 */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
        {error && (
          <div className="text-sm text-red-500 bg-red-50 rounded-xl px-4 py-3">
            {error}
          </div>
        )}

        {!loading && !error && items.length === 0 && (
          <div className="text-sm text-slate-400 text-center py-16">
            수집된 메시지가 없습니다.<br />
            <span className="text-xs">finvision_crawling/main.py 를 실행해 데이터를 수집하세요.</span>
          </div>
        )}

        {items.map(item => (
          <MessageCard key={item.id} item={item} />
        ))}
      </div>

      {/* 하단 카운트 */}
      {items.length > 0 && (
        <div className="px-6 py-2 border-t border-slate-100 bg-white shrink-0">
          <p className="text-[11px] text-slate-400">{items.length}개 메시지</p>
        </div>
      )}
    </div>
  )
}
