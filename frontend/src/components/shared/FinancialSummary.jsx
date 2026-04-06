import { useState, useEffect, useMemo } from 'react'
import { PieChart, TrendingUp, DollarSign, Percent } from 'lucide-react'
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, AreaChart, Area, Cell
} from 'recharts'
import { stockAPI } from '../../api/index'

/* ── 숫자 포매팅 ── */
function fmtBillions(n) {
  if (n == null) return '-'
  if (Math.abs(n) >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T'
  if (Math.abs(n) >= 1e9)  return '$' + (n / 1e9).toFixed(2) + 'B'
  if (Math.abs(n) >= 1e6)  return '$' + (n / 1e6).toFixed(2) + 'M'
  return '$' + n.toLocaleString()
}

/* ── 탭 정의 ── */
const TABS = [
  { key: 'income', label: '매출/이익', icon: DollarSign },
  { key: 'margin', label: '마진', icon: Percent },
  { key: 'eps',    label: 'EPS', icon: TrendingUp },
]

/* ── 차트 색상 ── */
const COLORS = {
  revenue:  '#6366f1',
  ebit:     '#f59e0b',
  netIncome:'#10b981',
  grossMargin:     '#6366f1',
  operatingMargin: '#f59e0b',
  profitMargin:    '#10b981',
  eps:      '#8b5cf6',
}

/* ── 커스텀 툴팁 ── */
function ChartTooltip({ active, payload, label, type }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-white border border-slate-200 rounded-xl px-3.5 py-2.5 text-xs shadow-lg min-w-[160px]">
      <p className="text-slate-400 text-[10px] mb-1.5 font-medium">{label}</p>
      {payload.map((entry, i) => (
        <div key={i} className="flex items-center justify-between gap-4 py-0.5">
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: entry.color }} />
            <span className="text-slate-600">{entry.name}</span>
          </div>
          <span className="font-bold text-slate-800">
            {type === 'currency' ? fmtBillions(entry.value) :
             type === 'percent'  ? (entry.value?.toFixed(1) + '%') :
             ('$' + entry.value?.toFixed(2))}
          </span>
        </div>
      ))}
    </div>
  )
}

