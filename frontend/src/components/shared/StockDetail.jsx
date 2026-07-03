import { useState, useEffect, useRef, useMemo, lazy, Suspense } from 'react'
import { createPortal } from 'react-dom'
import { TrendingUp, TrendingDown, CalendarDays, HelpCircle, Star, BarChart3, X, Check, ChevronDown, Users, Scale, Calendar, Target, FlaskConical, FileText, Newspaper } from 'lucide-react'
import { ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts'
import PriceChart from './PriceChart'
import { stockAPI } from '../../api/index'

/* ── 지연 로딩: 스크롤 시 로드 (below-fold 컴포넌트) ── */
const FilingList = lazy(() => import('./FilingList'))
const NewsFeed = lazy(() => import('./NewsFeed'))
const EarningsSimulator = lazy(() => import('./EarningsSimulator'))
const AnalystVsAI = lazy(() => import('./AnalystVsAI'))
const CompetitorComparison = lazy(() => import('./CompetitorComparison'))
const EarningsCalendar = lazy(() => import('./EarningsCalendar'))
const GuidanceAccuracy = lazy(() => import('./GuidanceAccuracy'))
const StockResearchChat = lazy(() => import('./StockResearchChat'))

/* ── IntersectionObserver 기반 지연 렌더링 래퍼 ── */
function LazySection({ children, fallback, rootMargin = '200px' }) {
  const ref = useRef(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setVisible(true); observer.disconnect() } },
      { rootMargin }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [rootMargin])

  return (
    <div ref={ref}>
      {visible ? (
        <Suspense fallback={fallback || <div className="h-32 bg-white rounded-xl border border-slate-200 animate-pulse" />}>
          {children}
        </Suspense>
      ) : (
        fallback || <div className="h-32 bg-white rounded-xl border border-slate-200 animate-pulse" />
      )}
    </div>
  )
}

/* ── 접기/펴기 섹션 래퍼 ── */
function CollapsibleSection({ title, icon: Icon, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between bg-white rounded-2xl border border-slate-200 shadow-sm px-5 py-3.5 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          {Icon && <Icon size={15} className="text-indigo-400" />}
          <span className="text-sm font-semibold text-slate-700">{title}</span>
        </div>
        <ChevronDown size={16} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  )
}

/* ── 기업 소개 (번역 + 펼치기/접기) ── */
function DescriptionBlock({ ticker, text }) {
  const [expanded, setExpanded] = useState(false)
  const [translated, setTranslated] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setExpanded(false)
    setTranslated(null)
  }, [ticker])

  useEffect(() => {
    if (!expanded || translated || loading) return
    setLoading(true)
    stockAPI.translateText(text)
      .then(res => setTranslated(res.data?.translated || text))
      .catch(() => setTranslated(text))
      .finally(() => setLoading(false))
  }, [expanded, text, translated, loading])

  return (
    <div className="mt-4">
      <div className={`text-xs text-slate-400 leading-relaxed ${expanded ? '' : 'line-clamp-2'}`}>
        {expanded ? (loading ? '번역 중...' : translated || text) : text}
      </div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 mt-1 text-[10px] text-indigo-400 hover:text-indigo-600 transition-colors"
      >
        <span>{expanded ? '접기' : '더보기 (한국어)'}</span>
        <ChevronDown size={12} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
    </div>
  )
}

/* ── 숫자 포매팅 ── */
function fmt(n, prefix = '') {
  if (n == null) return '-'
  if (Math.abs(n) >= 1e12) return prefix + (n / 1e12).toFixed(2) + 'T'
  if (Math.abs(n) >= 1e9)  return prefix + (n / 1e9).toFixed(2) + 'B'
  if (Math.abs(n) >= 1e6)  return prefix + (n / 1e6).toFixed(2) + 'M'
  return prefix + n.toLocaleString()
}

