import { useState, useEffect, useRef, useMemo } from 'react'
import { BarChart3, TrendingUp, AlertCircle, ChevronDown, ChevronRight, DollarSign, Activity, Calendar, Target, ArrowUpRight, ArrowDownRight, Shield, Info, Zap, PieChart, HelpCircle, Brain, MessageSquare, Hash, Sparkles } from 'lucide-react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine, Cell
} from 'recharts'
import { stockAPI } from '../../api/index'

/* ── 색상 ── */
const COLORS = {
  beat: '#10b981', meet: '#6b7280', miss: '#ef4444',
  beatBg: '#ecfdf5', missBg: '#fef2f2', meetBg: '#f9fafb',
  line: '#6366f1', estimateStroke: '#94a3b8',
  revenue: '#8b5cf6', margin: '#f59e0b',
}

/* ── 숫자 포매팅 ── */
function pct(v) { return v != null ? `${v > 0 ? '+' : ''}${v.toFixed(2)}%` : '-' }
function epsF(v) { return v != null ? `$${v.toFixed(2)}` : '-' }
function revFmt(v) {
  if (v == null) return '-'
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toLocaleString()}`
}
function growthFmt(v) { return v != null ? `${(v * 100).toFixed(1)}%` : '-' }
function round2(v) { return Math.round(v * 100) / 100 }

/* ── 카테고리 배지 ── */
function CategoryBadge({ category }) {
  const styles = {
    Beat: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    Meet: 'bg-gray-100 text-gray-600 border-gray-200',
    Miss: 'bg-red-100 text-red-700 border-red-200',
    Unknown: 'bg-slate-100 text-slate-500 border-slate-200',
  }
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${styles[category] || styles.Unknown}`}>
      {category}
    </span>
  )
}

/* ── 주가 변동 셀 (히트맵 스타일) ── */
function ChangeCell({ value }) {
  if (value == null) return <td className="px-2 py-1.5 text-center text-xs text-slate-300">-</td>
  const intensity = Math.min(Math.abs(value) / 8, 1)
  const isPositive = value > 0
  const bg = isPositive
    ? `rgba(16, 185, 129, ${intensity * 0.25})`
    : `rgba(239, 68, 68, ${intensity * 0.25})`
  const color = isPositive ? '#059669' : '#dc2626'
  return (
    <td className="px-2 py-1.5 text-center text-xs font-mono font-medium" style={{ backgroundColor: bg, color }}>
      {value > 0 ? '+' : ''}{value.toFixed(2)}%
    </td>
  )
}

