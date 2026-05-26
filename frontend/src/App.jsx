import useAppStore from './store/useAppStore'
import Sidebar from './components/Sidebar'
import MacroView from './components/views/MacroView'
import StockView from './components/views/StockView'
import PortfolioView from './components/views/PortfolioView'
import DeepResearchView from './components/views/DeepResearchView'

const VIEWS = {
  macro:        MacroView,
  stock:        StockView,
  portfolio:    PortfolioView,
  deepResearch: DeepResearchView,
}

export default function App() {
  const { currentView } = useAppStore()
  const View = VIEWS[currentView] || MacroView

  return (
    <div className="flex h-screen bg-slate-50 overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-hidden">
        <View />
      </main>
    </div>
  )
}