/* ── 지표 메타데이터 ── */
const METRIC_INFO = {
  market_cap:       { label: '시가총액',     desc: '기업의 총 주식 가치. 현재 주가 × 발행 주식 수로 계산됩니다.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  pe_ratio:         { label: 'PER',          desc: '주가수익비율. 주가를 주당순이익(EPS)으로 나눈 값. 낮을수록 저평가 가능성.', avgKey: 'pe', unit: '배', format: v => v?.toFixed(1) },
  forward_pe:       { label: 'Forward PE',   desc: '향후 12개월 예상 EPS 기준 PER. 미래 수익 전망 반영.', avgKey: 'pe', unit: '배', format: v => v?.toFixed(1) },
  eps:              { label: 'EPS',          desc: '주당순이익. 순이익 ÷ 발행 주식 수. 기업의 수익력 지표.', avgKey: null, unit: '$', format: v => `$${v?.toFixed(2)}` },
  pb_ratio:         { label: 'PBR',          desc: '주가순자산비율. 주가 ÷ 주당순자산. 1 미만이면 자산 대비 저평가.', avgKey: 'pb', unit: '배', format: v => v?.toFixed(2) },
  dividend_yield:   { label: '배당수익률',   desc: '주가 대비 연간 배당금 비율. 안정적 현금 수익 지표.', avgKey: 'dividend_yield', unit: '%', format: v => (v * 100).toFixed(2) + '%', isPercent: true },
  '52w_high':       { label: '52주 고가',    desc: '최근 52주간 최고 거래 가격.', avgKey: null, unit: '$', format: v => `$${Number(v).toFixed(2)}` },
  '52w_low':        { label: '52주 저가',    desc: '최근 52주간 최저 거래 가격.', avgKey: null, unit: '$', format: v => `$${Number(v).toFixed(2)}` },
  roe:              { label: 'ROE',          desc: '자기자본이익률. 순이익 ÷ 자기자본. 주주 자본 대비 수익 창출 효율.', avgKey: 'roe', unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  roa:              { label: 'ROA',          desc: '총자산이익률. 순이익 ÷ 총자산. 자산 활용 효율성 지표.', avgKey: 'roa', unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  debt_to_equity:   { label: '부채비율',     desc: '총부채 ÷ 자기자본. 재무 건전성 핵심 지표. 낮을수록 안전.', avgKey: 'debt_to_equity', unit: '배', format: v => v?.toFixed(2) },
  current_ratio:    { label: '유동비율',     desc: '유동자산 ÷ 유동부채. 1 이상이면 단기 채무 상환 가능.', avgKey: 'current_ratio', unit: '배', format: v => v?.toFixed(2) },
  operating_margin: { label: '영업이익률',   desc: '영업이익 ÷ 매출. 본업의 수익성을 나타냄.', avgKey: 'operating_margin', unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  gross_margin:     { label: '매출총이익률', desc: '매출총이익 ÷ 매출. 원가 경쟁력 지표.', avgKey: null, unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  profit_margin:    { label: '순이익률',     desc: '순이익 ÷ 매출. 최종 수익성 지표.', avgKey: 'profit_margin', unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  revenue_growth:   { label: '매출성장률',   desc: '전년 대비 매출 증가율. 성장 속도 지표.', avgKey: null, unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  fcf:              { label: '잉여현금흐름', desc: '영업활동 현금흐름 - 자본적 지출. 실제 사용 가능한 현금.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  total_revenue:    { label: '총매출',       desc: '기업의 총 매출액.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  total_debt:       { label: '총부채',       desc: '기업이 보유한 총 부채 규모.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  total_cash:       { label: '보유현금',     desc: '기업이 보유한 현금 및 현금성 자산.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  ebitda:           { label: 'EBITDA',       desc: '이자·세금·감가상각 전 이익. 기업 본질적 수익력 비교 지표.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  ev_to_ebitda:     { label: 'EV/EBITDA',    desc: '기업가치 ÷ EBITDA. 기업 인수 시 투자 회수 기간 추정 지표.', avgKey: null, unit: '배', format: v => v?.toFixed(1) },
  beta:             { label: '베타',         desc: '시장 대비 주가 변동성. 1보다 크면 시장보다 변동 큼.', avgKey: null, unit: '', format: v => v?.toFixed(2) },
  target_mean:      { label: '목표가(평균)', desc: '월가 애널리스트 평균 목표 주가.', avgKey: null, unit: '$', format: v => `$${v?.toFixed(2)}` },
  // ── 확장 지표 ──
  net_income:       { label: '당기순이익',       desc: '매출에서 모든 비용을 차감한 최종 이익. 기업의 실질적 수익력.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  pretax_income:    { label: '세전이익(EBT)',    desc: '세금 차감 전 이익. 세금 영향을 배제한 수익성 비교에 유용.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  ebit:             { label: 'EBIT',             desc: '이자·세금 차감 전 이익. 자본 구조와 세율 차이를 배제한 본업 수익력.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  interest_expense: { label: '이자비용',         desc: '부채에 대한 이자 지급액. 재무 부담 수준을 나타냄.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  tax_rate:         { label: '유효세율',         desc: '실제 납부 세금 비율 (1 - 순이익/세전이익). 세금 효율성 지표.', avgKey: 'tax_rate', unit: '%', format: v => v?.toFixed(1) + '%' },
  bps:              { label: 'BPS',              desc: '주당순자산가치. 자기자본 ÷ 발행주식수. 기업의 장부가치 기준 가치.', avgKey: null, unit: '$', format: v => `$${v?.toFixed(2)}` },
  accounts_receivable: { label: '매출채권',      desc: '아직 수금하지 못한 매출금. 매출의 질과 현금화 속도를 나타냄.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  inventory:        { label: '재고자산',         desc: '판매 대기 중인 재고의 가치. 재고 관리 효율성 지표.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  accounts_payable: { label: '매입채무',         desc: '아직 지급하지 않은 구매대금. 현금흐름 관리와 협상력 지표.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  working_capital:  { label: '운전자본',         desc: '유동자산 - 유동부채. 단기 영업 활동에 필요한 순 자금.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  tangible_book:    { label: '유형순자산',       desc: '무형자산을 제외한 순자산. 실질적 자산 가치 판단 기준.', avgKey: null, unit: '$', format: v => fmt(v, '$') },
  asset_turnover:   { label: '자산회전율',       desc: '매출 ÷ 총자산. 자산을 얼마나 효율적으로 활용해 매출을 창출하는지.', avgKey: 'asset_turnover', unit: '회', format: v => v?.toFixed(2) },
  inventory_turnover: { label: '재고회전율',     desc: '매출 ÷ 재고. 재고가 얼마나 빠르게 판매되는지. 높을수록 효율적.', avgKey: 'inventory_turnover', unit: '회', format: v => v?.toFixed(1) },
  receivables_turnover: { label: '매출채권회전율', desc: '매출 ÷ 매출채권. 외상 매출 수금 속도. 높을수록 현금화 빠름.', avgKey: 'receivables_turnover', unit: '회', format: v => v?.toFixed(1) },
  roic:             { label: 'ROIC',             desc: '투하자본수익률. 투자된 자본 대비 세후 영업이익. 자본 효율성 핵심 지표.', avgKey: 'roic', unit: '%', format: v => v?.toFixed(1) + '%' },
  ocf_margin:       { label: '영업CF마진',       desc: '영업현금흐름 ÷ 매출. 실제 현금 창출력. 이익의 질을 평가.', avgKey: 'ocf_margin', unit: '%', format: v => (v * 100).toFixed(1) + '%', isPercent: true },
  capex_to_revenue: { label: '설비투자비율',     desc: '자본적 지출 ÷ 매출. 성장을 위한 재투자 수준.', avgKey: null, unit: '%', format: v => v?.toFixed(1) + '%' },
  revenue_per_share: { label: '주당매출',        desc: '총매출 ÷ 발행주식수. 주당 기준 매출 규모.', avgKey: null, unit: '$', format: v => `$${v?.toFixed(2)}` },
  dividend_per_share: { label: '주당배당금',     desc: '연간 주당 배당금. 배당 투자 시 핵심 지표.', avgKey: null, unit: '$', format: v => `$${v?.toFixed(2)}` },
  payout_ratio:     { label: '배당성향',         desc: '배당금 ÷ 순이익. 이익 중 배당으로 지급하는 비율.', avgKey: 'payout_ratio', unit: '%', format: v => v?.toFixed(1) + '%' },
  eps_growth:       { label: 'EPS성장률',        desc: 'EPS의 전년 대비 변화율. 수익 성장 속도.', avgKey: null, unit: '%', format: v => v?.toFixed(1) + '%' },
  net_income_growth: { label: '순이익성장률',    desc: '순이익의 전년 대비 변화율.', avgKey: null, unit: '%', format: v => v?.toFixed(1) + '%' },
  operating_income_growth: { label: '영업이익성장률', desc: '영업이익의 전년 대비 변화율. 본업 성장 추이.', avgKey: null, unit: '%', format: v => v?.toFixed(1) + '%' },
}

/* 메트릭 그룹 정의 */
const METRIC_GROUPS = [
  { title: '가치 평가',    keys: ['market_cap', 'pe_ratio', 'forward_pe', 'eps', 'pb_ratio', 'bps', 'ev_to_ebitda'] },
  { title: '수익성',       keys: ['profit_margin', 'operating_margin', 'gross_margin', 'roe', 'roa', 'roic'] },
  { title: '수익구조',     keys: ['total_revenue', 'net_income', 'ebit', 'ebitda', 'pretax_income', 'interest_expense', 'tax_rate'] },
  { title: '재무 건전성',  keys: ['debt_to_equity', 'current_ratio', 'total_debt', 'total_cash', 'working_capital'] },
  { title: '효율성',       keys: ['asset_turnover', 'inventory_turnover', 'receivables_turnover', 'ocf_margin', 'capex_to_revenue', 'fcf'] },
  { title: '주당지표',     keys: ['revenue_per_share', 'dividend_per_share', 'dividend_yield', 'payout_ratio'] },
  { title: '성장률',       keys: ['revenue_growth', 'eps_growth', 'net_income_growth', 'operating_income_growth'] },
  { title: '자산 항목',    keys: ['accounts_receivable', 'inventory', 'accounts_payable', 'tangible_book'] },
  { title: '시장',         keys: ['beta', '52w_high', '52w_low', 'target_mean'] },
]

/* ── 차트 색상 팔레트 ── */
const CHART_COLORS = ['#6366f1', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

/* ── Portal 기반 툴팁 (잘림 방지) ── */
function MetricTooltip({ label, desc }) {
  const [show, setShow] = useState(false)
  const [pos, setPos] = useState({ top: 0, left: 0 })
  const iconRef = useRef(null)

  const handleEnter = () => {
    if (iconRef.current) {
      const rect = iconRef.current.getBoundingClientRect()
      setPos({
        top: rect.top - 8,
        left: rect.left + rect.width / 2,
      })
    }
    setShow(true)
  }

  return (
    <span className="relative inline-block ml-1">
      <HelpCircle
        ref={iconRef}
        size={12}
        className="text-slate-400 hover:text-indigo-500 cursor-help transition-colors inline-block align-text-top"
        onMouseEnter={handleEnter}
        onMouseLeave={() => setShow(false)}
      />
      {show && createPortal(
        <div
          style={{ top: pos.top, left: pos.left, transform: 'translate(-50%, -100%)' }}
          className="fixed w-72 bg-white border border-slate-200 rounded-xl shadow-xl p-3.5 z-[9999] text-left pointer-events-none"
        >
          <p className="text-xs font-bold text-slate-800 mb-1">{label}</p>
          <p className="text-[11px] text-slate-500 leading-relaxed">{desc}</p>
          <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-px">
            <div className="w-2.5 h-2.5 bg-white border-r border-b border-slate-200 rotate-45" />
          </div>
        </div>,
        document.body
      )}
    </span>
  )
}

/* ── 지표 카드 컴포넌트 ── */
function MetricCard({ metricKey, value, overview, isKeyMetric, isSelected, onClick, aiReason }) {
  const info = METRIC_INFO[metricKey]
  if (!info) return null
  const isChartable = CHARTABLE_METRICS.has(metricKey)
  // 차트 가능한 지표는 overview 값 없어도 표시 (히스토리에서만 확인 가능)
  if (value == null && !isChartable) return null

  const displayValue = value != null ? info.format(value) : '-'

  return (
    <div
      onClick={() => isChartable && onClick?.(metricKey)}
      className={`text-left p-3 rounded-xl border transition-all duration-200 group relative ${
        isChartable ? 'cursor-pointer' : 'cursor-default'
      } ${
        isSelected
          ? 'bg-indigo-50 border-indigo-300 ring-2 ring-indigo-200'
          : isKeyMetric
          ? 'bg-amber-50/50 border-amber-200 hover:border-amber-300 hover:shadow-sm'
          : isChartable
          ? 'bg-white border-slate-200 hover:border-indigo-200 hover:shadow-sm'
          : 'bg-slate-50/50 border-slate-200'
      }`}
    >
      {isKeyMetric && (
        <Star size={10} className="absolute top-2 right-2 text-amber-400 fill-amber-400" title={aiReason || ''} />
      )}
      <div className="flex items-start gap-2">
        {/* 체크박스 (차트 가능한 지표만) */}
        {isChartable && (
          <div className={`mt-0.5 w-4 h-4 rounded flex-shrink-0 flex items-center justify-center border transition-all ${
            isSelected
              ? 'bg-indigo-500 border-indigo-500'
              : 'border-slate-300 group-hover:border-indigo-400'
          }`}>
            {isSelected && <Check size={10} className="text-white" strokeWidth={3} />}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[11px] text-slate-500 flex items-center gap-0.5">
            {info.label}
            <MetricTooltip
              label={info.label}
              desc={info.desc}
            />
          </p>
          <p className={`text-sm font-bold mt-1 ${isSelected ? 'text-indigo-700' : 'text-slate-800'}`}>
            {displayValue}
          </p>
        </div>
      </div>
    </div>
  )
}

/* ── 지표 히스토리 기간 ── */
const METRIC_PERIODS = [
  { label: '1년',   value: '1y',  months: 12 },
  { label: '3년',   value: '3y',  months: 36 },
  { label: '5년',   value: '5y',  months: 60 },
  { label: '10년',  value: '10y', months: 120 },
  { label: '전체',  value: 'max', months: Infinity },
]

/* ── 지표 차트 타입 결정: 절대값($) = 막대, 비율(%) = 선 ── */
const METRIC_CHART_TYPE = {
  // 절대값 → 막대그래프 (왼쪽 Y축)
  market_cap: 'bar', total_revenue: 'bar', fcf: 'bar', ebitda: 'bar',
  total_debt: 'bar', total_cash: 'bar', eps: 'bar', revenue_growth: 'bar',
  net_income: 'bar', pretax_income: 'bar', ebit: 'bar', interest_expense: 'bar',
  bps: 'bar', accounts_receivable: 'bar', inventory: 'bar', accounts_payable: 'bar',
  working_capital: 'bar', tangible_book: 'bar',
  revenue_per_share: 'bar', dividend_per_share: 'bar',
  // 비율 → 선그래프 (오른쪽 Y축)
  pe_ratio: 'line', forward_pe: 'line', pb_ratio: 'line', ev_to_ebitda: 'line',
  profit_margin: 'line', operating_margin: 'line', gross_margin: 'line',
  roe: 'line', roa: 'line', debt_to_equity: 'line', current_ratio: 'line',
  dividend_yield: 'line', beta: 'line',
  tax_rate: 'line', asset_turnover: 'line', inventory_turnover: 'line',
  receivables_turnover: 'line', roic: 'line', ocf_margin: 'line',
  capex_to_revenue: 'line', payout_ratio: 'line',
  eps_growth: 'line', net_income_growth: 'line', operating_income_growth: 'line',
  '52w_high': 'bar', '52w_low': 'bar', target_mean: 'bar',
}

/* ── 차트에 올릴 수 있는 지표 (히스토리 데이터가 있는 것만) ── */
const CHARTABLE_METRICS = new Set([
  'total_revenue', 'revenue_growth', 'profit_margin', 'operating_margin',
  'gross_margin', 'roe', 'roa', 'debt_to_equity', 'current_ratio',
  'fcf', 'eps', 'ebitda', 'total_debt', 'total_cash',
  'pe_ratio', 'pb_ratio', 'market_cap', 'ev_to_ebitda', 'dividend_yield',
  // 확장 지표
  'net_income', 'pretax_income', 'ebit', 'interest_expense', 'tax_rate',
  'bps', 'accounts_receivable', 'inventory', 'accounts_payable',
  'working_capital', 'tangible_book',
  'asset_turnover', 'inventory_turnover', 'receivables_turnover',
  'roic', 'ocf_margin', 'capex_to_revenue',
  'revenue_per_share', 'dividend_per_share', 'payout_ratio',
  'eps_growth', 'net_income_growth', 'operating_income_growth',
])

/* ── 지표 히스토리 차트 ── */
function MetricHistoryChart({ ticker, selectedMetrics, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [period, setPeriod] = useState('max')
  const [focusedMetric, setFocusedMetric] = useState(null)

  useEffect(() => {
    if (!ticker || selectedMetrics.length === 0) return
    setLoading(true)
    stockAPI.getMetricHistory(ticker)
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [ticker])

  // 기간에 따라 분기/연간 자동 선택 (1y, 3y → 분기, 5y+ → 연간)
  const useQuarterly = period === '1y' || period === '3y'

  // 메트릭 키 → 히스토리 데이터 키 매핑 (연간 / 분기별)
  const METRIC_HISTORY_MAP_ANNUAL = {
    revenue_growth: 'revenue', total_revenue: 'revenue',
    profit_margin: 'profit_margin_hist', operating_margin: 'operating_margin_hist',
    gross_margin: 'gross_margin_hist', roe: 'roe_hist', roa: 'roa_hist',
    debt_to_equity: 'debt_to_equity_hist', current_ratio: 'current_ratio_hist',
    fcf: 'fcf_hist', eps: 'eps_hist', ebitda: 'ebitda_hist',
    total_debt: 'total_debt_hist', total_cash: 'total_cash_hist',
    pe_ratio: 'per_hist', pb_ratio: 'pbr_hist',
    market_cap: 'market_cap_hist', ev_to_ebitda: 'ev_to_ebitda_hist',
    dividend_yield: 'dividend_yield_hist',
    // 확장 지표
    net_income: 'net_income', pretax_income: 'pretax_income',
    ebit: 'ebit_hist', interest_expense: 'interest_expense_hist',
    tax_rate: 'tax_rate_hist', bps: 'bps_hist',
    accounts_receivable: 'accounts_receivable_hist',
    inventory: 'inventory_hist', accounts_payable: 'accounts_payable_hist',
    working_capital: 'working_capital_hist', tangible_book: 'tangible_book_hist',
    asset_turnover: 'asset_turnover_hist', inventory_turnover: 'inventory_turnover_hist',
    receivables_turnover: 'receivables_turnover_hist',
    roic: 'roic_hist', ocf_margin: 'ocf_margin_hist',
    capex_to_revenue: 'capex_to_revenue_hist',
    revenue_per_share: 'revenue_per_share_hist',
    dividend_per_share: 'dividend_per_share_hist', payout_ratio: 'payout_ratio_hist',
    eps_growth: 'eps_growth_hist', net_income_growth: 'net_income_growth_hist',
    operating_income_growth: 'operating_income_growth_hist',
    forward_pe: null, beta: null, '52w_high': null, '52w_low': null, target_mean: null,
  }
  const METRIC_HISTORY_MAP_QUARTERLY = {
    revenue_growth: 'revenue_quarterly', total_revenue: 'revenue_quarterly',
    profit_margin: 'profit_margin_quarterly', operating_margin: 'operating_margin_quarterly',
    gross_margin: 'gross_margin_quarterly', roe: 'roe_quarterly', roa: 'roa_quarterly',
    debt_to_equity: 'debt_to_equity_quarterly', current_ratio: 'current_ratio_quarterly',
    fcf: 'fcf_quarterly', eps: 'eps_quarterly', ebitda: 'ebitda_quarterly',
    total_debt: 'total_debt_quarterly', total_cash: 'total_cash_quarterly',
    pe_ratio: 'per_quarterly', pb_ratio: 'pbr_quarterly',
    market_cap: 'market_cap_quarterly', ev_to_ebitda: 'ev_to_ebitda_quarterly',
    dividend_yield: 'dividend_yield_quarterly',
    // 확장 지표
    net_income: 'net_income_quarterly', pretax_income: 'pretax_income_quarterly',
    ebit: 'ebit_quarterly', interest_expense: 'interest_expense_quarterly',
    tax_rate: 'tax_rate_quarterly', bps: 'bps_quarterly',
    accounts_receivable: 'accounts_receivable_quarterly',
    inventory: 'inventory_quarterly', accounts_payable: 'accounts_payable_quarterly',
    working_capital: 'working_capital_quarterly', tangible_book: 'tangible_book_quarterly',
    asset_turnover: 'asset_turnover_quarterly', inventory_turnover: 'inventory_turnover_quarterly',
    receivables_turnover: 'receivables_turnover_quarterly',
    roic: 'roic_quarterly', ocf_margin: 'ocf_margin_quarterly',
    capex_to_revenue: 'capex_to_revenue_quarterly',
    revenue_per_share: 'revenue_per_share_quarterly',
    dividend_per_share: 'dividend_per_share_quarterly', payout_ratio: 'payout_ratio_quarterly',
    eps_growth: 'eps_growth_quarterly', net_income_growth: 'net_income_growth_quarterly',
    operating_income_growth: 'operating_income_growth_quarterly',
    forward_pe: null, beta: null, '52w_high': null, '52w_low': null, target_mean: null,
  }

  const METRIC_HISTORY_MAP = useQuarterly ? METRIC_HISTORY_MAP_QUARTERLY : METRIC_HISTORY_MAP_ANNUAL

  // 사용 가능한 히스토리 데이터가 있는 메트릭만 필터
  const availableMetrics = selectedMetrics.filter(key => {
    const histKey = METRIC_HISTORY_MAP[key]
    if (histKey === undefined) {
      return data && data[key] && data[key].length > 0
    }
    if (histKey === null) return false
    return data && data[histKey] && data[histKey].length > 0
  })

  if (selectedMetrics.length === 0) return null

  // 차트 타입별 분류
  const barMetrics = availableMetrics.filter(k => (METRIC_CHART_TYPE[k] || 'bar') === 'bar')
  const lineMetrics = availableMetrics.filter(k => (METRIC_CHART_TYPE[k] || 'bar') === 'line')
  const hasBar = barMetrics.length > 0
  const hasLine = lineMetrics.length > 0

  // 기간 필터링
  const filterByPeriod = (items) => {
    if (!items || period === 'max') return items
    const periodInfo = METRIC_PERIODS.find(p => p.value === period)
    if (!periodInfo) return items
    const cutoff = new Date()
    cutoff.setMonth(cutoff.getMonth() - periodInfo.months)
    const cutoffStr = cutoff.toISOString().slice(0, 10)
    return items.filter(item => item.date >= cutoffStr)
  }

  // 차트 데이터 빌드
  const buildChartData = () => {
    if (!data) return []
    const allDates = new Set()
    const seriesMap = {}

    availableMetrics.forEach(key => {
      const histKey = METRIC_HISTORY_MAP[key] !== undefined ? METRIC_HISTORY_MAP[key] : key
      if (!histKey || !data[histKey]) return
      seriesMap[key] = {}
      const filtered = filterByPeriod(data[histKey])
      filtered.forEach(item => {
        allDates.add(item.date)
        seriesMap[key][item.date] = item.value
      })
    })

    const sortedDates = Array.from(allDates).sort()
    return sortedDates.map(date => {
      const point = { date: date.slice(0, 7) }
      availableMetrics.forEach(key => {
        point[key] = seriesMap[key]?.[date] ?? null
      })
      return point
    })
  }

  const chartData = buildChartData()

  const handleBadgeClick = (key) => {
    if (!availableMetrics.includes(key)) return
    setFocusedMetric(prev => prev === key ? null : key)
  }

  // 툴팁 포매터
  const tooltipFormatter = (val, name) => {
    const info = METRIC_INFO[name]
    const type = METRIC_CHART_TYPE[name] || 'bar'
    if (type === 'line') {
      // 비율 지표
      return [`${val?.toFixed(2)}${info?.unit === '%' ? '%' : ''}`, info?.label || name]
    }
    // 절대값 지표
    if (Math.abs(val) >= 1e12) return [`$${(val/1e12).toFixed(2)}T`, info?.label || name]
    if (Math.abs(val) >= 1e9) return [`$${(val/1e9).toFixed(2)}B`, info?.label || name]
    if (Math.abs(val) >= 1e6) return [`$${(val/1e6).toFixed(2)}M`, info?.label || name]
    return [val?.toLocaleString(), info?.label || name]
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      {/* 헤더: 제목 + 닫기 */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={16} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-800">지표 히스토리 차트</h3>
          <span className="text-[10px] text-slate-400">(배지 클릭→강조 / 체크 해제→제거)</span>
        </div>
        <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded-lg transition-colors">
          <X size={14} className="text-slate-400" />
        </button>
      </div>

      {/* 메트릭 배지 */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        {selectedMetrics.map((key, i) => {
          const info = METRIC_INFO[key]
          const hasData = availableMetrics.includes(key)
          const isFocused = focusedMetric === key
          const chartType = METRIC_CHART_TYPE[key] || 'bar'
          return (
            <button
              key={key}
              onClick={() => handleBadgeClick(key)}
              className={`text-[10px] px-2.5 py-1 rounded-full font-semibold transition-all duration-200 flex items-center gap-1 ${
                hasData
                  ? isFocused
                    ? 'text-white ring-2 ring-offset-1 scale-105 shadow-md'
                    : focusedMetric && !isFocused
                    ? 'text-white/70'
                    : 'text-white'
                  : 'text-slate-400 bg-slate-100 line-through cursor-default'
              }`}
              style={hasData ? {
                backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
                opacity: focusedMetric && !isFocused ? 0.5 : 1,
              } : {}}
              disabled={!hasData}
            >
              {hasData && <span className="text-[8px] opacity-75">{chartType === 'bar' ? '▮' : '〰'}</span>}
              {info?.label || key}
            </button>
          )
        })}
      </div>

      {/* 범례 설명 */}
      {hasBar && hasLine && (
        <div className="flex gap-4 mb-2 text-[10px] text-slate-400">
          <span>▮ 막대 = 절대값 (왼쪽 축)</span>
          <span>〰 선 = 비율 (오른쪽 축)</span>
        </div>
      )}

      {/* 기간 선택 버튼 */}
      <div className="flex items-center gap-2 mb-4">
        <div className="flex flex-wrap gap-1">
          {METRIC_PERIODS.map(p => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-2.5 py-1 text-[11px] rounded-lg font-medium transition-all ${
                period === p.value
                  ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-200'
                  : 'text-slate-400 hover:text-slate-600 hover:bg-slate-100'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
          useQuarterly ? 'bg-emerald-50 text-emerald-600 border border-emerald-200' : 'bg-slate-50 text-slate-400 border border-slate-200'
        }`}>
          {useQuarterly ? '분기별' : '연간'}
        </span>
      </div>

      {loading ? (
        <div className="h-48 flex items-center justify-center text-slate-400 text-sm">
          <div className="w-5 h-5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin mr-2" />
          로딩 중...
        </div>
      ) : chartData.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-slate-400 text-sm">
          {availableMetrics.length === 0 ? '선택한 지표의 히스토리 데이터가 없습니다' : '선택한 기간에 데이터가 없습니다'}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={chartData} margin={{ top: 5, right: hasLine ? 20 : 5, bottom: 5, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
            {/* 왼쪽 Y축: 절대값 (막대) 또는 선만 있을때 비율 */}
            <YAxis
              yAxisId="left"
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              width={70}
              tickFormatter={hasBar ? (v => {
                if (Math.abs(v) >= 1e12) return '$' + (v/1e12).toFixed(0) + 'T'
                if (Math.abs(v) >= 1e9) return '$' + (v/1e9).toFixed(0) + 'B'
                if (Math.abs(v) >= 1e6) return '$' + (v/1e6).toFixed(0) + 'M'
                return v.toLocaleString()
              }) : (v => v.toFixed(0) + '%')}
            />
            {/* 오른쪽 Y축: 비율 (막대+선 혼합일 때만) */}
            {hasBar && hasLine && (
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                width={50}
                tickFormatter={v => v.toFixed(0) + '%'}
              />
            )}
            <Tooltip
              contentStyle={{ borderRadius: 12, border: '1px solid #e2e8f0', fontSize: 12, boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}
              formatter={tooltipFormatter}
            />
            {/* 막대그래프 (절대값 지표) */}
            {barMetrics.map((key) => {
              const colorIdx = selectedMetrics.indexOf(key)
              const color = CHART_COLORS[colorIdx % CHART_COLORS.length]
              const isFocused = focusedMetric === key
              const hasFocus = focusedMetric !== null
              return (
                <Bar
                  key={key}
                  dataKey={key}
                  yAxisId="left"
                  fill={color}
                  fillOpacity={hasFocus ? (isFocused ? 0.85 : 0.15) : 0.7}
                  stroke={color}
                  strokeWidth={isFocused ? 2 : 0}
                  radius={[3, 3, 0, 0]}
                  name={key}
                />
              )
            })}
            {/* 선그래프 (비율 지표) */}
            {lineMetrics.map((key) => {
              const colorIdx = selectedMetrics.indexOf(key)
              const color = CHART_COLORS[colorIdx % CHART_COLORS.length]
              const isFocused = focusedMetric === key
              const hasFocus = focusedMetric !== null
              return (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  yAxisId={hasBar && hasLine ? 'right' : 'left'}
                  stroke={color}
                  strokeWidth={hasFocus ? (isFocused ? 4 : 1.5) : 2.5}
                  strokeOpacity={hasFocus ? (isFocused ? 1 : 0.25) : 1}
                  dot={hasFocus
                    ? (isFocused ? { r: 5, strokeWidth: 2, fill: '#fff' } : false)
                    : { r: 3.5, strokeWidth: 2, fill: '#fff' }
                  }
                  activeDot={isFocused || !hasFocus ? { r: 6 } : false}
                  connectNulls
                  name={key}
                />
              )
            })}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}


/* ── 프론트엔드 캐시 (5분 TTL) ── */
const _overviewCache = new Map()
const _CACHE_TTL = 5 * 60 * 1000

/* ── 스켈레톤 로딩 컴포넌트 ── */
function SkeletonCard() {
  return (
    <div className="animate-pulse space-y-5">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <div className="flex items-start justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-2.5">
              <div className="h-7 w-20 bg-slate-200 rounded-lg" />
              <div className="h-5 w-16 bg-indigo-100 rounded-lg" />
            </div>
            <div className="h-4 w-40 bg-slate-100 rounded" />
          </div>
          <div className="text-right space-y-1">
            <div className="h-8 w-28 bg-slate-200 rounded-lg" />
            <div className="h-4 w-24 bg-slate-100 rounded ml-auto" />
          </div>
        </div>
      </div>
      <div className="space-y-3">
        <div className="h-5 w-24 bg-slate-200 rounded" />
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2.5">
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="p-3 rounded-xl border border-slate-200 bg-white">
              <div className="h-3 w-12 bg-slate-100 rounded mb-2" />
              <div className="h-5 w-16 bg-slate-200 rounded" />
            </div>
          ))}
        </div>
      </div>
      <div className="h-64 bg-white rounded-2xl border border-slate-200 animate-pulse" />
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════
   메인 StockDetail 컴포넌트
   ══════════════════════════════════════════════════════════════════ */
export default function StockDetail({ ticker }) {
  const [overview, setOverview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [selectedMetrics, setSelectedMetrics] = useState([])
  const [aiKeyMetrics, setAiKeyMetrics] = useState(null) // AI 종목별 핵심지표
  const [showResearchChat, setShowResearchChat] = useState(false)

  useEffect(() => {
    if (!ticker) return
    setShowResearchChat(false)
    setSelectedMetrics([])

    // 캐시 확인
    const cached = _overviewCache.get(ticker.toUpperCase())
    if (cached && Date.now() - cached.ts < _CACHE_TTL) {
      setOverview(cached.data)
      setLoading(false)
      return
    }

    setLoading(true)
    // 캐시가 있으면 일단 표시 (stale-while-revalidate)
    if (cached) {
      setOverview(cached.data)
    } else {
      setOverview(null)
    }

    stockAPI.getOverview(ticker)
      .then(r => {
        setOverview(r.data)
        _overviewCache.set(ticker.toUpperCase(), { data: r.data, ts: Date.now() })
      })
      .catch(() => setOverview({ error: true }))
      .finally(() => setLoading(false))
  }, [ticker])

  // ── AI 종목별 핵심지표 가져오기 (competitors API에서) ──
  useEffect(() => {
    if (!ticker) return
    setAiKeyMetrics(null)
    stockAPI.getCompetitors(ticker)
      .then(r => {
        const metrics = r.data?.key_metrics
        if (metrics && metrics.length > 0) {
          setAiKeyMetrics(metrics)
        }
      })
      .catch(() => {})
  }, [ticker])

  // ── 30초마다 주가 자동 갱신 ──
  useEffect(() => {
    if (!ticker || !overview || overview.error) return
    const interval = setInterval(() => {
      stockAPI.getQuote(ticker)
        .then(r => {
          const q = r.data
          if (q.current_price != null) {
            setOverview(prev => prev ? { ...prev, current_price: q.current_price } : prev)
          }
        })
        .catch(() => {}) // 실패 시 무시 (다음 30초에 재시도)
    }, 30000)
    return () => clearInterval(interval)
  }, [ticker, overview?.ticker])

  const toggleMetric = (key) => {
    setSelectedMetrics(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    )
  }

  if (!ticker) return (
    <div className="flex items-center justify-center h-64 text-slate-400 text-sm">
      종목을 검색하거나 선택하세요.
    </div>
  )

  if (loading && !overview) return <SkeletonCard />

  const priceChange = overview?.current_price && overview?.['52w_low']
    ? ((overview.current_price - overview['52w_low']) / overview['52w_low'] * 100).toFixed(1)
    : null

  // AI 종목별 핵심지표 (있으면 AI, 없으면 섹터 기반 폴백)
  const keyMetrics = aiKeyMetrics
    ? aiKeyMetrics.map(m => m.metric)
    : (overview?.key_metrics || [])

  return (
    <div className="space-y-5">
      {/* ── 헤더 카드 ── */}
      {overview && !overview.error && (
        <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2.5">
                <h2 className="text-2xl font-extrabold text-slate-900">{ticker}</h2>
                {overview.sector && overview.sector !== '-' && (
                  <span className="text-xs bg-indigo-50 text-indigo-600 px-2.5 py-1 rounded-lg border border-indigo-100 font-medium">{overview.sector}</span>
                )}
                {overview.industry && overview.industry !== '-' && (
                  <span className="text-xs bg-slate-100 text-slate-500 px-2.5 py-1 rounded-lg font-medium">{overview.industry}</span>
                )}
              </div>
              <p className="text-slate-500 text-sm mt-1">
                {overview.name}
                {overview.country && overview.country !== '-' && (
                  <span className="text-slate-400 ml-2">· {overview.country}</span>
                )}
              </p>
            </div>
            <div className="text-right">
              <p className="text-3xl font-extrabold text-slate-900">${overview.current_price?.toFixed(2) ?? '-'}</p>
              {priceChange && (
                <div className={`flex items-center gap-1 justify-end text-sm mt-1 font-medium ${parseFloat(priceChange) >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                  {parseFloat(priceChange) >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  52주 저가 대비 {priceChange}%
                </div>
              )}
            </div>
          </div>

          {/* 실적 발표일 */}
          {overview.earnings_date && (
            <div className="mt-4 flex items-center gap-2.5 bg-gradient-to-r from-amber-50 to-orange-50 border border-amber-200 rounded-xl px-4 py-3">
              <CalendarDays size={16} className="text-amber-600" />
              <span className="text-sm text-amber-800 font-semibold">
                다음 실적 발표: {overview.earnings_date}
              </span>
            </div>
          )}

          {overview.description && <DescriptionBlock ticker={ticker} text={overview.description} />}
        </div>
      )}

      {/* ── 주가 차트 ── */}
      <PriceChart ticker={ticker} />

      {/* ── 재무 지표 그리드 (그룹별, 접기 가능) ── */}
      {overview && !overview.error && (
        <CollapsibleSection title="재무 지표" icon={BarChart3} defaultOpen={false}>
          <div className="space-y-4">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-[10px] text-slate-400">(☑ 체크하면 히스토리 차트에 추가)</span>
              <div className="flex items-center gap-3">
                {keyMetrics.length > 0 && (
                  <div className="flex items-center gap-1 text-[10px] text-amber-600">
                    <Star size={10} className="fill-amber-400 text-amber-400" />
                    <span>{aiKeyMetrics ? 'AI 추천 핵심 지표' : `${overview.sector} 핵심 지표`}</span>
                  </div>
                )}
                <div className="flex items-center gap-1 text-[10px] text-slate-400">
                  <div className="w-3 h-3 rounded border border-slate-300 flex items-center justify-center">
                    <Check size={7} className="text-slate-400" strokeWidth={3} />
                  </div>
                  <span>= 차트 추가 가능</span>
                </div>
              </div>
            </div>

            {METRIC_GROUPS.map(group => {
              const validKeys = group.keys.filter(k => overview[k] != null || CHARTABLE_METRICS.has(k))
              if (validKeys.length === 0) return null
              return (
                <div key={group.title}>
                  <p className="text-[11px] text-slate-400 font-semibold mb-2 uppercase tracking-wider">{group.title}</p>
                  <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2.5">
                    {validKeys.map(key => (
                      <MetricCard
                        key={key}
                        metricKey={key}
                        value={overview[key]}
                        overview={overview}
                        isKeyMetric={keyMetrics.includes(key)}
                        isSelected={selectedMetrics.includes(key)}
                        onClick={toggleMetric}
                        aiReason={aiKeyMetrics?.find(m => m.metric === key)?.reason}
                      />
                    ))}
                  </div>
                </div>
              )
            })}

            {/* 목표가 범위 별도 표시 */}
            {overview.target_low != null && overview.target_high != null && (
              <div className="bg-white rounded-xl border border-slate-200 p-4">
                <p className="text-[11px] text-slate-400 font-semibold mb-2">애널리스트 목표가 범위</p>
                <div className="flex items-center gap-4">
                  <span className="text-sm text-red-500 font-semibold">${overview.target_low.toFixed(0)}</span>
                  <div className="flex-1 h-2 bg-slate-100 rounded-full relative overflow-hidden">
                    {overview.current_price && (
                      <div
                        className="absolute top-0 h-full bg-gradient-to-r from-indigo-400 to-indigo-600 rounded-full"
                        style={{
                          left: '0%',
                          width: `${Math.min(100, Math.max(0, (overview.current_price - overview.target_low) / (overview.target_high - overview.target_low) * 100))}%`
                        }}
                      />
                    )}
                  </div>
                  <span className="text-sm text-emerald-600 font-semibold">${overview.target_high.toFixed(0)}</span>
                </div>
                {overview.target_mean && (
                  <p className="text-xs text-slate-500 mt-1.5">평균 목표가: <span className="font-semibold text-indigo-600">${overview.target_mean.toFixed(2)}</span></p>
                )}
              </div>
            )}
          </div>
        </CollapsibleSection>
      )}


      {/* ── 지표 히스토리 차트 ── */}
      {selectedMetrics.length > 0 && (
        <MetricHistoryChart
          ticker={ticker}
          selectedMetrics={selectedMetrics}
          onClose={() => setSelectedMetrics([])}
        />
      )}

      {/* ── SEC 공시 (기본 접힘) ── */}
      <CollapsibleSection title="SEC 공시" icon={FileText} defaultOpen={false}>
        <LazySection>
          <FilingList ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 뉴스 (기본 접힘) ── */}
      <CollapsibleSection title="뉴스" icon={Newspaper} defaultOpen={false}>
        <LazySection>
          <NewsFeed ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 경쟁사 비교 ── */}
      <CollapsibleSection title="AI 경쟁사 분석" icon={Users}>
        <LazySection>
          <CompetitorComparison ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 실적 발표 시뮬레이터 ── */}
      <CollapsibleSection title="실적 발표 시뮬레이터" icon={BarChart3}>
        <LazySection>
          <EarningsSimulator ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 애널리스트 vs AI 비교 ── */}
      <CollapsibleSection title="애널리스트 vs AI 분석" icon={Scale}>
        <LazySection>
          <AnalystVsAI ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 실적 발표 캘린더 ── */}
      <CollapsibleSection title="실적 캘린더" icon={Calendar}>
        <LazySection>
          <EarningsCalendar ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 가이던스 정확도 ── */}
      <CollapsibleSection title="가이던스 적중률" icon={Target}>
        <LazySection>
          <GuidanceAccuracy ticker={ticker} />
        </LazySection>
      </CollapsibleSection>

      {/* ── 심층 리서치 플로팅 버튼 + 사이드 패널 ── */}
      {createPortal(
        <>
          {!showResearchChat && (
            <button
              onClick={() => setShowResearchChat(true)}
              className="fixed bottom-8 right-8 z-50 flex items-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-2xl text-sm font-semibold shadow-lg shadow-indigo-400/40 transition-all hover:scale-105 active:scale-95"
            >
              <FlaskConical size={16} />
              심층 리서치
            </button>
          )}
          {showResearchChat && (
            <div className="fixed top-0 right-0 h-screen w-[420px] z-50 shadow-2xl shadow-slate-900/20">
              <Suspense fallback={
                <div className="h-full bg-white border-l border-slate-200 flex items-center justify-center text-slate-400 text-sm">
                  로딩 중...
                </div>
              }>
                <StockResearchChat ticker={ticker} onClose={() => setShowResearchChat(false)} />
              </Suspense>
            </div>
          )}
        </>,
        document.body
      )}
    </div>
  )
}
