import { useState } from 'react'
import { Search, FlaskConical } from 'lucide-react'
import StockResearchChat from '../shared/StockResearchChat'

const EXAMPLES = ['NVDA', 'AAPL', 'MSFT', 'TSLA', 'META', 'AMZN', 'GOOGL']

export default function DeepResearchView() {
  const [ticker, setTicker] = useState('')
  const [activeTicker, setActiveTicker] = useState(null)
  const [inputVal, setInputVal] = useState('')

  const launch = (t) => {
    const clean = (t || inputVal).trim().toUpperCase()
    if (!clean) return
    setActiveTicker(clean)
    setTicker(clean)
  }

  if (activeTicker) {
    return (
      <div className="flex flex-col h-screen">
        <StockResearchChat
          ticker={activeTicker}
          onClose={() => { setActiveTicker(null); setTicker('') }}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center h-screen bg-slate-50">
      <div className="w-full max-w-md px-6">
        {/* 아이콘 + 타이틀 */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-indigo-600 flex items-center justify-center mb-4 shadow-lg shadow-indigo-200">
            <FlaskConical size={28} className="text-white" />
          </div>
          <h2 className="text-xl font-extrabold text-slate-800">심층 리서치</h2>
          <p className="text-sm text-slate-400 mt-1 text-center">
            Gemini + 웹 검색으로 종목을 깊이 분석합니다
          </p>
        </div>

        {/* 입력 */}
        <div className="flex gap-2">
          <input
            type="text"
            value={inputVal}
            onChange={e => setInputVal(e.target.value.toUpperCase())}
            onKeyDown={e => { if (e.key === 'Enter') launch() }}
            placeholder="티커 입력 (예: NVDA)"
            className="flex-1 bg-white border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold text-slate-700 placeholder-slate-300 focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 tracking-wide"
            autoFocus
          />
          <button
            onClick={() => launch()}
            disabled={!inputVal.trim()}
            className="px-4 py-3 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white rounded-xl transition-colors flex items-center gap-1.5"
          >
            <Search size={16} />
          </button>
        </div>

        {/* 예시 티커 */}
        <div className="mt-4 flex flex-wrap gap-2 justify-center">
          {EXAMPLES.map(t => (
            <button
              key={t}
              onClick={() => launch(t)}
              className="px-3 py-1.5 text-xs font-semibold text-slate-500 border border-slate-200 rounded-full bg-white hover:border-indigo-300 hover:text-indigo-600 transition-all"
            >
              {t}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
