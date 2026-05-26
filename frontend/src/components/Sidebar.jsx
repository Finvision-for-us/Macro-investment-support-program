import { Globe, BarChart2, Briefcase, FlaskConical } from 'lucide-react'
import useAppStore from '../store/useAppStore'

const MENU = [
  { id: 'macro',        label: '거시경제',   icon: Globe },
  { id: 'stock',        label: '종목 분석',  icon: BarChart2 },
  { id: 'portfolio',    label: '포트폴리오', icon: Briefcase },
  { id: 'deepResearch', label: '심층 리서치', icon: FlaskConical },
]

export default function Sidebar() {
  const { currentView, setView } = useAppStore()

  return (
    <aside className="w-56 min-h-screen bg-gradient-to-b from-white to-slate-50 border-r border-slate-200 flex flex-col">
      {/* 로고 */}
      <div className="px-5 py-6 border-b border-slate-200">
        <h1 className="text-xl font-extrabold bg-gradient-to-r from-indigo-600 to-purple-600 bg-clip-text text-transparent tracking-wide">FinVision</h1>
        <p className="text-[11px] text-slate-400 mt-0.5">투자 리서치 대시보드</p>
      </div>

      {/* 메뉴 */}
      <nav className="flex-1 py-4 space-y-1 px-3">
        {MENU.map(({ id, label, icon: Icon }) => {
          const active = currentView === id
          return (
            <button
              key={id}
              onClick={() => setView(id)}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all
                ${active
                  ? 'bg-indigo-50 text-indigo-700 border border-indigo-200 shadow-sm shadow-indigo-100'
                  : 'text-slate-500 hover:bg-white hover:text-slate-700 hover:shadow-sm border border-transparent'
                }`}
            >
              <Icon size={18} />
              {label}
            </button>
          )
        })}
      </nav>

      {/* 하단 */}
      <div className="px-5 py-4 border-t border-slate-200">
        <p className="text-[10px] text-slate-400 leading-relaxed">
          데이터: FRED · Yahoo Finance · SEC EDGAR
        </p>
        <p className="text-[10px] text-slate-300 mt-1">본 도구는 투자 조언이 아닙니다</p>
      </div>
    </aside>
  )
}
