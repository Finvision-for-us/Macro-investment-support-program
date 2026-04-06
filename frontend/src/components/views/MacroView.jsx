import React, { useState, useEffect } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, BarChart, Bar, Cell } from 'recharts'
import { Sparkles, CheckCircle2, XCircle, MinusCircle, TrendingUp, TrendingDown, Minus, ArrowUpCircle, ArrowDownCircle } from 'lucide-react'
import { macroAPI } from '../../api/index'
import NewsFeed from '../shared/NewsFeed'
import CycleDiagram from '../shared/CycleDiagram'

const DAILY_KEYS = new Set(['DFF', 'T10YIE', 'T10Y2Y'])
const WEEKLY_KEYS = new Set(['ICSA'])

const INDICATOR_KEYS = [
  { key: 'GDP',      label: 'GDP',              color: '#6366f1' },
  { key: 'UNRATE',   label: '실업률',            color: '#ef4444' },
  { key: 'CPIAUCSL', label: 'CPI (물가지수)',    color: '#f97316' },
  { key: 'DFF',      label: '기준금리',          color: '#10b981' },
  { key: 'UMCSENT',  label: '소비자심리지수',    color: '#3b82f6' },
  { key: 'INDPRO',   label: '산업생산지수',      color: '#8b5cf6' },
  { key: 'T10Y2Y',   label: '장단기 금리차',     color: '#14b8a6' },
  { key: 'ICSA',     label: '신규 실업수당',      color: '#f43f5e' },
  { key: 'HOUST',    label: '주택착공건수',       color: '#a855f7' },
  { key: 'TCU',      label: '설비가동률',         color: '#0ea5e9' },
  { key: 'PCE',      label: 'PCE',              color: '#84cc16' },
  { key: 'T10YIE',   label: '기대인플레이션',     color: '#eab308' },
]

function getChartLimit(key) {
  if (DAILY_KEYS.has(key)) return 500   // ~2년
  if (WEEKLY_KEYS.has(key)) return 104  // ~2년
  return 40                              // 월간/분기 ~3년
}

const PHASE_COLORS = {
  1: '#64748b', 2: '#10b981', 3: '#22c55e', 4: '#f59e0b',
  5: '#f97316', 6: '#ef4444', 7: '#e11d48', 8: '#9f1239',
}

const INDICATOR_LABELS = {
  GDP: 'GDP YoY', UNRATE: '실업률', CPIAUCSL: 'CPI YoY', DFF: '기준금리',
  PCE: 'PCE YoY', UMCSENT: '소비자심리', INDPRO: '산업생산 YoY', T10YIE: '기대인플레',
  T10Y2Y: '장단기 금리차', ICSA: '실업수당 청구', HOUST: '주택착공', TCU: '설비가동률',
}

function IndicatorCard({ data }) {
  const val = data?.value
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <p className="text-xs text-slate-500">{data?.name}</p>
      <p className="text-lg font-bold text-slate-800 mt-1">
        {val != null ? val.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '-'}
        <span className="text-xs text-slate-400 ml-1">{data?.unit}</span>
      </p>
      <p className="text-xs text-slate-400 mt-0.5">{data?.date}</p>
    </div>
  )
}