/* ── 커스텀 툴팁 ── */
function TimelineTooltip({ active, payload, chartType }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  const showEps = !chartType || chartType === 'eps' || chartType === 'multi'
  const showRev = (chartType === 'revenue' || chartType === 'multi') && d.revenueGrowthYoY != null
  const showMargin = (chartType === 'margin' || chartType === 'multi') && d.marginChange != null
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-xl p-3 text-xs min-w-[200px]">
      <div className="flex items-center justify-between mb-1.5">
        <span className="font-bold text-slate-800">{d.quarter}</span>
        <CategoryBadge category={d.category} />
      </div>
      <div className="space-y-0.5 text-slate-600">
        {showEps && (
          <>
            <div className="flex justify-between">
              <span>실제 EPS</span>
              <span className="font-mono font-bold text-slate-800">{epsF(d.epsActual)}</span>
            </div>
            <div className="flex justify-between">
              <span>예상 EPS {d.verified ? '' : '(미검증)'}</span>
              <span className={`font-mono ${d.verified ? 'text-slate-500' : 'text-slate-300'}`}>{epsF(d.epsEstimateRaw || d.epsEstimate)}</span>
            </div>
            {d.surprisePct != null && (
              <div className="flex justify-between">
                <span>서프라이즈</span>
                <span className="font-mono font-bold" style={{ color: d.surprisePct > 0 ? '#059669' : d.surprisePct < 0 ? '#dc2626' : '#6b7280' }}>
                  {pct(d.surprisePct)}
                </span>
              </div>
            )}
          </>
        )}
        {showRev && (
          <div className={`flex justify-between ${showEps ? 'border-t border-slate-100 pt-1 mt-1' : ''}`}>
            <span>매출 성장률 (YoY)</span>
            <span className="font-mono font-bold" style={{ color: d.revenueGrowthYoY > 0 ? '#8b5cf6' : '#f59e0b' }}>
              {d.revenueGrowthYoY > 0 ? '+' : ''}{d.revenueGrowthYoY}%
            </span>
          </div>
        )}
        {showMargin && (
          <div className="flex justify-between border-t border-slate-100 pt-1 mt-1">
            <span>마진 변화 (QoQ)</span>
            <span className="font-mono font-bold" style={{ color: d.marginChange > 0 ? '#f59e0b' : '#dc2626' }}>
              {d.marginChange > 0 ? '+' : ''}{d.marginChange.toFixed(2)}%p
            </span>
          </div>
        )}
        {d.reaction != null && (
          <div className="flex justify-between border-t border-slate-100 pt-1 mt-1">
            <span>주가 반응</span>
            <span className="font-mono font-bold" style={{ color: d.reaction > 0 ? '#059669' : '#dc2626' }}>
              {pct(d.reaction)}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── 게이지 바 컴포넌트 ── */
function GaugeBar({ p10, p90, avg, min = -10, max = 10 }) {
  const range = max - min
  const toPercent = v => Math.max(0, Math.min(100, ((v - min) / range) * 100))
  const zeroPos = toPercent(0)
  const p10Pos = toPercent(p10)
  const p90Pos = toPercent(p90)
  const avgPos = toPercent(avg)

  return (
    <div className="relative w-full h-8 mt-1">
      <div className="absolute inset-x-0 top-3 h-2 bg-slate-100 rounded-full" />
      <div className="absolute top-2 h-4 w-px bg-slate-300" style={{ left: `${zeroPos}%` }} />
      <div className="absolute top-7 text-[8px] text-slate-400" style={{ left: `${zeroPos}%`, transform: 'translateX(-50%)' }}>0%</div>
      <div
        className="absolute top-3 h-2 rounded-full"
        style={{
          left: `${p10Pos}%`,
          width: `${p90Pos - p10Pos}%`,
          background: avg > 0
            ? 'linear-gradient(90deg, rgba(16,185,129,0.2), rgba(16,185,129,0.5))'
            : 'linear-gradient(90deg, rgba(239,68,68,0.5), rgba(239,68,68,0.2))',
        }}
      />
      <div
        className="absolute top-1.5 w-3 h-3 rounded-full border-2 shadow-sm"
        style={{
          left: `${avgPos}%`,
          transform: 'translateX(-50%)',
          backgroundColor: avg > 0 ? '#10b981' : '#ef4444',
          borderColor: 'white',
        }}
      />
      <div className="absolute -top-3 text-[9px] font-bold" style={{
        left: `${avgPos}%`,
        transform: 'translateX(-50%)',
        color: avg > 0 ? '#059669' : '#dc2626',
      }}>
        {pct(avg)}
      </div>
    </div>
  )
}

/* ── 접히는 섹션 ── */
function CollapsibleSection({ title, icon: Icon, children, defaultOpen = false, badge }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-slate-200 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-2.5 bg-slate-50 hover:bg-slate-100 transition-colors text-left"
      >
        {open ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}
        {Icon && <Icon size={14} className="text-slate-500" />}
        <span className="text-xs font-bold text-slate-700">{title}</span>
        {badge && <span className="text-[10px] bg-slate-200 text-slate-600 px-1.5 py-0.5 rounded-full ml-auto">{badge}</span>}
      </button>
      {open && <div className="p-4 border-t border-slate-200">{children}</div>}
    </div>
  )
}

/* ── 요인 신뢰도 색상 ── */
function getStrengthColor(rSq) {
  if (rSq >= 0.3) return { text: '#059669', bg: '#ecfdf5', border: '#a7f3d0', label: '강함' }
  if (rSq >= 0.15) return { text: '#d97706', bg: '#fffbeb', border: '#fde68a', label: '보통' }
  if (rSq >= 0.05) return { text: '#9333ea', bg: '#faf5ff', border: '#e9d5ff', label: '약함' }
  return { text: '#dc2626', bg: '#fef2f2', border: '#fecaca', label: '미미' }
}

/* ═══════════════════════════════════════════════════════════
   메인 컴포넌트
   ═══════════════════════════════════════════════════════════ */
export default function EarningsSimulator({ ticker }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(true)
  const [simEps, setSimEps] = useState(null)
  const [simRev, setSimRev] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const [sortKey, setSortKey] = useState('date')
  const [sortDir, setSortDir] = useState('desc')
  const [showExplanation, setShowExplanation] = useState(false)
  const [guidanceData, setGuidanceData] = useState(null)
  const [guidanceLoading, setGuidanceLoading] = useState(false)
  const [guidanceError, setGuidanceError] = useState(null)
  const guidanceAbortRef = useRef(null)   // 가이던스 중복 요청 방지

  useEffect(() => {
    if (!ticker) return

    // 이전 가이던스 요청 취소 (중복 Gemini 호출 방지)
    if (guidanceAbortRef.current) {
      guidanceAbortRef.current.abort()
    }

    setLoading(true)
    setError(null)
    setData(null)
    setGuidanceData(null)
    setGuidanceError(null)
    stockAPI.getEarnings(ticker)
      .then(r => {
        setData(r.data)
        if (r.data?.current_consensus_eps) {
          setSimEps(r.data.current_consensus_eps)
        } else if (r.data?.history?.length > 0) {
          const lastActual = r.data.history[0]?.eps_actual
          if (lastActual) setSimEps(lastActual)
        }
        if (r.data?.revenue_estimates?.current_quarter?.rev_avg) {
          setSimRev(parseFloat((r.data.revenue_estimates.current_quarter.rev_avg / 1e9).toFixed(1)))
        }
      })
      .catch(e => setError(e.response?.data?.detail || '데이터를 불러올 수 없습니다'))
      .finally(() => setLoading(false))

    // 가이던스 AI 분석 (별도 로딩 — AbortController로 중복 방지)
    const abortController = new AbortController()
    guidanceAbortRef.current = abortController
    setGuidanceLoading(true)
    stockAPI.getGuidance(ticker, 20)
      .then(r => {
        if (abortController.signal.aborted) return
        if (r.data?.guidance?.length > 0) {
          setGuidanceData(r.data)
          setGuidanceError(null)
        }
      })
      .catch(e => {
        if (abortController.signal.aborted) return
        // 503 = Gemini 미설정, 404 = CIK 없음 → 조용히 무시
        if (e.response?.status !== 503 && e.response?.status !== 404) {
          setGuidanceError(e.response?.data?.detail || '가이던스 분석 실패')
        }
      })
      .finally(() => {
        if (!abortController.signal.aborted) setGuidanceLoading(false)
      })

    return () => { abortController.abort() }  // cleanup: 언마운트 시 취소
  }, [ticker])

  // 타임라인 차트 데이터
  const timelineData = useMemo(() => {
    if (!data?.history) return []
    const sorted = [...data.history].sort((a, b) => (a.date || '').localeCompare(b.date || ''))

    const revMap = {}
    if (data.revenue_history) {
      data.revenue_history.forEach(r => {
        if (r.period_end) revMap[r.period_end] = r
      })
    }

    // Build margin data for QoQ
    const sortedRev = data.revenue_history ? [...data.revenue_history].sort((a, b) => (a.period_end || '').localeCompare(b.period_end || '')) : []
    const marginMap = {}
    for (let i = 1; i < sortedRev.length; i++) {
      const curr = sortedRev[i], prev = sortedRev[i - 1]
      if (curr.revenue_actual && curr.earnings_actual && prev.revenue_actual && prev.earnings_actual && Math.abs(curr.revenue_actual) > 0 && Math.abs(prev.revenue_actual) > 0) {
        const currMargin = curr.earnings_actual / curr.revenue_actual
        const prevMargin = prev.earnings_actual / prev.revenue_actual
        marginMap[curr.period_end] = Math.round((currMargin - prevMargin) * 10000) / 100 // pp
      }
    }

    const mapped = sorted.map((h) => {
      let shortLabel = h.period || h.date
      if (h.period) {
        const match = h.period.match(/Q(\d)\s*(\d{4})/)
        if (match) shortLabel = `Q${match[1]}'${match[2].slice(2)}`
      }

      const rev = revMap[h.period_end] || Object.values(revMap).find(r => {
        if (!r.period_end || !h.period_end) return false
        const d1 = new Date(r.period_end), d2 = new Date(h.period_end)
        return Math.abs(d1 - d2) <= 15 * 86400000
      })

      let revenueGrowthYoY = null
      if (rev?.revenue_actual) {
        const prevYearRev = data.revenue_history?.find(r => {
          if (!r.period_end || !h.period_end) return false
          const d1 = new Date(r.period_end), d2 = new Date(h.period_end)
          const daysDiff = (d2 - d1) / 86400000
          return daysDiff > 340 && daysDiff < 400
        })
        if (prevYearRev?.revenue_actual) {
          revenueGrowthYoY = ((rev.revenue_actual - prevYearRev.revenue_actual) / Math.abs(prevYearRev.revenue_actual)) * 100
        }
      }

      // Margin change
      const marginChange = marginMap[h.period_end] ?? null

      return {
        quarter: h.period || h.date,
        shortLabel,
        date: h.date,
        epsActual: h.eps_actual,
        epsEstimate: h.estimate_verified ? h.eps_estimate : null,
        epsEstimateRaw: h.eps_estimate,
        surprisePct: h.surprise_pct,
        reaction: h.reaction_1d_change,
        category: h.category || 'Unknown',
        verified: h.estimate_verified,
        revenueActual: rev?.revenue_actual || null,
        revenueGrowthYoY: revenueGrowthYoY != null ? Math.round(revenueGrowthYoY * 10) / 10 : null,
        marginChange,
      }
    })
    if (!showAll && mapped.length > 20) {
      return mapped.slice(-20)
    }
    return mapped
  }, [data, showAll])

  // 정렬된 히스토리
  const sortedHistory = useMemo(() => {
    if (!data?.history) return []
    return [...data.history].sort((a, b) => {
      const va = a[sortKey], vb = b[sortKey]
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      return sortDir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1)
    })
  }, [data, sortKey, sortDir])

  // 시뮬레이션 결과 (시간 가중치 + 가이던스 factor weight 반영)
  const simResult = useMemo(() => {
    if (!data?.history || simEps == null) return null
    const lastEstimate = data.current_consensus_eps || data.history[0]?.eps_estimate
    if (!lastEstimate) return null

    const impliedSurprise = ((simEps - lastEstimate) / Math.abs(lastEstimate)) * 100
    const range = 3

    const similar = data.history.filter(h =>
      h.estimate_verified &&
      h.surprise_pct != null && h.reaction_1d_change != null &&
      Math.abs(h.surprise_pct - impliedSurprise) <= range
    )

    if (similar.length === 0) return { impliedSurprise: round2(impliedSurprise), similar: [], count: 0 }

    const now = new Date()
    const weighted = similar.map(s => {
      const date = new Date(s.date || s.period_end)
      const yearsAgo = Math.max(0, (now - date) / (365.25 * 24 * 60 * 60 * 1000))
      const weight = 1 + 2 * Math.exp(-yearsAgo / 5)
      return { ...s, weight }
    })

    const totalWeight = weighted.reduce((s, w) => s + w.weight, 0)
    let weightedAvg = weighted.reduce((s, w) => s + w.reaction_1d_change * w.weight, 0) / totalWeight
    let weightedUpProb = weighted.reduce((s, w) => s + (w.reaction_1d_change > 0 ? w.weight : 0), 0) / totalWeight * 100

    // ── 가이던스 감성 factor 보정 ──
    // 가이던스 데이터가 있고, guidance_sentiment factor의 R²가 유의미하면
    // 최근 가이던스 sentiment 경향을 예측에 반영
    const _profile = data?.stock_profile || {}
    const _allFactors = _profile.factors || []
    const guidanceFactor = _allFactors.find(f => f.id === 'guidance_sentiment')
    if (guidanceFactor && guidanceFactor.r_squared >= 0.05 && guidanceData?.guidance?.length > 0) {
      // 최근 3분기 평균 sentiment
      const recentGuidance = guidanceData.guidance.slice(0, 3)
      const avgSentiment = recentGuidance.reduce((s, g) => s + (g.sentiment_score || 50), 0) / recentGuidance.length
      const sentimentSignal = (avgSentiment - 50) / 50  // -1 ~ +1 범위

      // factor weight 기반 보정 (과거 데이터에서 계산된 weight 그대로 사용)
      const guidanceWeight = guidanceFactor.weight || 0
      const avgVol = _profile.avg_volatility || 3
      const guidanceAdjustment = sentimentSignal * guidanceWeight * avgVol * 2

      // 가중 평균에 가이던스 보정 적용
      weightedAvg = weightedAvg + guidanceAdjustment
      // 상승 확률도 보정 (sentiment 긍정이면 상승 확률 상향)
      weightedUpProb = Math.max(0, Math.min(100, weightedUpProb + sentimentSignal * guidanceWeight * 30))
    }

    const reactions = similar.map(s => s.reaction_1d_change).sort((a, b) => a - b)
    const p10 = reactions[Math.floor(reactions.length * 0.1)] || reactions[0]
    const p90 = reactions[Math.ceil(reactions.length * 0.9) - 1] || reactions[reactions.length - 1]

    return {
      impliedSurprise: round2(impliedSurprise),
      estimate: lastEstimate,
      count: similar.length,
      avgReaction: round2(weightedAvg),
      p10: round2(p10),
      p90: round2(p90),
      upProbability: round2(weightedUpProb),
      similar: similar.slice(0, 5),
      guidanceApplied: !!(guidanceFactor && guidanceFactor.r_squared >= 0.05 && guidanceData?.guidance?.length > 0),
    }
  }, [data, simEps, guidanceData])

  function handleSort(key) {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  // 로딩
  if (loading) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={16} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-800">실적 발표 시뮬레이터</h3>
        </div>
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-6 w-6 border-2 border-indigo-500 border-t-transparent" />
          <span className="ml-3 text-sm text-slate-500">어닝 데이터 로딩 중...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={16} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-800">실적 발표 시뮬레이터</h3>
        </div>
        <div className="flex items-center gap-2 text-amber-600 text-sm py-4">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      </div>
    )
  }

  if (!data || data.total_count === 0) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={16} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-800">실적 발표 시뮬레이터</h3>
        </div>
        <p className="text-sm text-slate-400 py-4">이 종목의 실적 발표 데이터가 없습니다.</p>
      </div>
    )
  }

  const stats = data.statistics || {}
  const beatCount = stats.Beat?.count || 0
  const missCount = stats.Miss?.count || 0
  const classifiedCount = data.classified_count || 0
  const profile = data.stock_profile || {}
  const topFactors = profile.top_factors || []
  const allFactors = profile.factors || []
  const guidanceAnalysis = profile.guidance_analysis || {}
  const simConfig = profile.simulation_config || {}
  const factorReasoning = profile.factor_reasoning || {}
  const chartType = simConfig.chart_type || 'eps'

  // Determine primary factor for display
  const primaryFactor = topFactors[0] || null
  const primaryFactorName = primaryFactor?.name || 'EPS 서프라이즈'

  // Factor icons
  const factorIcons = {
    eps_surprise: Target,
    revenue_growth: TrendingUp,
    revenue_acceleration: Zap,
    margin_trend: PieChart,
    guidance_sentiment: Brain,
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm space-y-4">
      {/* ═══ 헤더 ═══ */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 size={16} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-800">실적 발표 시뮬레이터</h3>
          <span className="text-[10px] bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full border border-indigo-100">
            총 {data.total_count}분기 · 검증 {data.verified_count || 0}건
          </span>
        </div>
        <button onClick={() => setExpanded(!expanded)} className="text-slate-400 hover:text-slate-600">
          <ChevronDown size={18} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {expanded && (
        <>
          {/* ═══ 종목 특성 프로필 (핵심 변경: 종목별 맞춤) ═══ */}
          {profile.verified_sample_size >= 5 && (
            <div className="border border-slate-200 rounded-xl overflow-hidden">
              {/* 프로필 헤더 */}
              <div className="bg-gradient-to-r from-slate-800 to-indigo-900 px-4 py-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Shield size={14} className="text-indigo-300" />
                    <h4 className="text-xs font-bold text-white">{ticker} 종목 특성 분석</h4>
                    {profile.stock_type && (
                      <span className="text-[10px] bg-white/15 text-indigo-200 px-2 py-0.5 rounded-full">
                        {profile.stock_type}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] text-slate-400">신뢰도</span>
                    <span className="text-[11px] font-bold px-2 py-0.5 rounded-full" style={{
                      color: profile.predictability_score >= 60 ? '#6ee7b7'
                        : profile.predictability_score >= 35 ? '#fde68a' : '#fca5a5',
                      backgroundColor: 'rgba(255,255,255,0.1)',
                    }}>
                      {profile.predictability_score}/100
                    </span>
                  </div>
                </div>
                {profile.sector_description && (
                  <p className="text-[10px] text-slate-400 mt-1">{profile.sector_description}</p>
                )}
              </div>

              {/* 핵심 요인 리스트 (종목별로 다름) */}
              <div className="p-4 space-y-3">
                <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">이 종목의 주가를 움직이는 핵심 요인</div>

                {topFactors.map((f, i) => {
                  const reasoning = factorReasoning[f.id] || {}
                  const strength = getStrengthColor(f.r_squared)
                  const FactorIcon = factorIcons[f.id] || Activity
                  const barWidth = Math.min(f.r_squared * 200, 100) // Scale R² to bar width

                  return (
                    <div key={f.id} className="border border-slate-100 rounded-lg p-3 hover:border-slate-200 transition-colors">
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold text-white"
                          style={{ backgroundColor: i === 0 ? '#4f46e5' : i === 1 ? '#7c3aed' : '#9333ea' }}>
                          {i + 1}
                        </div>
                        <FactorIcon size={13} className="text-slate-500" />
                        <span className="text-[12px] font-bold text-slate-800">{f.name}</span>
                        <span className="text-[9px] px-1.5 py-0.5 rounded-full border font-semibold"
                          style={{ color: strength.text, backgroundColor: strength.bg, borderColor: strength.border }}>
                          {strength.label}
                        </span>
                        <span className="text-[9px] text-slate-400 ml-auto font-mono">
                          R²={f.r_squared?.toFixed(3)} · 비중 {Math.round((f.weight || 0) * 100)}%
                        </span>
                      </div>

                      {/* R² 바 */}
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div className="h-1.5 rounded-full transition-all duration-500" style={{
                            width: `${barWidth}%`,
                            backgroundColor: strength.text,
                          }} />
                        </div>
                      </div>

                      {/* 이유 설명 */}
                      <div className="text-[10px] text-slate-500 leading-relaxed space-y-0.5">
                        {reasoning.sector_note && (
                          <p className="text-slate-600"><span className="font-semibold text-slate-700">섹터 관점:</span> {reasoning.sector_note}</p>
                        )}
                        {reasoning.data_note && (
                          <p><span className="font-semibold text-slate-700">데이터 근거:</span> {reasoning.data_note}</p>
                        )}
                      </div>
                    </div>
                  )
                })}

                {/* 가이던스 영향 (별도 요인으로 표시) */}
                {guidanceAnalysis.guidance_influence_score >= 25 && (
                  <div className="border border-amber-200 rounded-lg p-3 bg-amber-50/50">
                    <div className="flex items-center gap-2 mb-1.5">
                      <div className="flex items-center justify-center w-5 h-5 rounded-full bg-amber-500 text-[10px] font-bold text-white">!</div>
                      <Activity size={13} className="text-amber-600" />
                      <span className="text-[12px] font-bold text-amber-800">가이던스 / 비실적 요인</span>
                      <span className="text-[9px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full border border-amber-200 font-semibold">
                        영향도 {guidanceAnalysis.guidance_influence_score}/100
                      </span>
                    </div>
                    <div className="text-[10px] text-amber-700 space-y-0.5">
                      <p>EPS Beat 후에도 주가 하락: <span className="font-bold">{guidanceAnalysis.beat_but_down_pct}%</span> ({guidanceAnalysis.beat_but_down_count}건)</p>
                      {guidanceAnalysis.miss_but_up_pct > 0 && (
                        <p>EPS Miss 후에도 주가 상승: <span className="font-bold">{guidanceAnalysis.miss_but_up_pct}%</span> ({guidanceAnalysis.miss_but_up_count}건)</p>
                      )}
                      <p className="text-amber-600 mt-1">
                        실적 자체보다 CEO 발언, 가이던스, 시장 기대치가 주가를 크게 좌우하는 종목입니다.
                      </p>
                    </div>
                  </div>
                )}

                {/* 분석 근거 (접기/펴기) */}
                <button
                  onClick={() => setShowExplanation(!showExplanation)}
                  className="flex items-center gap-1.5 text-[10px] text-indigo-500 hover:text-indigo-700 transition-colors"
                >
                  <HelpCircle size={12} />
                  <span className="font-semibold">왜 이런 분석이 나왔나요?</span>
                  {showExplanation ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                </button>
                {showExplanation && profile.analysis_explanation && (
                  <div className="bg-slate-50 rounded-lg p-3 border border-slate-100">
                    <pre className="text-[10px] text-slate-600 leading-relaxed whitespace-pre-wrap font-sans">
                      {profile.analysis_explanation}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ═══ 핵심 지표 요약 바 ═══ */}
          <div className="flex flex-wrap gap-2">
            <div className="flex items-center gap-1.5 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5">
              <ArrowUpRight size={12} className="text-emerald-600" />
              <span className="text-[11px] font-bold text-emerald-700">Beat {data.beat_rate}%</span>
              <span className="text-[10px] text-emerald-500">({beatCount}건)</span>
            </div>
            {stats.Beat?.avg_reaction_1d != null && (
              <div className="flex items-center gap-1.5 bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-1.5">
                <TrendingUp size={12} className="text-indigo-600" />
                <span className="text-[11px] text-indigo-700">Beat 평균 <span className="font-bold">{pct(stats.Beat.avg_reaction_1d)}</span></span>
              </div>
            )}
            {missCount > 0 && stats.Miss?.avg_reaction_1d != null && (
              <div className="flex items-center gap-1.5 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">
                <ArrowDownRight size={12} className="text-red-600" />
                <span className="text-[11px] text-red-700">Miss 평균 <span className="font-bold">{pct(stats.Miss.avg_reaction_1d)}</span></span>
              </div>
            )}
            {profile.avg_volatility != null && (
              <div className="flex items-center gap-1.5 bg-violet-50 border border-violet-200 rounded-lg px-3 py-1.5">
                <Activity size={12} className="text-violet-600" />
                <span className="text-[11px] text-violet-700">발표일 변동 <span className="font-bold">±{profile.avg_volatility}%</span></span>
              </div>
            )}
            {data.next_earnings && (
              <div className="flex items-center gap-1.5 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 ml-auto">
                <Calendar size={12} className="text-amber-600" />
                <span className="text-[11px] text-amber-700">다음 발표 <span className="font-bold">{data.next_earnings}</span></span>
              </div>
            )}
          </div>

          {/* ═══ 동적 타임라인 차트 (종목별 다름) ═══ */}
          {timelineData.length > 0 && (() => {
            const hasRevData = timelineData.some(d => d.revenueGrowthYoY != null)
            const hasMarginData = timelineData.some(d => d.marginChange != null)
            const showEps = chartType === 'eps' || chartType === 'multi'
            const showRevenue = (chartType === 'revenue' || chartType === 'multi') && hasRevData
            const showMargin = chartType === 'margin' || (chartType === 'multi' && hasMarginData && !showRevenue)

            // Dynamic chart title based on primary factor
            const chartTitle = primaryFactor
              ? `${primaryFactorName} & 주가 반응 타임라인`
              : 'EPS 실적 & 주가 반응 타임라인'

            return (
            <div className="border border-slate-200 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <h4 className="text-xs font-bold text-slate-700">{chartTitle}</h4>
                  {primaryFactor && (
                    <span className="text-[9px] bg-indigo-100 text-indigo-600 px-1.5 py-0.5 rounded-full">
                      {chartType === 'eps' ? 'EPS 기반' : chartType === 'revenue' ? '매출 기반' : chartType === 'margin' ? '마진 기반' : '복합 요인'}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {data.history?.length > 20 && (
                    <button
                      onClick={() => setShowAll(!showAll)}
                      className="text-[10px] px-2 py-0.5 rounded-full border border-indigo-200 text-indigo-600 hover:bg-indigo-50 transition-colors"
                    >
                      {showAll ? `최근 20분기` : `전체 ${data.history.length}분기`}
                    </button>
                  )}
                  <div className="flex items-center gap-3 text-[9px] text-slate-500 flex-wrap">
                    {showEps && <>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-emerald-500 inline-block" /> Beat</span>
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500 inline-block" /> Miss</span>
                    </>}
                    {showRevenue && (
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-violet-500 inline-block" /> 매출성장</span>
                    )}
                    {showMargin && (
                      <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-amber-500 inline-block" /> 마진변화</span>
                    )}
                    <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-indigo-500 inline-block" /> 주가반응</span>
                  </div>
                </div>
              </div>

              <ResponsiveContainer width="100%" height={280}>
                <ComposedChart data={timelineData} margin={{ top: 5, right: 50, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis
                    dataKey="shortLabel"
                    tick={{ fontSize: 9, fill: '#94a3b8' }}
                    interval={timelineData.length > 30 ? 3 : timelineData.length > 15 ? 1 : 0}
                    angle={timelineData.length > 20 ? -45 : 0}
                    textAnchor={timelineData.length > 20 ? 'end' : 'middle'}
                    height={timelineData.length > 20 ? 45 : 30}
                  />

                  {/* Primary Y-axis based on chart type */}
                  {showEps && (
                    <YAxis yAxisId="eps" tick={{ fontSize: 9, fill: '#94a3b8' }} tickFormatter={v => `$${v.toFixed(1)}`} width={45} />
                  )}
                  {showRevenue && (
                    <YAxis yAxisId="revGrowth" tick={{ fontSize: 9, fill: '#8b5cf6' }}
                      tickFormatter={v => `${v > 0 ? '+' : ''}${v}%`} width={45}
                      hide={showEps}
                    />
                  )}
                  {showMargin && !showEps && !showRevenue && (
                    <YAxis yAxisId="marginAxis" tick={{ fontSize: 9, fill: '#f59e0b' }}
                      tickFormatter={v => `${v > 0 ? '+' : ''}${v}%p`} width={45}
                    />
                  )}

                  <YAxis yAxisId="reaction" orientation="right" tick={{ fontSize: 9, fill: '#818cf8' }}
                    tickFormatter={v => `${v > 0 ? '+' : ''}${v}%`} width={45}
                  />
                  <Tooltip content={<TimelineTooltip chartType={chartType} />} />
                  <ReferenceLine yAxisId="reaction" y={0} stroke="#cbd5e1" strokeDasharray="3 3" />

                  {/* EPS bars */}
                  {showEps && (
                    <Bar yAxisId="eps" dataKey="epsEstimate" fill="transparent" stroke="#94a3b8" strokeWidth={1} strokeDasharray="3 3" barSize={16} radius={[2, 2, 0, 0]} />
                  )}
                  {showEps && (
                    <Bar yAxisId="eps" dataKey="epsActual" barSize={12} radius={[2, 2, 0, 0]}>
                      {timelineData.map((entry, idx) => (
                        <Cell key={idx}
                          fill={entry.category === 'Beat' ? COLORS.beat : entry.category === 'Miss' ? COLORS.miss : entry.category === 'Meet' ? COLORS.meet : '#cbd5e1'}
                          fillOpacity={0.85}
                        />
                      ))}
                    </Bar>
                  )}

                  {/* Revenue growth bars */}
                  {showRevenue && (
                    <Bar yAxisId={showEps ? 'revGrowth' : 'revGrowth'} dataKey="revenueGrowthYoY" barSize={showEps ? 8 : 14} radius={[2, 2, 0, 0]}>
                      {timelineData.map((entry, idx) => (
                        <Cell key={idx}
                          fill={entry.revenueGrowthYoY > 0 ? '#8b5cf6' : '#f59e0b'}
                          fillOpacity={0.7}
                        />
                      ))}
                    </Bar>
                  )}

                  {/* Margin change bars */}
                  {showMargin && !showEps && !showRevenue && (
                    <Bar yAxisId="marginAxis" dataKey="marginChange" barSize={14} radius={[2, 2, 0, 0]}>
                      {timelineData.map((entry, idx) => (
                        <Cell key={idx}
                          fill={entry.marginChange > 0 ? '#f59e0b' : '#ef4444'}
                          fillOpacity={0.7}
                        />
                      ))}
                    </Bar>
                  )}

                  {/* Price reaction line */}
                  <Line yAxisId="reaction" type="monotone" dataKey="reaction"
                    stroke={COLORS.line} strokeWidth={2}
                    dot={{ r: 3, fill: COLORS.line, strokeWidth: 0 }}
                    activeDot={{ r: 5, strokeWidth: 2, stroke: '#fff' }}
                    connectNulls
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            )
          })()}

          {/* ═══ 시뮬레이션 패널 (종목별 동적) ═══ */}
          <div className="border border-indigo-200 rounded-xl overflow-hidden">
            {/* 시뮬레이션 헤더 */}
            <div className="bg-gradient-to-r from-indigo-600 to-violet-600 px-4 py-2.5 flex items-center justify-between">
              <h4 className="text-xs font-bold text-white flex items-center gap-1.5">
                <Target size={14} />
                다음 실적 시뮬레이션
                {data.next_earnings && (
                  <span className="font-normal text-indigo-200 ml-1">({data.next_earnings})</span>
                )}
              </h4>
              {primaryFactor && (
                <span className="text-[9px] bg-white/15 text-indigo-200 px-2 py-0.5 rounded-full">
                  {primaryFactorName} 기반 분석
                </span>
              )}
            </div>

            <div className="p-4 bg-gradient-to-br from-indigo-50/50 to-violet-50/30">
              {/* 시뮬레이션 기반 요인 안내 */}
              {simConfig.input_factors && (
                <div className="flex items-start gap-2 mb-3 bg-white/60 rounded-lg px-3 py-2 border border-indigo-100">
                  <Info size={11} className="text-indigo-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[10px] text-slate-600">
                    <span className="font-semibold text-indigo-700">이 종목의 시뮬레이션 기반:</span>{' '}
                    {simConfig.input_factors.map(f => {
                      const names = { eps_surprise: 'EPS 서프라이즈', revenue_growth: '매출 성장률', revenue_acceleration: '매출 가속도', margin_trend: '수익성 변화', guidance_sentiment: '가이던스 감성(AI)' }
                      return names[f] || f
                    }).join(', ')}
                    {guidanceAnalysis.guidance_influence_score >= 30 && (
                      <span className="text-amber-600 font-semibold"> + 가이던스 영향 주의</span>
                    )}
                  </div>
                </div>
              )}

              {(() => {
                const inputFactors = simConfig.input_factors || ['eps_surprise']
                const showEpsInput = inputFactors.includes('eps_surprise') || !simConfig.input_factors
                const showRevInput = inputFactors.includes('revenue_growth') || (data.revenue_estimates?.current_quarter?.rev_avg && !simConfig.input_factors)

                return (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {/* 좌측: 입력 */}
                    <div className="space-y-3">
                      {/* EPS 입력 */}
                      {showEpsInput && (
                        <div>
                          <div className="flex items-center gap-1.5 mb-1">
                            <Target size={10} className="text-indigo-500" />
                            <label className="text-[10px] text-slate-600 font-semibold">
                              예상 실제 EPS
                              {primaryFactor?.id !== 'eps_surprise' && (
                                <span className="text-slate-400 font-normal ml-1">(보조 지표)</span>
                              )}
                            </label>
                          </div>
                          <div className="flex items-center gap-2 text-[10px] text-slate-500 mb-1 bg-white/60 rounded px-2 py-1">
                            <span>컨센서스:</span>
                            <span className="font-bold text-indigo-700 font-mono">
                              {data.current_consensus_eps != null ? epsF(data.current_consensus_eps) : data.history[0]?.eps_estimate != null ? epsF(data.history[0].eps_estimate) : '없음'}
                            </span>
                          </div>
                          <input
                            type="number" step="0.01"
                            value={simEps ?? ''}
                            onChange={e => setSimEps(e.target.value ? parseFloat(e.target.value) : null)}
                            className="w-full px-3 py-1.5 text-sm border border-indigo-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300 font-mono"
                            placeholder="예상 EPS 입력"
                          />
                          {data.history[0]?.eps_estimate && (
                            <input type="range"
                              min={(data.history[0].eps_estimate * 0.7).toFixed(2)}
                              max={(data.history[0].eps_estimate * 1.3).toFixed(2)}
                              step="0.01"
                              value={simEps ?? data.history[0].eps_estimate}
                              onChange={e => setSimEps(parseFloat(e.target.value))}
                              className="w-full h-1.5 bg-indigo-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 mt-2"
                            />
                          )}
                          {simResult && (
                            <div className="text-[10px] mt-1 flex items-center gap-1">
                              <span className="text-slate-500">→ 서프라이즈:</span>
                              <span className="font-bold font-mono" style={{
                                color: simResult.impliedSurprise > 2 ? '#059669' : simResult.impliedSurprise < -2 ? '#dc2626' : '#6b7280'
                              }}>
                                {pct(simResult.impliedSurprise)}
                              </span>
                              <CategoryBadge category={simResult.impliedSurprise > 2 ? 'Beat' : simResult.impliedSurprise < -2 ? 'Miss' : 'Meet'} />
                            </div>
                          )}
                        </div>
                      )}

                      {/* 매출 입력 */}
                      {showRevInput && data.revenue_estimates?.current_quarter?.rev_avg && (
                        <div className={showEpsInput ? "pt-2 border-t border-indigo-100" : ""}>
                          <div className="flex items-center gap-1.5 mb-1">
                            <TrendingUp size={10} className="text-violet-500" />
                            <label className="text-[10px] text-slate-600 font-semibold">
                              예상 실제 매출
                              {primaryFactor?.id === 'revenue_growth' && (
                                <span className="text-violet-500 font-bold ml-1">(핵심 지표)</span>
                              )}
                            </label>
                          </div>
                          <div className="text-[10px] text-slate-500 mb-1 bg-white/60 rounded px-2 py-1">
                            컨센서스: <span className="font-bold text-violet-700">{revFmt(data.revenue_estimates.current_quarter.rev_avg)}</span>
                          </div>
                          <input type="number" step="0.1"
                            value={simRev ?? ''}
                            onChange={e => setSimRev(e.target.value ? parseFloat(e.target.value) : null)}
                            className="w-full px-3 py-1.5 text-sm border border-violet-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-300 font-mono"
                            placeholder={`예: ${(data.revenue_estimates.current_quarter.rev_avg / 1e9).toFixed(1)} (십억$)`}
                          />
                          <input type="range"
                            min={(data.revenue_estimates.current_quarter.rev_avg * 0.85 / 1e9).toFixed(1)}
                            max={(data.revenue_estimates.current_quarter.rev_avg * 1.15 / 1e9).toFixed(1)}
                            step="0.1"
                            value={simRev ?? (data.revenue_estimates.current_quarter.rev_avg / 1e9).toFixed(1)}
                            onChange={e => setSimRev(parseFloat(e.target.value))}
                            className="w-full h-1.5 bg-violet-200 rounded-lg appearance-none cursor-pointer accent-violet-600 mt-2"
                          />
                          {simRev != null && (
                            <div className="text-[10px] text-violet-500 mt-1">
                              → 매출 서프라이즈: <span className="font-bold font-mono">
                                {pct(((simRev * 1e9 - data.revenue_estimates.current_quarter.rev_avg) / data.revenue_estimates.current_quarter.rev_avg) * 100)}
                              </span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>

                    {/* 우측: 결과 */}
                    <div className="space-y-3">
                      {simResult && simResult.count > 0 ? (
                        <>
                          <div className="bg-white rounded-xl border border-indigo-100 p-3">
                            <div className="text-[10px] text-slate-500 mb-1">예상 주가 변동 범위</div>
                            <div className="text-lg font-bold text-indigo-700 font-mono">
                              {pct(simResult.p10)} ~ {pct(simResult.p90)}
                            </div>
                            <GaugeBar p10={simResult.p10} p90={simResult.p90} avg={simResult.avgReaction} />
                          </div>

                          <div className="grid grid-cols-2 gap-2">
                            <div className="bg-white rounded-lg border border-indigo-100 p-2.5 text-center">
                              <div className="text-[9px] text-slate-500">평균 반응</div>
                              <div className="text-sm font-bold font-mono" style={{ color: simResult.avgReaction > 0 ? '#059669' : '#dc2626' }}>
                                {pct(simResult.avgReaction)}
                              </div>
                            </div>
                            <div className="bg-white rounded-lg border border-indigo-100 p-2.5 text-center">
                              <div className="text-[9px] text-slate-500">상승 확률</div>
                              <div className="text-sm font-bold text-indigo-700">{simResult.upProbability}%</div>
                              <div className="w-full h-1 bg-slate-100 rounded-full mt-1">
                                <div className="h-1 rounded-full transition-all" style={{
                                  width: `${simResult.upProbability}%`,
                                  backgroundColor: simResult.upProbability >= 50 ? '#10b981' : '#ef4444',
                                }} />
                              </div>
                            </div>
                          </div>

                          {/* 가이던스 주의 배너 */}
                          {guidanceAnalysis.guidance_influence_score >= 30 && (
                            <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                              <div className="text-[10px] text-amber-800 font-semibold mb-0.5 flex items-center gap-1">
                                <AlertCircle size={10} /> 가이던스 영향 주의
                              </div>
                              <div className="text-[9px] text-amber-700">
                                이 종목은 가이던스가 주가에 큰 영향을 미칩니다.
                                EPS Beat + 부정적 가이던스 시 하락 가능성이 있습니다.
                              </div>
                            </div>
                          )}

                          <div className="text-[10px] text-slate-500 flex items-center gap-2">
                            <span>유사 사례 <span className="font-bold">{simResult.count}건</span> (서프라이즈 ±3%)</span>
                            {simResult.guidanceApplied && (
                              <span className="text-purple-600 font-semibold flex items-center gap-1">
                                <Brain size={10} /> 가이던스 감성 반영됨
                              </span>
                            )}
                          </div>
                          {simResult.similar.length > 0 && (
                            <div className="space-y-1 max-h-20 overflow-y-auto">
                              {simResult.similar.map((s, i) => (
                                <div key={i} className="flex items-center gap-2 text-[10px] bg-white rounded px-2 py-1 border border-slate-100">
                                  <span className="text-slate-400">{s.date}</span>
                                  <span className="font-mono text-slate-500">{pct(s.surprise_pct)}</span>
                                  <span className="ml-auto font-mono font-bold" style={{ color: s.reaction_1d_change > 0 ? '#059669' : '#dc2626' }}>
                                    {pct(s.reaction_1d_change)}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )}
                        </>
                      ) : simResult ? (
                        <div className="flex items-center justify-center h-full text-xs text-slate-400 py-8">
                          해당 서프라이즈 범위에 유사한 과거 사례가 없습니다.
                        </div>
                      ) : (
                        <div className="flex items-center justify-center h-full text-xs text-slate-400 py-8">
                          예상 EPS를 입력하면 시뮬레이션 결과가 표시됩니다.
                        </div>
                      )}
                    </div>
                  </div>
                )
              })()}
            </div>
          </div>

          {/* ═══ AI 가이던스 분석 (Gemini) ═══ */}
          {(guidanceData || guidanceLoading) && (
            <div className="border border-purple-200 rounded-xl overflow-hidden">
              {/* 헤더 */}
              <div className="bg-gradient-to-r from-purple-700 to-indigo-800 px-4 py-2.5 flex items-center justify-between">
                <h4 className="text-xs font-bold text-white flex items-center gap-1.5">
                  <Brain size={14} />
                  AI 가이던스 분석
                  {guidanceData && (
                    <span className="font-normal text-purple-200 ml-1">
                      ({guidanceData.total_analyzed}분기 분석 완료)
                    </span>
                  )}
                </h4>
                <div className="flex items-center gap-2">
                  {guidanceLoading && (
                    <div className="flex items-center gap-1.5">
                      <div className="animate-spin rounded-full h-3 w-3 border border-white/30 border-t-white" />
                      <span className="text-[9px] text-purple-200">Gemini 분석 중...</span>
                    </div>
                  )}
                  <span className="text-[9px] bg-white/15 text-purple-200 px-2 py-0.5 rounded-full">
                    {guidanceData?.guidance?.some(g => g.source_type === 'transcript')
                      ? 'Earnings Call + Gemini AI'
                      : 'SEC 8-K + Gemini AI'}
                  </span>
                </div>
              </div>

              {guidanceData && (
                <div className="p-4 space-y-4">
                  {/* 테마 패턴 요약 */}
                  {guidanceData.theme_patterns?.themes && Object.keys(guidanceData.theme_patterns.themes).length > 0 && (
                    <div>
                      <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                        <Hash size={11} />
                        가이던스 테마별 주가 반응 패턴
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(guidanceData.theme_patterns.themes).slice(0, 12).map(([theme, data]) => {
                          const isPositive = data.avg_reaction > 0
                          return (
                            <div key={theme}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-[10px] transition-all hover:shadow-sm"
                              style={{
                                backgroundColor: isPositive ? 'rgba(16,185,129,0.05)' : 'rgba(239,68,68,0.05)',
                                borderColor: isPositive ? '#a7f3d0' : '#fecaca',
                              }}
                            >
                              <span className="font-medium text-slate-700">{theme}</span>
                              <span className="font-mono font-bold" style={{ color: isPositive ? '#059669' : '#dc2626' }}>
                                {data.avg_reaction > 0 ? '+' : ''}{data.avg_reaction}%
                              </span>
                              <span className="text-slate-400">({data.count}건)</span>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {/* 분기별 가이던스 상세 */}
                  <div>
                    <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                      <MessageSquare size={11} />
                      분기별 가이던스 AI 분석
                    </div>
                    <div className="space-y-3">
                      {guidanceData.guidance.map((g, i) => {
                        const sentColor = g.sentiment_score >= 65 ? '#059669'
                          : g.sentiment_score >= 45 ? '#d97706' : '#dc2626'
                        const sentLabel = g.sentiment_score >= 65 ? '긍정'
                          : g.sentiment_score >= 45 ? '중립' : '부정'

                        // Find matching earnings for price reaction
                        const matchedEarning = data?.history?.find(h => h.period_end === g.period_end)
                        const reaction = matchedEarning?.reaction_1d_change

                        // Quarter label
                        let qLabel = g.period_end
                        if (g.period_end) {
                          const d = new Date(g.period_end)
                          const q = Math.ceil((d.getMonth() + 1) / 3)
                          qLabel = `Q${q}'${d.getFullYear().toString().slice(2)}`
                        }

                        return (
                          <div key={g.period_end || i} className="border border-slate-200 rounded-lg overflow-hidden hover:border-slate-300 transition-colors">
                            {/* 분기 헤더 */}
                            <div className="flex items-center justify-between bg-slate-50 px-3 py-2">
                              <div className="flex items-center gap-2">
                                <span className="text-[11px] font-bold text-slate-700">{qLabel}</span>
                                <span className="text-[9px] text-slate-400">{g.report_date}</span>
                                {/* 감성 점수 */}
                                <span className="text-[9px] font-bold px-2 py-0.5 rounded-full border" style={{
                                  color: sentColor,
                                  backgroundColor: `${sentColor}10`,
                                  borderColor: `${sentColor}30`,
                                }}>
                                  {sentLabel} {g.sentiment_score}
                                </span>
                                {/* 영향 요인 */}
                                {g.impact_factor && (
                                  <span className="text-[9px] bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded-full border border-indigo-100">
                                    {g.impact_factor === 'guidance' ? '가이던스 주도'
                                      : g.impact_factor === 'eps' ? 'EPS 주도'
                                      : g.impact_factor === 'revenue' ? '매출 주도'
                                      : g.impact_factor === 'sentiment' ? '심리 주도'
                                      : g.impact_factor === 'macro' ? '매크로 영향'
                                      : g.impact_factor}
                                  </span>
                                )}
                                {/* 소스 타입 */}
                                <span className={`text-[8px] px-1.5 py-0.5 rounded-full ${
                                  g.source_type === 'transcript'
                                    ? 'bg-emerald-50 text-emerald-600 border border-emerald-100'
                                    : 'bg-slate-50 text-slate-400 border border-slate-200'
                                }`}>
                                  {g.source_type === 'transcript' ? 'Earnings Call' : '8-K Filing'}
                                </span>
                                {/* 원문 보기 — 트랜스크립트(Motley Fool)/8-K(SEC) 원본 링크. 실제 URL일 때만. */}
                                {g.filing_url?.startsWith('http') && (
                                  <a
                                    href={g.filing_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-[8px] px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-600 border border-indigo-100 hover:bg-indigo-100 transition-colors"
                                    title={g.source_type === 'transcript' ? '어닝콜 원문 트랜스크립트 보기' : '8-K 공시 원문 보기'}
                                  >
                                    원문 보기 ↗
                                  </a>
                                )}
                              </div>
                              {reaction != null && (
                                <span className="text-[11px] font-bold font-mono" style={{ color: reaction > 0 ? '#059669' : '#dc2626' }}>
                                  {reaction > 0 ? '+' : ''}{reaction.toFixed(2)}%
                                </span>
                              )}
                            </div>

                            {/* 내용 */}
                            <div className="px-3 py-2.5 space-y-2">
                              {/* 가이던스 요약 */}
                              {g.guidance_summary && (
                                <p className="text-[11px] text-slate-700 leading-relaxed">
                                  {g.guidance_summary}
                                </p>
                              )}

                              {/* 핵심 테마 태그 */}
                              {g.key_themes?.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                  {g.key_themes.map((theme, ti) => (
                                    <span key={ti} className="text-[9px] bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded border border-purple-100">
                                      #{theme}
                                    </span>
                                  ))}
                                </div>
                              )}

                              {/* 상세 가이던스 (매출/마진/수치) */}
                              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-1">
                                {g.revenue_guidance && g.revenue_guidance !== '미제시' && (
                                  <div className="bg-violet-50/50 rounded px-2 py-1.5 border border-violet-100">
                                    <div className="text-[8px] font-semibold text-violet-500 uppercase">매출 가이던스</div>
                                    <div className="text-[10px] text-violet-800">{g.revenue_guidance}</div>
                                  </div>
                                )}
                                {g.margin_guidance && g.margin_guidance !== '미제시' && (
                                  <div className="bg-amber-50/50 rounded px-2 py-1.5 border border-amber-100">
                                    <div className="text-[8px] font-semibold text-amber-500 uppercase">마진 가이던스</div>
                                    <div className="text-[10px] text-amber-800">{g.margin_guidance}</div>
                                  </div>
                                )}
                                {g.specific_numbers && (
                                  <div className="bg-blue-50/50 rounded px-2 py-1.5 border border-blue-100">
                                    <div className="text-[8px] font-semibold text-blue-500 uppercase mb-1">주요 수치</div>
                                    {typeof g.specific_numbers === 'object' ? (
                                      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                                        {Object.entries(g.specific_numbers).slice(0, 10).map(([k, v]) => (
                                          <div key={k} className="text-[10px] text-blue-800 flex justify-between gap-1">
                                            <span className="text-blue-500 truncate" title={k.replace(/_/g, ' ')}>{k.replace(/_/g, ' ')}</span>
                                            <span className="font-medium whitespace-nowrap">{v}</span>
                                          </div>
                                        ))}
                                        {Object.keys(g.specific_numbers).length > 10 && (
                                          <div className="text-[9px] text-blue-400 col-span-2">...외 {Object.keys(g.specific_numbers).length - 10}개</div>
                                        )}
                                      </div>
                                    ) : (
                                      <div className="text-[10px] text-blue-800">{g.specific_numbers}</div>
                                    )}
                                  </div>
                                )}
                              </div>

                              {/* AI 주석 (시장 반응 해석) */}
                              {g.ai_annotation && (
                                <div className="flex items-start gap-1.5 bg-gradient-to-r from-purple-50 to-indigo-50 rounded-lg px-2.5 py-2 border border-purple-100">
                                  <Sparkles size={11} className="text-purple-500 mt-0.5 flex-shrink-0" />
                                  <div className="text-[10px] text-purple-800 leading-relaxed">
                                    <span className="font-semibold">AI 분석:</span> {g.ai_annotation}
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* 안내 */}
                  <p className="text-[9px] text-slate-400 text-center">
                    어닝콜 트랜스크립트를 Gemini AI가 분석한 결과입니다. 과거 가이던스 패턴과 감성 점수가 시뮬레이션에 반영됩니다.
                  </p>
                </div>
              )}

              {guidanceLoading && !guidanceData && (
                <div className="p-6 flex flex-col items-center justify-center gap-2">
                  <div className="animate-spin rounded-full h-6 w-6 border-2 border-purple-500 border-t-transparent" />
                  <span className="text-xs text-slate-500">AI 가이던스 분석을 불러오는 중...</span>
                  <span className="text-[9px] text-slate-400">첫 분석 시 1-2분 소요, 이후 즉시 로드</span>
                </div>
              )}

              {guidanceError && (
                <div className="p-4 text-xs text-amber-600 flex items-center gap-2">
                  <AlertCircle size={14} />
                  {guidanceError}
                </div>
              )}
            </div>
          )}

          {/* ═══ 가이던스 영향 분석 (상세) ═══ */}
          {guidanceAnalysis.cases?.length > 0 && (
            <CollapsibleSection
              title="가이던스 영향 상세 분석"
              icon={Activity}
              badge={`${guidanceAnalysis.cases.length}건`}
              defaultOpen={false}
            >
              <div className="flex flex-wrap items-center gap-3 mb-3 text-[11px]">
                <div className="flex items-center gap-1.5 bg-red-50 border border-red-200 rounded-lg px-2.5 py-1">
                  <ArrowDownRight size={10} className="text-red-500" />
                  <span className="text-red-700">Beat해도 하락: <span className="font-bold">{guidanceAnalysis.beat_but_down_pct}%</span></span>
                </div>
                <div className="flex items-center gap-1.5 bg-emerald-50 border border-emerald-200 rounded-lg px-2.5 py-1">
                  <ArrowUpRight size={10} className="text-emerald-500" />
                  <span className="text-emerald-700">Miss해도 상승: <span className="font-bold">{guidanceAnalysis.miss_but_up_pct}%</span></span>
                </div>
              </div>

              <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 sticky top-0">
                    <tr>
                      <th className="px-2 py-1.5 text-left text-[10px] font-semibold text-slate-600">발표일</th>
                      <th className="px-2 py-1.5 text-left text-[10px] font-semibold text-slate-600">분기</th>
                      <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-slate-600">EPS 결과</th>
                      <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-slate-600">서프라이즈</th>
                      <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-slate-600">주가 반응</th>
                      <th className="px-2 py-1.5 text-left text-[10px] font-semibold text-slate-600">해석</th>
                    </tr>
                  </thead>
                  <tbody>
                    {guidanceAnalysis.cases.map((c, i) => (
                      <tr key={i} className="border-t border-slate-100 hover:bg-slate-50/50">
                        <td className="px-2 py-1.5 text-slate-600 whitespace-nowrap">{c.date}</td>
                        <td className="px-2 py-1.5 text-slate-500 whitespace-nowrap">{c.period}</td>
                        <td className="px-2 py-1.5 text-center"><CategoryBadge category={c.eps_category} /></td>
                        <td className="px-2 py-1.5 text-center font-mono" style={{
                          color: c.surprise_pct > 0 ? '#059669' : c.surprise_pct < 0 ? '#dc2626' : '#6b7280'
                        }}>
                          {c.surprise_pct != null ? pct(c.surprise_pct) : '-'}
                        </td>
                        <td className="px-2 py-1.5 text-center font-mono font-medium" style={{
                          color: c.reaction > 0 ? '#059669' : '#dc2626'
                        }}>
                          {c.reaction != null ? pct(c.reaction) : '-'}
                        </td>
                        <td className="px-2 py-1.5 text-[10px] text-slate-500">{c.interpretation}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CollapsibleSection>
          )}

          {/* ═══ 전체 히스토리 테이블 ═══ */}
          <CollapsibleSection
            title="전체 실적 히스토리"
            icon={BarChart3}
            badge={`${data.total_count}건`}
          >
            <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0">
                  <tr>
                    {[
                      { key: 'date', label: '발표일' },
                      { key: 'period', label: '분기' },
                      { key: 'eps_estimate', label: '예상 EPS' },
                      { key: 'eps_actual', label: '실제 EPS' },
                      { key: 'surprise_pct', label: '서프라이즈%' },
                      { key: 'category', label: '결과' },
                      { key: 'pre_3d_change', label: 'D-3→D-1' },
                      { key: 'reaction_1d_change', label: 'D-1→D+1' },
                      { key: 'post_3d_change', label: 'D→D+3' },
                      { key: 'post_5d_change', label: 'D→D+5' },
                    ].map(col => (
                      <th key={col.key}
                        onClick={() => handleSort(col.key)}
                        className="px-2 py-2 text-left font-semibold text-slate-600 cursor-pointer hover:text-indigo-600 whitespace-nowrap select-none"
                      >
                        {col.label}
                        {sortKey === col.key && (
                          <span className="ml-0.5 text-indigo-500">{sortDir === 'asc' ? '↑' : '↓'}</span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedHistory.map((h, i) => (
                    <tr key={i}
                      className="border-t border-slate-100 hover:bg-slate-50/50 transition-colors"
                      style={{
                        backgroundColor: h.category === 'Beat' ? 'rgba(16,185,129,0.03)'
                          : h.category === 'Miss' ? 'rgba(239,68,68,0.03)' : undefined
                      }}
                    >
                      <td className="px-2 py-1.5 font-medium text-slate-700 whitespace-nowrap">{h.date}</td>
                      <td className="px-2 py-1.5 text-slate-500 whitespace-nowrap">{h.period || '-'}</td>
                      <td className="px-2 py-1.5 text-center font-mono">
                        <span className={h.estimate_verified ? '' : 'text-slate-300'}>
                          {epsF(h.eps_estimate)}
                        </span>
                        {h.estimate_verified === false && h.eps_estimate != null && (
                          <span className="text-[8px] text-amber-400 ml-0.5" title="미검증 추정치">?</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-center font-mono font-medium">{epsF(h.eps_actual)}</td>
                      <td className="px-2 py-1.5 text-center font-mono" style={{
                        color: h.surprise_pct > 0 ? '#059669' : h.surprise_pct < 0 ? '#dc2626' : '#6b7280'
                      }}>
                        {h.surprise_pct != null ? `${h.surprise_pct > 0 ? '+' : ''}${h.surprise_pct.toFixed(2)}%` : '-'}
                      </td>
                      <td className="px-2 py-1.5 text-center"><CategoryBadge category={h.category} /></td>
                      <ChangeCell value={h.pre_3d_change} />
                      <ChangeCell value={h.reaction_1d_change} />
                      <ChangeCell value={h.post_3d_change} />
                      <ChangeCell value={h.post_5d_change} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CollapsibleSection>

          {/* ═══ 매출 추정치 & 가이던스 ═══ */}
          {(data.revenue_estimates || data.revenue_history?.length > 0) && (
            <CollapsibleSection
              title="매출 추정치 & 재무 데이터"
              icon={DollarSign}
              badge={data.revenue_history?.length ? `${data.revenue_history.length}분기` : null}
            >
              <div className="space-y-4">
                {data.revenue_estimates && Object.keys(data.revenue_estimates).length > 0 && (
                  <div>
                    <div className="text-[10px] font-semibold text-slate-600 mb-1.5">애널리스트 컨센서스 추정치</div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="bg-violet-100/50">
                            <th className="px-2 py-1.5 text-left text-[10px] font-semibold text-violet-700">기간</th>
                            <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-violet-700">매출 추정(평균)</th>
                            <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-violet-700">매출 범위</th>
                            <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-violet-700">EPS 추정</th>
                            <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-violet-700">이익 성장률</th>
                            <th className="px-2 py-1.5 text-center text-[10px] font-semibold text-violet-700">애널리스트 수</th>
                          </tr>
                        </thead>
                        <tbody>
                          {['current_quarter', 'next_quarter', 'current_year', 'next_year'].map(key => {
                            const est = data.revenue_estimates[key]
                            if (!est) return null
                            const labels = { current_quarter: '현재 분기', next_quarter: '다음 분기', current_year: '현재 연도', next_year: '다음 연도' }
                            return (
                              <tr key={key} className="border-t border-violet-100 hover:bg-violet-50/50">
                                <td className="px-2 py-1.5 font-medium text-slate-700">{labels[key]} <span className="text-[9px] text-slate-400">({est.end_date})</span></td>
                                <td className="px-2 py-1.5 text-center font-mono font-medium">{revFmt(est.rev_avg)}</td>
                                <td className="px-2 py-1.5 text-center text-[10px] text-slate-500 font-mono">
                                  {est.rev_low && est.rev_high ? `${revFmt(est.rev_low)} ~ ${revFmt(est.rev_high)}` : '-'}
                                </td>
                                <td className="px-2 py-1.5 text-center font-mono">{est.eps_avg != null ? `$${est.eps_avg.toFixed(2)}` : '-'}</td>
                                <td className="px-2 py-1.5 text-center font-mono" style={{ color: est.earnings_growth > 0 ? '#059669' : est.earnings_growth < 0 ? '#dc2626' : '#6b7280' }}>
                                  {growthFmt(est.earnings_growth)}
                                </td>
                                <td className="px-2 py-1.5 text-center text-slate-500">{est.num_analysts_rev || '-'}</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {data.revenue_history?.length > 0 && (
                  <div>
                    <div className="text-[10px] font-semibold text-slate-600 mb-1.5">분기별 실제 매출</div>
                    <div className="overflow-x-auto max-h-60 overflow-y-auto border border-violet-100 rounded-lg">
                      <table className="w-full text-xs">
                        <thead className="bg-violet-50 sticky top-0">
                          <tr>
                            <th className="px-2 py-1.5 text-left text-[10px] font-semibold text-violet-700">분기</th>
                            <th className="px-2 py-1.5 text-right text-[10px] font-semibold text-violet-700">매출</th>
                            <th className="px-2 py-1.5 text-right text-[10px] font-semibold text-violet-700">순이익</th>
                            <th className="px-2 py-1.5 text-right text-[10px] font-semibold text-violet-700">마진</th>
                            <th className="px-2 py-1.5 text-right text-[10px] font-semibold text-violet-700">QoQ</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.revenue_history.map((q, i) => {
                            const prev = data.revenue_history[i + 1]
                            const qoq = prev?.revenue_actual && q.revenue_actual
                              ? ((q.revenue_actual - prev.revenue_actual) / Math.abs(prev.revenue_actual) * 100) : null
                            const margin = q.revenue_actual && q.earnings_actual
                              ? (q.earnings_actual / q.revenue_actual * 100) : null
                            return (
                              <tr key={i} className="border-t border-violet-50 hover:bg-violet-50/30">
                                <td className="px-2 py-1 font-medium text-slate-700 whitespace-nowrap">
                                  {q.quarter_label} {q.period_end && <span className="text-[9px] text-slate-400">({q.period_end})</span>}
                                </td>
                                <td className="px-2 py-1 text-right font-mono font-medium text-slate-800">{revFmt(q.revenue_actual)}</td>
                                <td className="px-2 py-1 text-right font-mono text-slate-500">{q.earnings_actual != null ? revFmt(q.earnings_actual) : '-'}</td>
                                <td className="px-2 py-1 text-right font-mono text-slate-500">
                                  {margin != null ? `${margin.toFixed(1)}%` : '-'}
                                </td>
                                <td className="px-2 py-1 text-right font-mono" style={{ color: qoq > 0 ? '#059669' : qoq < 0 ? '#dc2626' : '#6b7280' }}>
                                  {qoq != null ? `${qoq > 0 ? '+' : ''}${qoq.toFixed(1)}%` : '-'}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            </CollapsibleSection>
          )}

          {/* 면책 조항 */}
          <p className="text-[9px] text-slate-400 text-center pt-1">
            이 시뮬레이션은 과거 데이터를 기반으로 한 참고 자료이며, 투자 조언이 아닙니다. 종목별 분석은 통계적 상관관계에 기반하며, 미래 결과를 보장하지 않습니다.
          </p>
        </>
      )}
    </div>
  )
}
