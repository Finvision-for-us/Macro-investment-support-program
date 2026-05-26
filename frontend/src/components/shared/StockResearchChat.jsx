import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Send, X, ChevronDown, ChevronUp, ExternalLink,
  Loader2, BookOpen, Zap, ZapOff, Plus, Trash2,
  CheckCircle, Edit3, History
} from 'lucide-react'

const API = '/api/deep-research'

// ── 인라인 URL 패턴 → [n] 각주 변환 ──
// 처리하는 패턴:
//   [source: https://...]
//   (Tavily | https://...)
//   (Parallel | https://...)
function parseSourcesFromText(text) {
  if (!text) return { clean: '', urls: [] }
  const urls = []
  const seen = new Map()
  let counter = 0

  const register = (url) => {
    const trimmed = url.trim()
    if (!seen.has(trimmed)) {
      counter++
      seen.set(trimmed, counter)
      urls.push(trimmed)
    }
    return `[${seen.get(trimmed)}]`
  }

  const clean = text
    // [source: URL]
    .replace(/\[source:\s*(https?:\/\/[^\]]+)\]/gi, (_, url) => register(url))
    // (Tavily | URL) or (Parallel | URL) etc.
    .replace(/\(\s*(?:Tavily|Parallel|SEC|EDGAR|Source)\s*\|\s*(https?:\/\/[^\s)]+)\s*\)/gi, (_, url) => register(url))
    // bare https:// URLs in parentheses: (https://...)
    .replace(/\(\s*(https?:\/\/[^\s)]{10,})\s*\)/g, (_, url) => register(url))
    .replace(/\s{2,}/g, ' ').trim()

  return { clean, urls }
}

function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url }
}

// [n] → [n](#fn-n) 마크다운 링크 변환 (ReactMarkdown a 렌더러에서 각주 처리)
function preprocessFootnotes(text) {
  if (!text) return text
  return text.replace(/\[(\d{1,3})\]/g, (_, n) => `[${n}](#fn-${n})`)
}

function credibilityToTier(c) {
  if (c === 'high') return 'Tier 1'
  if (c === 'medium') return 'Tier 2'
  return 'Tier 4'
}