function MiniChart({ seriesId, color, limit = 40 }) {
  const [data, setData] = useState([])

  useEffect(() => {
    macroAPI.getIndicator(seriesId, limit)
      .then(r => setData(r.data.data?.filter(d => d.value != null) || []))
      .catch(() => {})
  }, [seriesId, limit])

  if (!data.length) return <div className="h-32 flex items-center justify-center text-slate-400 text-xs">로딩 중...</div>

  return (
    <ResponsiveContainer width="100%" height={130}>
      <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`g${seriesId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.15} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
        <XAxis dataKey="date" hide />
        <YAxis hide domain={['auto', 'auto']} />
        <Tooltip
          contentStyle={{ background: '#ffffff', border: '1px solid #e2e8f0', fontSize: 11, borderRadius: 8 }}
          formatter={v => [v?.toFixed(2), '']}
          labelFormatter={l => l}
        />
        <Area type="monotone" dataKey="value" stroke={color} strokeWidth={1.5} fill={`url(#g${seriesId})`} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

const TYPE_LABELS = { leading: '선행', coincident: '동행', lagging: '후행' }
const TYPE_COLORS = { leading: 'text-blue-500', coincident: 'text-slate-600', lagging: 'text-amber-500' }

function TrendIcon({ trend, size = 12 }) {
  if (trend === 'up') return <TrendingUp size={size} className="text-emerald-500" />
  if (trend === 'down') return <TrendingDown size={size} className="text-red-400" />
  return <Minus size={size} className="text-slate-300" />
}

function ZScoreBar({ score }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  const color = pct >= 70 ? 'bg-emerald-400' : pct >= 40 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function IndicatorMatchGrid({ matchingIndicators }) {
  if (!matchingIndicators) return null
  const entries = Object.entries(matchingIndicators)

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <p className="text-xs font-medium text-slate-500 mb-1">지표 패턴 매칭 결과</p>
      <p className="text-[10px] text-slate-400 mb-3">Z-score = 패턴 중심과의 유사도 | 트렌드 = 최근 방향성</p>
      <div className="grid grid-cols-[14px_16px_100px_60px_120px_64px_24px_24px_1fr] gap-x-2 gap-y-1.5 items-center text-xs">
        {/* 헤더 */}
        <span />
        <span className="text-[9px] text-slate-400">분류</span>
        <span className="text-[9px] text-slate-400">지표</span>
        <span className="text-[9px] text-slate-400 text-right">현재값</span>
        <span className="text-[9px] text-slate-400 text-center">패턴 범위</span>
        <span className="text-[9px] text-slate-400 text-center">유사도</span>
        <span className="text-[9px] text-slate-400 text-center">현재</span>
        <span className="text-[9px] text-slate-400 text-center">기대</span>
        <span className="text-[9px] text-slate-400">트렌드</span>

        {entries.map(([id, info]) => (
          <React.Fragment key={id}>
            {/* 매칭 아이콘 */}
            {info.no_pattern ? (
              <MinusCircle size={13} className="text-slate-300" />
            ) : info.match ? (
              <CheckCircle2 size={13} className="text-emerald-500" />
            ) : (
              <XCircle size={13} className="text-red-400" />
            )}
            {/* 지표 유형 */}
            <span className={`text-[9px] font-medium ${TYPE_COLORS[info.indicator_type] || 'text-slate-400'}`}>
              {TYPE_LABELS[info.indicator_type] || '-'}
            </span>
            {/* 지표명 */}
            <span className="text-slate-700 truncate">{INDICATOR_LABELS[id] || id}</span>
            {/* 현재값 */}
            <span className="font-medium text-slate-800 text-right">
              {info.value != null ? info.value : '-'}
            </span>
            {/* 패턴 범위 */}
            <span className="text-slate-400 text-center">
              {info.pattern_range ? `[${info.pattern_range[0]} ~ ${info.pattern_range[1]}]` : '-'}
            </span>
            {/* Z-score 바 */}
            <div className="flex justify-center">
              <ZScoreBar score={info.z_score} />
            </div>
            {/* 현재 트렌드 */}
            <div className="flex justify-center">
              <TrendIcon trend={info.trend} />
            </div>
            {/* 기대 트렌드 */}
            <div className="flex justify-center">
              <TrendIcon trend={info.expected_trend} />
            </div>
            {/* 트렌드 매칭 */}
            <span className={`text-[10px] ${
              info.trend_match === true ? 'text-emerald-500' :
              info.trend_match === false ? 'text-red-400' : 'text-slate-300'
            }`}>
              {info.trend_match === true ? '일치' : info.trend_match === false ? '불일치' : '-'}
            </span>
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}

function PhaseScoreChart({ allPhases, currentPhase }) {
  if (!allPhases?.length) return null

  const data = allPhases.map(p => ({
    name: p.name,
    score: p.score,
    phase: p.phase,
  }))

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
      <p className="text-xs font-medium text-slate-500 mb-2">Phase별 매칭 점수</p>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={data} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
          <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-20} textAnchor="end" height={40} />
          <YAxis hide />
          <Tooltip
            contentStyle={{ background: '#ffffff', border: '1px solid #e2e8f0', fontSize: 11, borderRadius: 8 }}
            formatter={(v, _, props) => [`${(v * 100).toFixed(1)}%`, props.payload.name]}
          />
          <Bar dataKey="score" radius={[4, 4, 0, 0]}>
            {data.map((entry) => (
              <Cell
                key={entry.phase}
                fill={PHASE_COLORS[entry.phase]}
                opacity={entry.phase === currentPhase ? 1 : 0.4}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function MacroView() {
  const [overview, setOverview] = useState({})
  const [cycleState, setCycleState] = useState(null)
  const [cycleLoading, setCycleLoading] = useState(true)
  const [aiResult, setAiResult] = useState(null)
  const [aiLoading, setAiLoading] = useState(false)

  useEffect(() => {
    macroAPI.getOverview().then(r => setOverview(r.data.indicators || {})).catch(() => {})
    macroAPI.getMarketState()
      .then(r => setCycleState(r.data))
      .catch(() => {})
      .finally(() => setCycleLoading(false))
  }, [])

  const handleAi = () => {
    setAiLoading(true)
    macroAPI.aiAnalyze()
      .then(r => setAiResult(r.data))
      .finally(() => setAiLoading(false))
  }

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-slate-800">거시경제 대시보드</h2>
        <button
          onClick={handleAi}
          disabled={aiLoading}
          className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 border border-indigo-200 text-indigo-600 rounded-lg text-sm hover:bg-indigo-100 transition-all disabled:opacity-50"
        >
          <Sparkles size={14} />
          {aiLoading ? 'AI 분석 중...' : 'AI 시장 분석'}
        </button>
      </div>

      {/* 경기 사이클 포지셔닝 */}
      {cycleLoading ? (
        <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-slate-400 text-sm">
          경기 사이클 분석 중... (첫 로딩 시 10~15초 소요)
        </div>
      ) : cycleState && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
          <p className="text-xs font-medium text-slate-500 mb-4">경기 사이클 포지셔닝</p>
          <div className="grid grid-cols-[300px_1fr] gap-6">
            {/* 왼쪽: 원형 다이어그램 */}
            <div className="flex flex-col items-center">
              <CycleDiagram
                currentPhase={cycleState.phase}
                confidence={cycleState.confidence}
                phaseScores={cycleState.all_phases}
              />
            </div>

            {/* 오른쪽: 섹터 + 점수 차트 */}
            <div className="space-y-4">
              {/* 섹터 추천 */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-slate-500 mb-1">유리한 섹터</p>
                  <div className="flex flex-wrap gap-1">
                    {cycleState.recommended_sectors?.map(s => (
                      <span key={s} className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded">{s}</span>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">주의 섹터</p>
                  <div className="flex flex-wrap gap-1">
                    {cycleState.caution_sectors?.map(s => (
                      <span key={s} className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded">{s}</span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Phase 점수 차트 */}
              <PhaseScoreChart allPhases={cycleState.all_phases} currentPhase={cycleState.phase} />
            </div>
          </div>

          {/* 지표 매칭 그리드 */}
          <div className="mt-4">
            <IndicatorMatchGrid matchingIndicators={cycleState.matching_indicators} />
          </div>
        </div>
      )}

      {/* AI 분석 결과 */}
      {aiResult && (
        <div className={`p-4 rounded-xl text-xs leading-relaxed border whitespace-pre-wrap ${
          aiResult.status === 'disabled'
            ? 'bg-amber-50 text-amber-700 border-amber-200'
            : aiResult.status === 'success'
            ? 'bg-indigo-50 text-slate-600 border-indigo-200'
            : 'bg-red-50 text-red-600 border-red-200'
        }`}>
          {aiResult.analysis || aiResult.message}
        </div>
      )}

      {/* 주요 지표 카드 */}
      <div className="grid grid-cols-4 gap-3">
        {INDICATOR_KEYS.map(({ key }) => (
          <IndicatorCard key={key} data={overview[key]} />
        ))}
      </div>

      {/* 지표 차트 */}
      <div className="grid grid-cols-3 gap-4">
        {INDICATOR_KEYS.map(({ key, label, color }) => (
          <div key={key} className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
            <p className="text-xs font-medium text-slate-500 mb-2">{label}</p>
            <MiniChart seriesId={key} color={color} limit={getChartLimit(key)} />
          </div>
        ))}
      </div>

      {/* 거시 뉴스 */}
      <NewsFeed ticker={null} />
    </div>
  )
}
