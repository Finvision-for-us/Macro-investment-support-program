import { useState, useRef, useEffect, useCallback, Fragment } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Send, X, ChevronDown, ChevronUp, ExternalLink,
  Loader2, BookOpen, Zap, ZapOff, Plus, Trash2,
  CheckCircle, Edit3, History, AlertTriangle, Circle
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

// ── 인라인 검증 태그 → 배지·툴팁 ([unverified]/[추론] 날것 텍스트 제거) ──
// 백엔드가 태그 위치를 정정(방어선 6)해 배지가 단어를 쪼개지 않는다.
const TAG_META = {
  미검증: {
    label: '미검증',
    tip: '수집 원문에서 이 수치·사실을 확인하지 못함 (거짓이라는 뜻은 아님)',
    cls: 'bg-amber-100 text-amber-700 border-amber-200',
  },
  추론: {
    label: '추론',
    tip: '수집 자료 기반 해석·전망 (원문에 적힌 사실이 아님)',
    cls: 'bg-sky-100 text-sky-700 border-sky-200',
  },
  검증필요: {
    label: '추가검증',
    tip: '자동생성·루머 등 낮은 신뢰 출처 — 교차확인 필요',
    cls: 'bg-orange-100 text-orange-700 border-orange-200',
  },
  출처미상: {
    label: '출처미상',
    tip: '출처 URL이 확인되지 않은 주장',
    cls: 'bg-slate-100 text-slate-500 border-slate-200',
  },
}

function TagBadge({ kind }) {
  const m = TAG_META[kind]
  if (!m) return null
  return (
    <span
      title={m.tip}
      className={`inline-flex items-center px-1 rounded text-[9px] font-semibold border align-middle cursor-help mx-0.5 ${m.cls}`}
    >
      {m.label}
    </span>
  )
}

const _BADGE_SRC = '\\[\\[?unverified\\]?\\]|\\[추론\\]|\\[추가\\s*검증\\s*필요\\]|\\[source:\\s*미확인\\]'

function _tagKind(tok) {
  if (/unverified/i.test(tok)) return '미검증'
  if (/추론/.test(tok)) return '추론'
  if (/추가/.test(tok)) return '검증필요'
  return '출처미상'
}

// 문자열 → [텍스트 | 배지 | 각주 sup] 노드 배열.
// withFootnotes=true면 [n]도 클릭 가능한 각주로(마크다운 밖 raw 경로용).
function tokenizeInline(str, withFootnotes = false, onFootnoteClick) {
  if (typeof str !== 'string' || !str) return str
  const src = withFootnotes ? `(${_BADGE_SRC})|\\[(\\d{1,3})\\]` : `(${_BADGE_SRC})`
  const re = new RegExp(src, 'gi')
  const out = []
  let last = 0
  let m
  let key = 0
  while ((m = re.exec(str)) !== null) {
    if (m.index > last) out.push(str.slice(last, m.index))
    if (m[2] !== undefined) {
      const num = m[2]
      out.push(
        <sup key={`f${key++}`}>
          <a
            href={`#fn-${num}`}
            className="text-indigo-500 hover:text-indigo-700 text-[10px] font-semibold cursor-pointer no-underline"
            onClick={e => { e.preventDefault(); onFootnoteClick?.(num) }}
          >[{num}]</a>
        </sup>
      )
    } else {
      out.push(<TagBadge key={`b${key++}`} kind={_tagKind(m[1] || m[0])} />)
    }
    last = m.index + m[0].length
  }
  if (last < str.length) out.push(str.slice(last))
  return out.length ? out : str
}