// ── 출처 토글 카드 ──
function SourceCards({ urls, extra = [] }) {
  const [open, setOpen] = useState(false)
  const all = [...new Set([...urls, ...extra])]
  if (all.length === 0) return null
  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen(p => !p)}
        className="flex items-center gap-1.5 text-[11px] text-indigo-500 hover:text-indigo-700 font-medium transition-colors"
      >
        <ExternalLink size={10} />
        출처 {all.length}개 {open ? '▲' : '▼'}
      </button>
      {open && (
        <div className="mt-2 grid grid-cols-1 gap-1.5">
          {all.map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-2 bg-slate-50 border border-slate-100 rounded-lg hover:bg-indigo-50 hover:border-indigo-200 transition-colors group"
            >
              <span className="flex-shrink-0 text-[10px] font-bold text-indigo-400 w-5 text-center">[{i + 1}]</span>
              <img
                src={`https://www.google.com/s2/favicons?domain=${domainOf(url)}&sz=16`}
                alt=""
                className="w-4 h-4 flex-shrink-0 rounded-sm"
                onError={e => { e.target.style.display = 'none' }}
              />
              <div className="flex-1 min-w-0">
                <p className="text-[11px] font-medium text-slate-600 group-hover:text-indigo-700 truncate">{domainOf(url)}</p>
                <p className="text-[10px] text-slate-400 truncate">{url}</p>
              </div>
              <ExternalLink size={10} className="flex-shrink-0 text-slate-300 group-hover:text-indigo-400" />
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 출처 커버리지 섹션 ──
function CoverageSection({ coverage }) {
  const [open, setOpen] = useState(false)
  const total = (coverage.checked?.length || 0) + (coverage.unchecked?.length || 0)
  if (total === 0) return null

  return (
    <div className="border border-slate-100 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(p => !p)}
        className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 hover:bg-slate-100 text-left transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-600">출처 커버리지</span>
          <span className="text-[10px] px-1.5 py-0.5 bg-emerald-100 text-emerald-700 rounded-full font-medium">
            확인 {coverage.checked?.length || 0}
          </span>
          {coverage.unchecked?.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded-full font-medium">
              미확인 {coverage.unchecked.length}
            </span>
          )}
        </div>
        {open ? <ChevronUp size={12} className="text-slate-400" /> : <ChevronDown size={12} className="text-slate-400" />}
      </button>

      {open && (
        <div className="px-4 py-3 border-t border-slate-100 bg-white space-y-3">
          {coverage.checked?.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-emerald-600 uppercase tracking-wide mb-1.5">확인된 출처</p>
              <ul className="space-y-1">
                {coverage.checked.map((item, i) => (
                  <li key={i} className="flex items-start gap-2 text-[12px] text-slate-600">
                    <span className="text-emerald-500 flex-shrink-0 mt-0.5">✓</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {coverage.unchecked?.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-amber-600 uppercase tracking-wide mb-1.5">미확인 출처</p>
              <ul className="space-y-1">
                {coverage.unchecked.map((item, i) => (
                  <li key={i} className="flex items-start gap-2 text-[12px] text-slate-500">
                    <span className="text-amber-400 flex-shrink-0 mt-0.5">○</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {coverage.notes && (
            <p className="text-[11px] text-slate-400 italic border-t border-slate-50 pt-2">{coverage.notes}</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── 마크다운 렌더러 ──
function MarkdownContent({ text, className = '', onFootnoteClick }) {
  if (!text) return null
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="text-sm font-bold text-slate-800 mt-3 mb-1.5">{children}</h1>,
          h2: ({ children }) => (
            <h2 className="text-[13px] font-bold text-slate-700 mt-3 mb-1 flex items-center gap-1.5">
              <span className="w-1 h-3 bg-indigo-400 rounded-full flex-shrink-0" />
              {children}
            </h2>
          ),
          h3: ({ children }) => <h3 className="text-[13px] font-semibold text-slate-600 mt-2 mb-0.5">{children}</h3>,
          p: ({ children }) => <p className="text-[13px] text-slate-600 leading-relaxed mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="space-y-1 mb-2">{children}</ul>,
          ol: ({ children }) => <ol className="space-y-1 mb-2 list-decimal list-inside">{children}</ol>,
          li: ({ ordered, children }) => ordered
            ? <li className="text-[13px] text-slate-600 leading-snug">{children}</li>
            : (
              <li className="text-[13px] text-slate-600 leading-snug flex gap-1.5">
                <span className="text-indigo-400 flex-shrink-0 mt-0.5">•</span>
                <span>{children}</span>
              </li>
            ),
          strong: ({ children }) => <strong className="font-semibold text-slate-800">{children}</strong>,
          em: ({ children }) => <em className="italic text-slate-500">{children}</em>,
          code: ({ className, children }) => className?.startsWith('language-')
            ? <pre className="bg-slate-50 border border-slate-100 rounded-lg p-3 overflow-x-auto text-xs font-mono my-2">{children}</pre>
            : <code className="bg-slate-100 text-slate-700 px-1 py-0.5 rounded text-xs font-mono">{children}</code>,
          blockquote: ({ children }) => <blockquote className="border-l-2 border-indigo-200 pl-3 text-slate-500 italic my-2">{children}</blockquote>,
          a: ({ href, children }) => {
            // [n](#fn-n) 각주 링크
            if (href?.startsWith('#fn-') && onFootnoteClick) {
              const num = href.slice(4)
              return (
                <sup>
                  <a
                    href={href}
                    className="text-indigo-500 hover:text-indigo-700 text-[10px] font-semibold cursor-pointer no-underline"
                    onClick={e => { e.preventDefault(); onFootnoteClick(num) }}
                  >
                    [{children}]
                  </a>
                </sup>
              )
            }
            return <a href={href} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:underline">{children}</a>
          },
          hr: () => <hr className="border-slate-100 my-3" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

// ── 상대시간 ──
function relativeTime(isoStr) {
  if (!isoStr) return ''
  const diff = (Date.now() - new Date(isoStr + 'Z').getTime()) / 1000
  if (diff < 60) return '방금'
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`
  if (diff < 2592000) return `${Math.floor(diff / 86400)}d`
  if (diff < 31536000) return `${Math.floor(diff / 2592000)}mo`
  return `${Math.floor(diff / 31536000)}y`
}

// ── API 헬퍼 ──
async function fetchJSON(url, options = {}) {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function streamSSE(jobId, onEvent) {
  const es = new EventSource(`${API}/${jobId}/stream`)
  es.onmessage = (e) => { try { onEvent(JSON.parse(e.data)) } catch {} }
  es.onerror = () => es.close()
  return () => es.close()
}

async function fetchInternalContext(ticker) {
  try {
    const res = await fetch(`/api/stock/${ticker}/overview`)
    if (!res.ok) return ''
    const d = await res.json()
    return [
      `회사: ${d.name || ticker}`,
      `섹터: ${d.sector || 'N/A'} / 산업: ${d.industry || 'N/A'}`,
      `현재가: $${d.current_price || 'N/A'}`,
      `시가총액: $${(d.market_cap || 0).toLocaleString()}`,
      `52주 최고/최저: $${d['52w_high'] || 'N/A'} / $${d['52w_low'] || 'N/A'}`,
      `PER: ${d.pe_ratio || 'N/A'} / PBR: ${d.pb_ratio || 'N/A'}`,
      d.description ? `사업 요약: ${d.description.slice(0, 500)}` : '',
    ].filter(Boolean).join('\n')
  } catch { return '' }
}

// ── 세션 API ──
const sessionAPI = {
  list: (ticker) => fetchJSON(`${API}/sessions/${ticker}`),
  create: (ticker, title, mode) =>
    fetchJSON(`${API}/sessions`, { method: 'POST', body: JSON.stringify({ ticker, title, mode }) }),
  getMessages: (sid) => fetchJSON(`${API}/sessions/${sid}/messages`),
  saveMessage: (sid, role, content, metadata) =>
    fetchJSON(`${API}/sessions/${sid}/messages`, {
      method: 'POST', body: JSON.stringify({ role, content, metadata }),
    }),
  delete: (sid) => fetchJSON(`${API}/sessions/${sid}`, { method: 'DELETE' }),
}

// ── 히스토리 드롭다운 ──
function HistoryDropdown({ ticker, currentSessionId, onSelect, onNew }) {
  const [open, setOpen] = useState(false)
  const [sessions, setSessions] = useState([])
  const ref = useRef(null)

  const load = useCallback(async () => {
    try {
      const data = await sessionAPI.list(ticker)
      setSessions(data.sessions || [])
    } catch {}
  }, [ticker])

  useEffect(() => { if (open) load() }, [open, load])

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    const handler = () => { if (open) load() }
    window.addEventListener('research-session-updated', handler)
    return () => window.removeEventListener('research-session-updated', handler)
  }, [open, load])

  const handleDelete = async (e, sid) => {
    e.stopPropagation()
    await sessionAPI.delete(sid)
    load()
    if (sid === currentSessionId) onNew()
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(p => !p)}
        title="채팅 기록"
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
          open ? 'bg-slate-200 text-slate-700' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
        }`}
      >
        <History size={13} />
        기록
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-72 bg-white border border-slate-200 rounded-2xl shadow-xl shadow-slate-200/70 z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
            <p className="text-xs font-semibold text-slate-700">{ticker} 채팅 기록</p>
            <button
              onClick={() => { onNew(); setOpen(false) }}
              className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800 font-medium"
            >
              <Plus size={11} />새 채팅
            </button>
          </div>

          <div className="max-h-72 overflow-y-auto">
            {sessions.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-6">채팅 기록 없음</p>
            ) : (
              sessions.map(s => (
                <button
                  key={s.id}
                  onClick={() => { onSelect(s.id); setOpen(false) }}
                  className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors border-b border-slate-50 last:border-b-0 group ${
                    s.id === currentSessionId ? 'bg-indigo-50' : 'hover:bg-slate-50'
                  }`}
                >
                  <span className={`flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded ${
                    s.mode === 'deep' ? 'bg-indigo-100 text-indigo-600' : 'bg-slate-100 text-slate-500'
                  }`}>
                    {s.mode === 'deep' ? '심층' : '빠른'}
                  </span>
                  <span className="flex-1 text-[13px] text-slate-700 truncate font-medium">
                    {s.title}
                  </span>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="text-[11px] text-slate-400">{relativeTime(s.updated_at)}</span>
                    <button
                      onClick={(e) => handleDelete(e, s.id)}
                      className="opacity-0 group-hover:opacity-100 text-slate-300 hover:text-red-500 transition-all"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 진행 바 ──
function ProgressBar({ pct, message }) {
  return (
    <div className="space-y-1.5 py-1">
      <div className="flex justify-between text-xs text-slate-500">
        <span className="truncate pr-2">{message}</span>
        <span className="flex-shrink-0">{pct}%</span>
      </div>
      <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div className="h-full bg-indigo-500 rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ── 최종 보고서 ──
function ResearchReport({ result }) {
  const [open, setOpen] = useState({})
  const [highlighted, setHighlighted] = useState(null)
  const footnoteItemRefs = useRef({})
  const toggle = (i) => setOpen(p => ({ ...p, [i]: !p[i] }))

  const scrollToFn = useCallback((num) => {
    const el = footnoteItemRefs.current[num]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setHighlighted(num)
      setTimeout(() => setHighlighted(null), 2000)
    }
  }, [])

  // ref_number가 있는 출처만 번호순 정렬
  const numberedSources = (result.sources || [])
    .filter(s => s.ref_number != null)
    .sort((a, b) => a.ref_number - b.ref_number)

  const summary = parseSourcesFromText(result.summary)

  return (
    <div className="space-y-3 text-sm">
      {/* 핵심 요약 */}
      <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-4">
        <p className="text-xs font-semibold text-indigo-600 mb-2">핵심 요약</p>
        <MarkdownContent
          text={preprocessFootnotes(summary.clean)}
          onFootnoteClick={scrollToFn}
        />
        <SourceCards urls={summary.urls} />
      </div>

      {/* 핵심 발견 */}
      {result.key_findings?.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide">핵심 발견</p>
          {result.key_findings.map((f, i) => {
            const parsed = parseSourcesFromText(f.finding)
            return (
              <div key={i} className="p-3 bg-white border border-slate-100 rounded-lg">
                <div className="flex gap-2.5">
                  <span className={`flex-shrink-0 w-2 h-2 rounded-full mt-1.5 ${
                    f.confidence === 'high' ? 'bg-emerald-400' : f.confidence === 'medium' ? 'bg-amber-400' : 'bg-slate-300'
                  }`} />
                  <p className="text-slate-700 text-[13px] leading-snug">
                    {preprocessFootnotes(parsed.clean)}
                  </p>
                </div>
                {parsed.urls.length > 0 && (
                  <div className="ml-4.5 mt-1">
                    <SourceCards urls={parsed.urls} extra={f.sources || []} />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 섹션 */}
      {result.sections?.map((s, i) => {
        const parsed = parseSourcesFromText(s.content)
        return (
          <div key={i} className="border border-slate-100 rounded-xl overflow-hidden">
            <button onClick={() => toggle(i)}
              className="w-full flex items-center justify-between p-3.5 bg-white hover:bg-slate-50 transition-colors text-left">
              <span className="font-semibold text-slate-800 text-sm">{s.title}</span>
              {open[i] ? <ChevronUp size={13} className="text-slate-400" /> : <ChevronDown size={13} className="text-slate-400" />}
            </button>
            {open[i] && (
              <div className="px-4 pb-4 bg-white border-t border-slate-50">
                <div className="mt-3">
                  <MarkdownContent
                    text={preprocessFootnotes(parsed.clean)}
                    onFootnoteClick={scrollToFn}
                  />
                </div>
                <SourceCards urls={parsed.urls} extra={s.sources || []} />
              </div>
            )}
          </div>
        )
      })}

      {/* 타임라인 */}
      {result.timeline?.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide">타임라인</p>
          {result.timeline.map((t, i) => {
            const parsed = parseSourcesFromText(t.event)
            return (
              <div key={i} className="flex gap-3 text-[13px]">
                <span className="flex-shrink-0 text-indigo-500 font-mono text-xs w-24">{t.date}</span>
                <div>
                  <span className="text-slate-600">{preprocessFootnotes(parsed.clean)}</span>
                  {(parsed.urls.length > 0 || t.source) && (
                    <SourceCards urls={parsed.urls} extra={t.source ? [t.source] : []} />
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 출처 커버리지 */}
      {result.coverage && (result.coverage.checked?.length > 0 || result.coverage.unchecked?.length > 0) && (
        <CoverageSection coverage={result.coverage} />
      )}

      {/* 번호 각주 목록 */}
      {numberedSources.length > 0 && (
        <div className="pt-3 border-t border-slate-100">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-2">참고 출처</p>
          <ol className="space-y-1">
            {numberedSources.map(src => (
              <li
                key={src.ref_number}
                id={`fn-${src.ref_number}`}
                ref={el => { footnoteItemRefs.current[src.ref_number] = el }}
                className={`flex items-start gap-2 text-[11px] rounded-lg px-2 py-1 -mx-2 transition-colors duration-300 ${
                  highlighted === String(src.ref_number) ? 'bg-indigo-50' : ''
                }`}
              >
                <span className="flex-shrink-0 text-indigo-500 font-semibold w-6 text-right">
                  [{src.ref_number}]
                </span>
                <div className="flex-1 min-w-0">
                  <a
                    href={src.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-600 hover:text-indigo-600 break-all leading-snug"
                  >
                    {src.title || src.domain}
                  </a>
                  <span className="text-slate-400 ml-1 whitespace-nowrap">
                    ({src.domain}{src.credibility ? `, ${credibilityToTier(src.credibility)}` : ''})
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="text-[11px] text-slate-400 pt-1 border-t border-slate-100">
        검색 {result.metadata?.total_queries}회 · 출처 {result.metadata?.total_sources}개 ·
        {result.metadata?.elapsed_seconds?.toFixed(0)}초 · ${result.metadata?.estimated_cost_usd?.toFixed(3)}
      </div>
    </div>
  )
}

// ── 플랜 분리 헬퍼 (사전 검색 섹션 분리) ──
// SCOUT_PLAN_PROMPT 출력 형식: **사전 검색 분석** (bold) 또는 ## 사전 검색 (header)
function splitPlanSections(plan) {
  // 매칭: ** 또는 ## 형식의 사전 검색 섹션
  const scoutRe = /(\*\*사전\s*검색[^*]*\*\*[\s\S]*?)(?=\*\*조사\s*항목|\*\*리서치\s*계획|#{1,3}\s*조사|#{1,3}\s*리서치|$)/
  const m = plan.match(scoutRe)
  if (!m) return { scout: '', main: plan }
  const scoutStart = plan.indexOf(m[0])
  const before = plan.slice(0, scoutStart).trim()
  const after = plan.slice(scoutStart + m[0].length).trim()
  return { scout: m[0].trim(), main: (before + '\n\n' + after).trim() }
}

// ── 플랜 확인 버블 ──
function PlanBubble({ plan, onConfirm, onEdit, disabled }) {
  const [scoutOpen, setScoutOpen] = useState(false)
  const { scout, main } = splitPlanSections(plan)

  // 첫 줄(요약)과 나머지 분리
  const lines = main.split('\n')
  const firstNonEmpty = lines.find(l => l.trim() && !l.startsWith('#'))
  const summary = firstNonEmpty?.trim() || ''

  return (
    <div className="space-y-3">
      {/* 한 줄 요약 배지 */}
      {summary && (
        <div className="flex items-start gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-xl">
          <Edit3 size={12} className="text-amber-500 flex-shrink-0 mt-0.5" />
          <p className="text-[12px] text-amber-700 font-medium leading-snug">{summary}</p>
        </div>
      )}

      {/* 사전 검색 섹션 (접힘) */}
      {scout && (
        <div className="border border-slate-100 rounded-xl overflow-hidden">
          <button
            onClick={() => setScoutOpen(p => !p)}
            className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 hover:bg-slate-100 text-left transition-colors"
          >
            <span className="text-[11px] font-semibold text-slate-500">사전 검색 분석</span>
            {scoutOpen ? <ChevronUp size={12} className="text-slate-400" /> : <ChevronDown size={12} className="text-slate-400" />}
          </button>
          {scoutOpen && (
            <div className="px-4 py-3 border-t border-slate-100 bg-white">
              <MarkdownContent text={scout} />
            </div>
          )}
        </div>
      )}

      {/* 메인 계획 */}
      <div className="bg-white border border-amber-200 rounded-xl p-4">
        <p className="text-[11px] font-semibold text-amber-600 mb-2">리서치 계획 — 검토 후 실행하세요</p>
        <MarkdownContent text={main} />
      </div>

      <div className="flex gap-2">
        <button
          onClick={onConfirm}
          disabled={disabled}
          className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white rounded-lg text-xs font-semibold transition-colors"
        >
          <CheckCircle size={13} />최종 실행
        </button>
        <button
          onClick={onEdit}
          disabled={disabled}
          className="flex items-center gap-1.5 px-3 py-2 border border-slate-200 hover:bg-slate-50 disabled:opacity-40 text-slate-600 rounded-lg text-xs transition-colors"
        >
          <Edit3 size={13} />수정 요청
        </button>
      </div>
    </div>
  )
}

// ── 메시지 버블 ──
function Message({ msg, onConfirmPlan, onEditPlan, isRunning }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm leading-relaxed">
          {msg.content}
        </div>
      </div>
    )
  }
  if (msg.role === 'progress') {
    return <div className="px-1"><ProgressBar pct={msg.pct || 0} message={msg.content} /></div>
  }
  if (msg.role === 'report') {
    return <ResearchReport result={msg.result} />
  }
  if (msg.role === 'plan') {
    return <PlanBubble plan={msg.content} onConfirm={onConfirmPlan} onEdit={onEditPlan} disabled={isRunning} />
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[82%] bg-white border border-slate-100 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
        <MarkdownContent text={msg.content} />
      </div>
    </div>
  )
}

// ── 메인 컴포넌트 ──
export default function StockResearchChat({ ticker, onClose }) {
  const [deepMode, setDeepMode] = useState(true)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [sessionId, setSessionId] = useState(null)
  const [currentPlan, setCurrentPlan] = useState(null)
  const [internalContext, setInternalContext] = useState('')
  const [editingPlan, setEditingPlan] = useState(false)
  const bottomRef = useRef(null)
  const cleanupRef = useRef(null)
  const progressIdRef = useRef(null)

  useEffect(() => { fetchInternalContext(ticker).then(setInternalContext) }, [ticker])

  useEffect(() => {
    if (!sessionId) {
      setMessages([{
        role: 'assistant',
        content: deepMode
          ? `${ticker} 심층 리서치를 시작합니다.\n질문을 입력하면 리서치 계획을 먼저 보여드립니다.`
          : `${ticker} 빠른 답변 모드입니다.\nFinVision 보유 데이터로 즉시 답변합니다.`,
      }])
      setCurrentPlan(null)
    }
  }, [sessionId, deepMode, ticker])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])
  useEffect(() => () => cleanupRef.current?.(), [])

  const addMsg = (msg) => setMessages(prev => [...prev, msg])
  const updateLast = (update) =>
    setMessages(prev => { const a = [...prev]; a[a.length - 1] = { ...a[a.length - 1], ...update }; return a })
  const updateProgress = (pct, content) =>
    setMessages(prev => prev.map(m => m._pid === progressIdRef.current ? { ...m, pct, content } : m))

  const ensureSession = async (title, mode) => {
    if (sessionId) return sessionId
    const data = await sessionAPI.create(ticker, title, mode)
    setSessionId(data.session_id)
    window.dispatchEvent(new Event('research-session-updated'))
    return data.session_id
  }
  const saveMsg = async (sid, role, content, metadata) => {
    try { await sessionAPI.saveMessage(sid, role, content, metadata) } catch {}
  }

  const handleDeepQuery = async (query) => {
    const sid = await ensureSession(query.slice(0, 30) || ticker, 'deep')
    addMsg({ role: 'user', content: query })
    await saveMsg(sid, 'user', query)
    setIsRunning(true)
    addMsg({ role: 'assistant', content: '사전 검색 중... (실제 데이터 수집 후 계획 수립)' })
    try {
      const data = await fetchJSON(`${API}/stock/${ticker}/plan`, {
        method: 'POST',
        body: JSON.stringify({ query, internal_context: internalContext }),
      })
      setCurrentPlan(data.plan)
      updateLast({ role: 'plan', content: data.plan })
      await saveMsg(sid, 'plan', data.plan)
    } catch (e) {
      updateLast({ role: 'assistant', content: `계획 생성 실패: ${e.message}` })
    } finally { setIsRunning(false) }
  }

  const handlePlanEdit = async (userMsg) => {
    if (!currentPlan || !userMsg.trim()) return
    const sid = sessionId || await ensureSession(ticker, 'deep')
    addMsg({ role: 'user', content: userMsg })
    await saveMsg(sid, 'user', userMsg)
    setIsRunning(true)
    addMsg({ role: 'assistant', content: '계획 수정 중...' })
    try {
      const data = await fetchJSON(`${API}/plan/refine`, {
        method: 'POST',
        body: JSON.stringify({ current_plan: currentPlan, user_message: userMsg }),
      })
      setCurrentPlan(data.plan)
      updateLast({ role: 'plan', content: data.plan })
      await saveMsg(sid, 'plan', data.plan)
    } catch (e) {
      updateLast({ role: 'assistant', content: `수정 실패: ${e.message}` })
    } finally { setIsRunning(false); setEditingPlan(false) }
  }

  const handleExecute = async () => {
    if (!currentPlan) return
    const sid = sessionId || await ensureSession(ticker, 'deep')
    setIsRunning(true)
    const pid = Date.now()
    progressIdRef.current = pid
    addMsg({ role: 'progress', _pid: pid, pct: 0, content: '리서치 시작 중...' })
    try {
      const userMsgs = messages.filter(m => m.role === 'user')
      const originalQuery = userMsgs[0]?.content || ticker
      const job = await fetchJSON(`${API}/stock/${ticker}/execute`, {
        method: 'POST',
        body: JSON.stringify({ query: originalQuery, plan: currentPlan, internal_context: internalContext }),
      })
      cleanupRef.current = streamSSE(job.job_id, async (event) => {
        if (event.stage === 'heartbeat') return
        updateProgress(event.progress_pct, event.message)
        if (event.stage === 'done') {
          const status = await fetchJSON(`${API}/${job.job_id}/status`)
          if (status.result) {
            setMessages(prev => prev.map(m => m._pid === pid ? { role: 'report', result: status.result } : m))
            await saveMsg(sid, 'report', JSON.stringify(status.result))
          }
          setIsRunning(false); setCurrentPlan(null)
          window.dispatchEvent(new Event('research-session-updated'))
        } else if (event.stage === 'error') {
          setMessages(prev => prev.map(m => m._pid === pid ? { role: 'assistant', content: `오류: ${event.message}` } : m))
          setIsRunning(false)
        }
      })
    } catch (e) {
      setMessages(prev => prev.filter(m => m._pid !== pid))
      addMsg({ role: 'assistant', content: `실행 실패: ${e.message}` })
      setIsRunning(false)
    }
  }

  const handleSimpleChat = async (question) => {
    const sid = await ensureSession(question.slice(0, 30) || ticker, 'simple')
    addMsg({ role: 'user', content: question })
    await saveMsg(sid, 'user', question)
    setIsRunning(true)
    addMsg({ role: 'assistant', content: '...' })
    const history = messages.filter(m => ['user', 'assistant'].includes(m.role)).slice(-6)
      .map(m => ({ role: m.role, content: m.content }))
    try {
      const data = await fetchJSON(`${API}/stock/${ticker}/chat`, {
        method: 'POST',
        body: JSON.stringify({ question, internal_context: internalContext, history }),
      })
      updateLast({ role: 'assistant', content: data.answer })
      await saveMsg(sid, 'assistant', data.answer)
    } catch (e) {
      updateLast({ role: 'assistant', content: `오류: ${e.message}` })
    } finally {
      setIsRunning(false)
      window.dispatchEvent(new Event('research-session-updated'))
    }
  }

  const handleSelectSession = async (sid) => {
    setSessionId(sid)
    try {
      const data = await sessionAPI.getMessages(sid)
      const msgs = (data.messages || []).map(m => {
        if (m.role === 'report') {
          try { return { role: 'report', result: JSON.parse(m.content) } }
          catch { return { role: 'assistant', content: m.content } }
        }
        return { role: m.role, content: m.content }
      })
      setMessages(msgs.length > 0 ? msgs : [{ role: 'assistant', content: `${ticker} 이전 채팅입니다.` }])
      setCurrentPlan(null)
    } catch {}
  }

  const handleNew = () => {
    setSessionId(null); setCurrentPlan(null); setEditingPlan(false)
    cleanupRef.current?.(); setIsRunning(false)
  }

  const send = () => {
    const q = input.trim()
    if (!q || isRunning) return
    setInput('')
    if (editingPlan || currentPlan) handlePlanEdit(q)
    else if (!deepMode) handleSimpleChat(q)
    else handleDeepQuery(q)
  }

  const SUGGESTIONS = deepMode
    ? [`${ticker} 투자 가치 종합 분석`, `${ticker} 실적 및 향후 전망`, `${ticker} 주요 리스크`]
    : [`${ticker} 현재 PER은?`, `최근 어닝 서프라이즈`, `가이던스 요약`]

  return (
    <div className="flex flex-col h-full bg-white border-l border-slate-200">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center flex-shrink-0">
            <BookOpen size={15} className="text-white" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-bold text-slate-800">{ticker} 리서치</p>
            <p className="text-[11px] text-slate-400">
              {deepMode ? '⚡ 심층 리서치 모드' : '💬 빠른 답변 모드'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <HistoryDropdown
            ticker={ticker}
            currentSessionId={sessionId}
            onSelect={handleSelectSession}
            onNew={handleNew}
          />

          <button
            onClick={handleNew}
            title="새 채팅"
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 hover:bg-slate-200 transition-all"
          >
            <Plus size={13} />
          </button>

          <button
            onClick={() => { setDeepMode(p => !p); handleNew() }}
            title={deepMode ? '빠른 모드로 전환' : '심층 리서치로 전환'}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              deepMode
                ? 'bg-indigo-100 text-indigo-700 hover:bg-indigo-200'
                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
            }`}
          >
            {deepMode ? <Zap size={12} /> : <ZapOff size={12} />}
            {deepMode ? '심층' : '빠른'}
          </button>

          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 p-1 ml-1">
            <X size={16} />
          </button>
        </div>
      </div>

      {/* 메시지 영역 */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {messages.map((msg, i) => (
          <Message
            key={i}
            msg={msg}
            onConfirmPlan={handleExecute}
            onEditPlan={() => { setEditingPlan(true); setInput('') }}
            isRunning={isRunning}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {editingPlan && (
        <div className="px-5 py-2 bg-amber-50 border-t border-amber-100 text-xs text-amber-700 flex items-center justify-between flex-shrink-0">
          <span>✏️ 계획 수정 내용을 입력하세요</span>
          <button onClick={() => setEditingPlan(false)} className="text-amber-500 hover:text-amber-700">취소</button>
        </div>
      )}

      {messages.length <= 1 && (
        <div className="px-5 pb-3 flex flex-wrap gap-2 flex-shrink-0">
          {SUGGESTIONS.map((s, i) => (
            <button key={i} onClick={() => setInput(s)}
              className="text-[12px] px-3 py-1.5 rounded-full border border-slate-200 text-slate-500 hover:border-indigo-300 hover:text-indigo-600 transition-all">
              {s}
            </button>
          ))}
        </div>
      )}

      {/* 입력창 */}
      <div className="flex-shrink-0 px-4 py-3.5 border-t border-slate-100">
        <div className="flex gap-2.5 items-end">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder={
              isRunning ? '처리 중...' :
              editingPlan ? '수정 내용을 입력하세요...' :
              currentPlan ? '추가 수정 요청 또는 최종 실행 버튼을 누르세요' :
              deepMode ? `${ticker} 심층 분석 질문을 입력하세요...` :
              `${ticker}에 대해 빠르게 질문하세요...`
            }
            disabled={isRunning}
            rows={1}
            className="flex-1 resize-none bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:bg-white focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 transition-all disabled:opacity-50 max-h-28 overflow-y-auto"
          />
          <button
            onClick={send}
            disabled={!input.trim() || isRunning}
            className="flex-shrink-0 w-10 h-10 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white rounded-xl flex items-center justify-center transition-colors"
          >
            {isRunning ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
