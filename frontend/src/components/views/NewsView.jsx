import { useState } from 'react'
import { Radio, Newspaper } from 'lucide-react'
import TelegramFeedView from './TelegramFeedView'
import PipelineView from './PipelineView'

const TABS = [
  { id: 'telegram', label: '텔레그램 속보', icon: Radio },
  { id: 'pipeline', label: '수집 현황',     icon: Newspaper },
]

export default function NewsView() {
  const [tab, setTab] = useState('telegram')

  return (
    <div className="flex flex-col h-full">
      {/* 탭 바 */}
      <div className="flex items-center gap-1 px-5 pt-3 pb-0 border-b border-slate-200 bg-white shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => {
          const active = tab === id
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold border-b-2 transition-colors
                ${active
                  ? 'border-indigo-500 text-indigo-600'
                  : 'border-transparent text-slate-400 hover:text-slate-600'
                }`}
            >
              <Icon size={14} />
              {label}
            </button>
          )
        })}
      </div>

      {/* 콘텐츠 */}
      <div className="flex-1 overflow-hidden">
        {tab === 'telegram' ? <TelegramFeedView /> : <PipelineView />}
      </div>
    </div>
  )
}