// ReactMarkdown children(문자열/배열/요소)에 배지 적용.
// (각주 [n]은 markdown이 링크로 변환→a 렌더러가 처리하므로 여기선 배지만.)
function withBadges(children) {
  if (typeof children === 'string') return tokenizeInline(children, false)
  if (Array.isArray(children)) {
    return children.map((c, i) =>
      typeof c === 'string'
        ? <Fragment key={i}>{tokenizeInline(c, false)}</Fragment>
        : c
    )
  }
  return children
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

// ── 미검증·불확실 항목 섹션 ──
function UnverifiedGapsSection({ gaps }) {
  const [open, setOpen] = useState(false)
  const items = (gaps || []).filter(Boolean)
  if (items.length === 0) return null

  return (
    <div className="border border-amber-100 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(p => !p)}
        className="w-full flex items-center justify-between px-3.5 py-2.5 bg-amber-50 hover:bg-amber-100 text-left transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <AlertTriangle size={13} className="text-amber-600 flex-shrink-0" />
          <span className="text-[11px] font-semibold text-amber-800">미검증·불확실 항목</span>
          <span className="text-[10px] px-1.5 py-0.5 bg-white/70 text-amber-700 rounded-full font-medium">
            {items.length}
          </span>
        </div>
        {open ? <ChevronUp size={12} className="text-amber-600" /> : <ChevronDown size={12} className="text-amber-600" />}
      </button>

      {open && (
        <div className="px-4 py-3 border-t border-amber-100 bg-white">
          <ul className="space-y-1.5">
            {items.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-[12px] text-slate-600 leading-snug">
                <span className="text-amber-500 flex-shrink-0 mt-0.5">!</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── 수치 교차검증 섹션 (pro-rata·환율·gross↔net·세율, 결정론적) ──
function CrossValidationSection({ items }) {
  const [open, setOpen] = useState(false)
  const list = (items || []).filter(Boolean)
  if (list.length === 0) return null

  const parse = (s) => {
    const m = s.match(/^\s*\[([^\]]+)\]\s*(.*)$/)
    return m ? { tag: m[1], body: m[2] } : { tag: '', body: s }
  }
  // 분류는 '태그'만 보고 판단한다(본문의 "35% 이상" 같은 표현 오분류 방지).
  // warn을 먼저 평가한다: 태그에 '정합'과 '상충'이 공존하면 주의(warn)가 이기도록.
  const classify = (s) => {
    const tag = parse(s).tag || s
    if (/상충|재확인|이상/.test(tag)) return 'warn'
    if (/정합|일치/.test(tag)) return 'ok'   // 'N개 출처 일치'·'원장 일치'(SEC XBRL) 포함
    return 'weak'
  }
  const tone = {
    ok:   { pill: 'bg-emerald-50 text-emerald-700 border-emerald-200', dot: 'text-emerald-500', mark: '✓' },
    warn: { pill: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'text-amber-500', mark: '!' },
    weak: { pill: 'bg-slate-100 text-slate-500 border-slate-200', dot: 'text-slate-400', mark: '·' },
  }
  const okN = list.filter(s => classify(s) === 'ok').length
  const warnN = list.filter(s => classify(s) === 'warn').length

  return (
    <div className="border border-indigo-100 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(p => !p)}
        className="w-full flex items-center justify-between px-3.5 py-2.5 bg-indigo-50 hover:bg-indigo-100 text-left transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <CheckCircle size={13} className="text-indigo-600 flex-shrink-0" />
          <span className="text-[11px] font-semibold text-indigo-800">수치 교차검증</span>
          {okN > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 bg-emerald-100 text-emerald-700 rounded-full font-medium">정합 {okN}</span>
          )}
          {warnN > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded-full font-medium">주의 {warnN}</span>
          )}
        </div>
        {open ? <ChevronUp size={12} className="text-indigo-600" /> : <ChevronDown size={12} className="text-indigo-600" />}
      </button>

      {open && (
        <div className="px-4 py-3 border-t border-indigo-100 bg-white">
          <ul className="space-y-2">
            {list.map((item, i) => {
              const kind = classify(item)
              const { tag, body } = parse(item)
              const t = tone[kind]
              return (
                <li key={i} className="flex items-start gap-2 text-[12px] text-slate-600 leading-snug">
                  <span className={`flex-shrink-0 mt-0.5 ${t.dot}`}>{t.mark}</span>
                  <span className="min-w-0">
                    {tag && (
                      <span className={`inline-block text-[10px] px-1.5 py-0.5 mr-1.5 rounded border font-medium align-middle ${t.pill}`}>{tag}</span>
                    )}
                    <span className="align-middle">{body}</span>
                  </span>
                </li>
              )
            })}
          </ul>
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
          p: ({ children }) => <p className="text-[13px] text-slate-600 leading-relaxed mb-2 last:mb-0">{withBadges(children)}</p>,
          ul: ({ children }) => <ul className="space-y-1 mb-2">{children}</ul>,
          ol: ({ children }) => <ol className="space-y-1 mb-2 list-decimal list-inside">{children}</ol>,
          li: ({ ordered, children }) => ordered
            ? <li className="text-[13px] text-slate-600 leading-snug">{withBadges(children)}</li>
            : (
              <li className="text-[13px] text-slate-600 leading-snug flex gap-1.5">
                <span className="text-indigo-400 flex-shrink-0 mt-0.5">•</span>
                <span>{withBadges(children)}</span>
              </li>
            ),
          strong: ({ children }) => <strong className="font-semibold text-slate-800">{withBadges(children)}</strong>,
          em: ({ children }) => <em className="italic text-slate-500">{withBadges(children)}</em>,
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

// ── ① 계획·실행 통합 체크리스트 ──
// 승인된 계획이 실행되며 단계별로 체크된다(타 딥리서치 AI 방식). 파이프라인
// stage → 4개 실행 단계로 매핑, 진행은 단조(뒤로 안 감 — 검색↔평가 루프 흡수).
// draft 도착 시 이 카드는 초안 리포트로 교체되므로 '보고서 작성'까지만 다룬다.
const EXEC_PHASES = [
  { key: 'plan', label: '리서치 계획 확정' },
  { key: 'collect', label: '정보 수집 · 검색과 원문 추출' },
  { key: 'evaluate', label: '충분성 평가 · 추가 확장' },
  { key: 'write', label: '보고서 작성' },
]
const STAGE_TO_IDX = {
  planning: 0, searching: 1, extracting: 1,
  reflecting: 2, synthesizing: 3, draft: 3, done: 3,
}

function ExecutionChecklist({ msg }) {
  const [planOpen, setPlanOpen] = useState(false)
  const cur = msg._phaseIdx ?? 0
  const planMain = msg.plan ? splitPlanSections(msg.plan).main : ''
  const planSummary = planMain.split('\n').find(l => l.trim() && !l.startsWith('#'))?.trim() || ''

  return (
    <div className="border border-indigo-100 rounded-xl overflow-hidden bg-white">
      <div className="px-4 pt-3 pb-3">
        <div className="flex justify-between items-center mb-2">
          <span className="text-[11px] font-semibold text-indigo-600">리서치 진행 중</span>
          <span className="text-[11px] text-slate-400">{msg.pct || 0}%</span>
        </div>
        <div className="h-1 bg-slate-100 rounded-full overflow-hidden mb-3">
          <div className="h-full bg-indigo-500 rounded-full transition-all duration-500" style={{ width: `${msg.pct || 0}%` }} />
        </div>
        <ul className="space-y-2">
          {EXEC_PHASES.map((p, i) => {
            const done = i < cur
            const active = i === cur
            return (
              <li key={p.key} className="flex items-start gap-2">
                {done
                  ? <CheckCircle size={14} className="text-emerald-500 flex-shrink-0 mt-0.5" />
                  : active
                    ? <Loader2 size={14} className="text-indigo-500 animate-spin flex-shrink-0 mt-0.5" />
                    : <Circle size={14} className="text-slate-300 flex-shrink-0 mt-0.5" />}
                <div className="min-w-0">
                  <span className={`text-[12px] leading-snug ${done ? 'text-slate-500' : active ? 'text-slate-800 font-medium' : 'text-slate-400'}`}>{p.label}</span>
                  {active && msg.content && (
                    <span className="block text-[11px] text-slate-400 truncate">{msg.content}</span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </div>
      {planSummary && (
        <div className="border-t border-slate-100">
          <button
            onClick={() => setPlanOpen(p => !p)}
            className="w-full flex items-center justify-between px-4 py-2 text-left hover:bg-slate-50 transition-colors"
          >
            <span className="text-[11px] text-slate-400 truncate pr-2">계획: {planSummary}</span>
            {planOpen ? <ChevronUp size={12} className="text-slate-400 flex-shrink-0" /> : <ChevronDown size={12} className="text-slate-400 flex-shrink-0" />}
          </button>
          {planOpen && (
            <div className="px-4 pb-3">
              <MarkdownContent text={planMain} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const CLAIM_STATUS = {
  verified: ['검증', 'bg-emerald-100 text-emerald-700'],
  single_source: ['단일출처', 'bg-amber-100 text-amber-700'],
  partially_verified: ['부분검증', 'bg-sky-100 text-sky-700'],
  contradicted: ['상충', 'bg-red-100 text-red-700'],
  unverified: ['미검증', 'bg-slate-100 text-slate-600'],
}

function ClaimLedgerSection({ claims }) {
  const [open, setOpen] = useState(false)
  const list = claims || []
  if (!list.length) return null
  const eligible = list.filter(c => c.executive_summary_eligible).length
  return (
    <div className="border border-slate-100 rounded-xl overflow-hidden">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 hover:bg-slate-100">
        <span className="text-[11px] font-semibold text-slate-700">
          주장 검증 원장 · 요약 통과 {eligible}/{list.length}
        </span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && <div className="p-3 space-y-2 bg-white">
        {list.map(c => {
          const state = CLAIM_STATUS[c.verification_status] || CLAIM_STATUS.unverified
          return <div key={c.claim_id} className="border border-slate-100 rounded-lg p-3">
            <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${state[1]}`}>{state[0]}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600">{c.claim_type}</span>
              {c.executive_summary_eligible && <span className="text-[10px] text-emerald-600">요약 사용 가능</span>}
            </div>
            <p className="text-[12px] text-slate-700 leading-snug">{tokenizeInline(c.claim_text, true)}</p>
            {c.evidence_excerpt && <p className="mt-1.5 text-[10px] text-slate-400 line-clamp-3">근거: {c.evidence_excerpt}</p>}
            {c.counter_evidence?.length > 0 &&
              <ul className="mt-1.5 text-[10px] text-red-600 space-y-1">
                {c.counter_evidence.map((e, i) => <li key={i}>반대 근거: {e}</li>)}
              </ul>}
            <SourceCards urls={c.source_ids || []} />
          </div>
        })}
      </div>}
    </div>
  )
}

function CalculationLedgerSection({ calculations }) {
  const [open, setOpen] = useState(false)
  const list = calculations || []
  if (!list.length) return null
  const invalid = list.filter(c => c.validation_status !== 'valid').length
  return (
    <div className="border border-violet-100 rounded-xl overflow-hidden">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3.5 py-2.5 bg-violet-50 hover:bg-violet-100">
        <span className="text-[11px] font-semibold text-violet-800">계산 검증 원장 · 주의 {invalid}/{list.length}</span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && <div className="p-3 space-y-2 bg-white">
        {list.map(c => <div key={c.calculation_id} className="border border-slate-100 rounded-lg p-3">
          <div className="flex gap-2 items-center">
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              c.validation_status === 'valid' ? 'bg-emerald-100 text-emerald-700' :
              c.validation_status === 'invalid' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
            }`}>{c.validation_status}</span>
            <span className="text-[11px] font-semibold text-slate-700">{c.description}</span>
          </div>
          <p className="mt-1.5 text-[11px] text-slate-600 font-mono">{c.formula}</p>
          {c.recomputed_value != null && <p className="mt-1 text-[10px] text-violet-700">
            코드 재계산: {c.recomputed_value}
            {c.recomputation_delta != null ? ` · 보고값 차이 ${c.recomputation_delta}` : ''}
          </p>}
          {c.validation_errors?.length > 0 &&
            <p className="mt-1 text-[10px] text-red-600">{c.validation_errors.join(' · ')}</p>}
          {c.assumptions?.length > 0 &&
            <p className="mt-1 text-[10px] text-slate-400">가정: {c.assumptions.join(' · ')}</p>}
        </div>)}
      </div>}
    </div>
  )
}

function SearchDiagnostics({ attempts }) {
  const [open, setOpen] = useState(false)
  const list = attempts || []
  if (!list.length) return null
  const failed = list.filter(a => a.status !== 'success' && a.status !== 'no_results').length
  return (
    <div className="border border-slate-100 rounded-xl overflow-hidden">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3.5 py-2.5 bg-slate-50 hover:bg-slate-100">
        <span className="text-[11px] font-semibold text-slate-600">검색 진단 · 실패/미실행 {failed}/{list.length}</span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && <div className="p-3 space-y-1 bg-white max-h-64 overflow-y-auto">
        {list.map((a, i) => <div key={`${a.source}-${i}`} className="text-[10px] text-slate-500 flex gap-2">
          <span className="font-semibold w-20 flex-shrink-0">{a.source}</span>
          <span className={a.status === 'success' ? 'text-emerald-600' : a.status === 'no_results' ? 'text-slate-400' : 'text-amber-600'}>{a.status}</span>
          <span className="truncate">{a.query}</span>
        </div>)}
      </div>}
    </div>
  )
}

function ScenarioAnalysisSection({ analysis }) {
  if (!analysis) return null
  const order = { bear: 0, base: 1, bull: 2 }
  const tone = {
    bear: 'border-red-100 bg-red-50 text-red-700',
    base: 'border-slate-200 bg-slate-50 text-slate-700',
    bull: 'border-emerald-100 bg-emerald-50 text-emerald-700',
  }
  const cases = [...(analysis.cases || [])].sort((a, b) => order[a.name] - order[b.name])
  return (
    <div className="border border-slate-100 rounded-xl p-3 bg-white">
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-xs font-semibold text-slate-700">Bull / Base / Bear 시나리오</p>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
          analysis.validation_status === 'valid'
            ? 'bg-emerald-100 text-emerald-700'
            : 'bg-red-100 text-red-700'
        }`}>{analysis.validation_status}</span>
      </div>
      {analysis.validation_errors?.length > 0 &&
        <p className="text-[10px] text-red-600 mb-2">
          결과 사용 차단: {analysis.validation_errors.join(' · ')}
        </p>}
      <div className="grid grid-cols-1 gap-2">
        {cases.map(c => <div key={c.name} className={`rounded-lg border p-3 ${tone[c.name] || tone.base}`}>
          <div className="flex justify-between items-center mb-1.5">
            <span className="text-[11px] font-bold uppercase">{c.name}</span>
            <span className="text-[11px] font-semibold">{(c.probability * 100).toFixed(0)}%</span>
          </div>
          {c.outputs?.map((o, i) =>
            <p key={i} className="text-[12px] font-semibold">
              {o.metric_name}: {o.value} {o.unit}
              <span className="font-normal opacity-70"> · {o.scope} · {o.period}</span>
            </p>)}
          <p className="mt-1.5 text-[10px] opacity-80">가정: {(c.assumptions || []).join(' · ')}</p>
          <p className="mt-1 text-[10px] opacity-80">무효화: {(c.invalidation_triggers || []).join(' · ')}</p>
          <SourceCards urls={c.evidence_source_ids || []} />
        </div>)}
      </div>
    </div>
  )
}

// ── 최종 보고서 ──
function ResearchReport({ result, isDraft = false }) {
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

  const summary = parseSourcesFromText(
    !isDraft && result.safe_executive_summary
      ? result.safe_executive_summary
      : result.summary
  )

  return (
    <div className="space-y-3 text-sm">
      {isDraft && (
        <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-[12px] text-amber-700">
          <Loader2 size={13} className="animate-spin flex-shrink-0" />
          <span><span className="font-semibold">초안</span>입니다 · 심사 중 (검증·교차확인·수치 재대조 진행) — 곧 심사본으로 교체됩니다</span>
        </div>
      )}
      {/* 핵심 요약 */}
      <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-4">
        <p className="text-xs font-semibold text-indigo-600 mb-2">
          {!isDraft && result.safe_executive_summary ? '검증된 핵심 요약' : '핵심 요약'}
        </p>
        <MarkdownContent
          text={preprocessFootnotes(summary.clean)}
          onFootnoteClick={scrollToFn}
        />
        <SourceCards urls={summary.urls} />
      </div>

      <ScenarioAnalysisSection analysis={result.scenario_analysis} />
      <ClaimLedgerSection claims={result.claim_ledger} />
      <CalculationLedgerSection calculations={result.calculation_ledger} />
      <SearchDiagnostics attempts={result.search_attempts} />

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
                    {tokenizeInline(parsed.clean, true, scrollToFn)}
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
                  <span className="text-slate-600">{tokenizeInline(parsed.clean, true, scrollToFn)}</span>
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

      {/* 수치 교차검증 (pro-rata·환율·gross↔net·세율) */}
      <CrossValidationSection items={result.cross_validation} />

      {/* 미검증·불확실 항목 */}
      <UnverifiedGapsSection gaps={result.unverified_gaps} />

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
                  {(src.publisher || src.published_at || src.document_type || src.reporting_period || src.source_section) && (
                    <p className="text-[10px] text-slate-400 mt-0.5">
                      {[
                        src.publisher,
                        src.published_at,
                        src.document_type,
                        src.reporting_period,
                        src.source_section,
                      ].filter(Boolean).join(' · ')}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {!isDraft && (
        <div className="text-[11px] text-slate-400 pt-1 border-t border-slate-100">
          검색 {result.metadata?.total_queries}회 · 출처 {result.metadata?.total_sources}개 ·
          {result.metadata?.elapsed_seconds?.toFixed(0)}초 · ${result.metadata?.estimated_cost_usd?.toFixed(3)}
        </div>
      )}
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
    return <ExecutionChecklist msg={msg} />
  }
  if (msg.role === 'report') {
    return <ResearchReport result={msg.result} isDraft={msg._draft} />
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
  const updateProgress = (pct, content, stage) =>
    setMessages(prev => prev.map(m => {
      if (m._pid !== progressIdRef.current) return m
      // 실행 단계는 단조 증가(검색↔평가 루프에도 체크가 뒤로 가지 않게)
      const idx = STAGE_TO_IDX[stage]
      const phaseIdx = idx == null ? (m._phaseIdx ?? 0) : Math.max(m._phaseIdx ?? 0, idx)
      return { ...m, pct, content, stage: stage ?? m.stage, _phaseIdx: phaseIdx }
    }))

  // 세션 생성 실패는 치명적이지 않다(저장만 못 할 뿐) — null 반환하고 채팅은 계속.
  // 과거엔 여기서 throw되면 사용자 메시지가 추가되기 전에 핸들러가 죽어
  // '프롬프트 증발' 버그가 됐다.
  const ensureSession = async (title, mode) => {
    if (sessionId) return sessionId
    try {
      const data = await sessionAPI.create(ticker, title, mode)
      setSessionId(data.session_id)
      window.dispatchEvent(new Event('research-session-updated'))
      return data.session_id
    } catch {
      return null
    }
  }
  const saveMsg = async (sid, role, content, metadata) => {
    if (!sid) return
    try { await sessionAPI.saveMessage(sid, role, content, metadata) } catch {}
  }

  const handleDeepQuery = async (query) => {
    addMsg({ role: 'user', content: query }) // 사용자 메시지 즉시 표시(세션 실패와 무관)
    const sid = await ensureSession(query.slice(0, 30) || ticker, 'deep')
    await saveMsg(sid, 'user', query)
    setIsRunning(true)
    addMsg({ role: 'assistant', content: '딥 플래닝 시작 — 다라운드 정찰 + 심사...' })
    // 계획 생성 소요는 라운드 포화 시점에 따라 20초~수 분으로 가변 —
    // 백엔드 진행상황을 2초 간격 폴링해 현재 단계를 표시

    const pid = crypto.randomUUID()
    const progressTimer = setInterval(async () => {
      try {
        const p = await fetchJSON(`${API}/plan-progress/${pid}`)
        if (p.message) {
          updateLast({ role: 'assistant', content: `🔎 ${p.message}` })
        }
      } catch {} // 진행 표시는 부가 기능 — 실패해도 계획 생성엔 영향 없음
    }, 2000)
    try {
      const data = await fetchJSON(`${API}/stock/${ticker}/plan`, {
        method: 'POST',
        body: JSON.stringify({ query, internal_context: internalContext, progress_id: pid }),
      })
      clearInterval(progressTimer)
      setCurrentPlan(data.plan)
      updateLast({ role: 'plan', content: data.plan })
      await saveMsg(sid, 'plan', data.plan)
    } catch (e) {
      updateLast({ role: 'assistant', content: `계획 생성 실패: ${e.message}` })
    } finally {
      clearInterval(progressTimer)
      setIsRunning(false)
    }
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
    // ① 계획 버블을 실행 체크리스트로 '제자리 교체'해 계획→실행을 한 카드로 통합.
    // (계획 원문은 카드 안 접이식으로 보존)
    const execMsg = {
      role: 'progress', _pid: pid, pct: 0, stage: 'planning', _phaseIdx: 0,
      content: '리서치 시작 중...', plan: currentPlan,
    }
    setMessages(prev => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'plan') { next[i] = execMsg; return next }
      }
      next.push(execMsg)
      return next
    })
    try {
      const userMsgs = messages.filter(m => m.role === 'user')
      const originalQuery = userMsgs[0]?.content || ticker
      const job = await fetchJSON(`${API}/stock/${ticker}/execute`, {
        method: 'POST',
        body: JSON.stringify({ query: originalQuery, plan: currentPlan, internal_context: internalContext }),
      })
      cleanupRef.current = streamSSE(job.job_id, async (event) => {
        if (event.stage === 'heartbeat') return
        updateProgress(event.progress_pct, event.message, event.stage)
        if (event.stage === 'draft') {
          // ② 초안 즉시 표시 — 심사본이 'done'에서 _pid로 교체한다(isRunning 유지)
          const draft = event.data?.draft_report
          if (draft) {
            setMessages(prev => prev.map(m =>
              m._pid === pid ? { role: 'report', result: draft, _pid: pid, _draft: true } : m))
          }
          return
        }
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
    addMsg({ role: 'user', content: question }) // 사용자 메시지 즉시 표시(세션 실패와 무관)
    const sid = await ensureSession(question.slice(0, 30) || ticker, 'simple')
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