/* ── 요약 카드 (최신 연도 하이라이트) ── */
function SummaryCards({ data }) {
  if (!data || data.length === 0) return null
  const latest = data[data.length - 1]
  const prev = data.length >= 2 ? data[data.length - 2] : null

  const cards = [
    {
      label: '매출',
      value: latest.revenue,
      prev: prev?.revenue,
      format: fmtBillions,
      color: 'indigo',
    },
    {
      label: '영업이익',
      value: latest.ebit,
      prev: prev?.ebit,
      format: fmtBillions,
      color: 'amber',
    },
    {
      label: '순이익',
      value: latest.netIncome,
      prev: prev?.netIncome,
      format: fmtBillions,
      color: 'emerald',
    },
  ]

  return (
    <div className="grid grid-cols-3 gap-3 mb-4">
      {cards.map(c => {
        const growth = c.value != null && c.prev != null && c.prev !== 0
          ? ((c.value - c.prev) / Math.abs(c.prev) * 100)
          : null
        return (
          <div key={c.label} className="bg-slate-50 rounded-xl p-3 border border-slate-100">
            <p className="text-[10px] text-slate-400 font-medium">{c.label}</p>
            <p className="text-sm font-bold text-slate-800 mt-0.5">
              {c.value != null ? c.format(c.value) : '-'}
            </p>
            {growth != null && (
              <p className={`text-[10px] font-semibold mt-0.5 ${growth >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                {growth >= 0 ? '+' : ''}{growth.toFixed(1)}% YoY
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   메인 FinancialSummary 컴포넌트
   ══════════════════════════════════════════════════════════════════ */
export default function FinancialSummary({ ticker }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [activeTab, setActiveTab] = useState('income')

  useEffect(() => {
    if (!ticker) return
    setLoading(true)
    setError(false)
    stockAPI.getMetricHistory(ticker)
      .then(r => setData(r.data))
      .catch(() => { setData(null); setError(true) })
      .finally(() => setLoading(false))
  }, [ticker])

  /* ── 매출/영업이익/순이익 차트 데이터 ── */
  const incomeData = useMemo(() => {
    if (!data) return []
    const revMap = {}
    const niMap = {}
    const ebitMap = {}
    const allDates = new Set()

    // revenue = annualTotalRevenue
    ;(data.revenue || []).forEach(d => { revMap[d.date] = d.value; allDates.add(d.date) })
    // net_income = annualNetIncome
    ;(data.net_income || []).forEach(d => { niMap[d.date] = d.value; allDates.add(d.date) })
    // ebit_hist = annualEBIT (or operating_income as fallback)
    ;(data.ebit_hist || data.operating_income || []).forEach(d => { ebitMap[d.date] = d.value; allDates.add(d.date) })

    return Array.from(allDates)
      .sort()
      .map(date => ({
        year: date.slice(0, 4),
        revenue: revMap[date] ?? null,
        ebit: ebitMap[date] ?? null,
        netIncome: niMap[date] ?? null,
      }))
      .filter(d => d.revenue != null || d.ebit != null || d.netIncome != null)
  }, [data])

  /* ── 마진 트렌드 데이터 ── */
  const marginData = useMemo(() => {
    if (!data) return []
    const gmMap = {}
    const omMap = {}
    const pmMap = {}
    const allDates = new Set()

    ;(data.gross_margin_hist || []).forEach(d => { gmMap[d.date] = d.value; allDates.add(d.date) })
    ;(data.operating_margin_hist || []).forEach(d => { omMap[d.date] = d.value; allDates.add(d.date) })
    ;(data.profit_margin_hist || []).forEach(d => { pmMap[d.date] = d.value; allDates.add(d.date) })

    return Array.from(allDates)
      .sort()
      .map(date => ({
        year: date.slice(0, 4),
        grossMargin: gmMap[date] ?? null,
        operatingMargin: omMap[date] ?? null,
        profitMargin: pmMap[date] ?? null,
      }))
      .filter(d => d.grossMargin != null || d.operatingMargin != null || d.profitMargin != null)
  }, [data])

  /* ── EPS 트렌드 데이터 ── */
  const epsData = useMemo(() => {
    if (!data) return []
    return (data.eps_hist || [])
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(d => ({
        year: d.date.slice(0, 4),
        eps: d.value,
      }))
  }, [data])

  const hasIncome = incomeData.length > 0
  const hasMargin = marginData.length > 0
  const hasEps = epsData.length > 0
  const hasAnyData = hasIncome || hasMargin || hasEps

  /* ── 로딩/에러/빈 상태 ── */
  if (loading) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-5 h-5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-slate-400">재무 요약 로딩 중...</span>
        </div>
        <div className="h-64 bg-slate-50 rounded-xl animate-pulse" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <div className="h-32 flex items-center justify-center text-slate-400 text-sm">
          재무 요약 데이터를 불러올 수 없습니다.
        </div>
      </div>
    )
  }

  if (!hasAnyData) return null

  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
      {/* ── 헤더 ── */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <PieChart size={16} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-800">재무 요약</h3>
          <span className="text-[10px] text-slate-400">연간 실적 트렌드</span>
        </div>
      </div>

      {/* ── 요약 카드 (매출/영업이익/순이익 최신) ── */}
      {hasIncome && <SummaryCards data={incomeData} />}

      {/* ── 탭 버튼 ── */}
      <div className="flex gap-1 mb-4 bg-slate-100 rounded-xl p-1">
        {TABS.map(tab => {
          const disabled = (tab.key === 'income' && !hasIncome) ||
                          (tab.key === 'margin' && !hasMargin) ||
                          (tab.key === 'eps' && !hasEps)
          const Icon = tab.icon
          return (
            <button
              key={tab.key}
              onClick={() => !disabled && setActiveTab(tab.key)}
              disabled={disabled}
              className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg transition-all ${
                activeTab === tab.key
                  ? 'bg-white text-indigo-600 shadow-sm'
                  : disabled
                  ? 'text-slate-300 cursor-not-allowed'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Icon size={13} />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* ── 차트 영역 ── */}
      <div className="h-[300px]">
        {activeTab === 'income' && hasIncome && (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={incomeData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="year" tick={{ fontSize: 11, fill: '#94a3b8' }} />
              <YAxis
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                width={70}
                tickFormatter={v => {
                  if (Math.abs(v) >= 1e12) return '$' + (v/1e12).toFixed(0) + 'T'
                  if (Math.abs(v) >= 1e9)  return '$' + (v/1e9).toFixed(0) + 'B'
                  if (Math.abs(v) >= 1e6)  return '$' + (v/1e6).toFixed(0) + 'M'
                  return v.toLocaleString()
                }}
              />
              <Tooltip content={<ChartTooltip type="currency" />} />
              <Legend
                iconType="circle"
                iconSize={8}
                wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
              />
              <Bar dataKey="revenue" name="매출" fill={COLORS.revenue} fillOpacity={0.8} radius={[4, 4, 0, 0]} />
              <Bar dataKey="ebit" name="영업이익" fill={COLORS.ebit} fillOpacity={0.8} radius={[4, 4, 0, 0]} />
              <Bar dataKey="netIncome" name="순이익" fill={COLORS.netIncome} fillOpacity={0.8} radius={[4, 4, 0, 0]} />
            </ComposedChart>
          </ResponsiveContainer>
        )}

        {activeTab === 'margin' && hasMargin && (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={marginData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
              <defs>
                <linearGradient id="gradGross" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS.grossMargin} stopOpacity={0.15} />
                  <stop offset="95%" stopColor={COLORS.grossMargin} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gradOp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS.operatingMargin} stopOpacity={0.15} />
                  <stop offset="95%" stopColor={COLORS.operatingMargin} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gradProfit" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS.profitMargin} stopOpacity={0.15} />
                  <stop offset="95%" stopColor={COLORS.profitMargin} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="year" tick={{ fontSize: 11, fill: '#94a3b8' }} />
              <YAxis
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                width={50}
                tickFormatter={v => v.toFixed(0) + '%'}
              />
              <Tooltip content={<ChartTooltip type="percent" />} />
              <Legend
                iconType="circle"
                iconSize={8}
                wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
              />
              <Area
                type="monotone"
                dataKey="grossMargin"
                name="매출총이익률"
                stroke={COLORS.grossMargin}
                strokeWidth={2.5}
                fill="url(#gradGross)"
                dot={{ r: 3.5, strokeWidth: 2, fill: '#fff' }}
                activeDot={{ r: 5.5 }}
                connectNulls
              />
              <Area
                type="monotone"
                dataKey="operatingMargin"
                name="영업이익률"
                stroke={COLORS.operatingMargin}
                strokeWidth={2.5}
                fill="url(#gradOp)"
                dot={{ r: 3.5, strokeWidth: 2, fill: '#fff' }}
                activeDot={{ r: 5.5 }}
                connectNulls
              />
              <Area
                type="monotone"
                dataKey="profitMargin"
                name="순이익률"
                stroke={COLORS.profitMargin}
                strokeWidth={2.5}
                fill="url(#gradProfit)"
                dot={{ r: 3.5, strokeWidth: 2, fill: '#fff' }}
                activeDot={{ r: 5.5 }}
                connectNulls
              />
            </AreaChart>
          </ResponsiveContainer>
        )}

        {activeTab === 'eps' && hasEps && (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={epsData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="year" tick={{ fontSize: 11, fill: '#94a3b8' }} />
              <YAxis
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                width={50}
                tickFormatter={v => '$' + v.toFixed(0)}
              />
              <Tooltip content={<ChartTooltip type="eps" />} />
              <Legend
                iconType="circle"
                iconSize={8}
                wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
              />
              <Bar
                dataKey="eps"
                name="EPS (주당순이익)"
                radius={[4, 4, 0, 0]}
                fillOpacity={0.8}
              >
                {epsData.map((entry, index) => (
                  <Cell key={index} fill={entry.eps >= 0 ? COLORS.eps : '#ef4444'} />
                ))}
              </Bar>
              {/* EPS 트렌드 라인 */}
              <Line
                type="monotone"
                dataKey="eps"
                name="EPS 추세"
                stroke={COLORS.eps}
                strokeWidth={2}
                strokeDasharray="5 3"
                dot={false}
                legendType="none"
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
