import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 거시경제
export const macroAPI = {
  getOverview: () => api.get('/macro/overview'),
  getIndicator: (seriesId, limit = 60) => api.get(`/macro/indicator/${seriesId}`, { params: { limit } }),
  getMarketState: () => api.get('/macro/market-state', { timeout: 60000 }),
  getNews: () => api.get('/macro/news'),
  aiAnalyze: () => api.post('/macro/ai-analyze'),
}

// 종목
export const stockAPI = {
  search: (q) => api.get('/stock/search', { params: { q } }),
  suggest: (q, enrich = 0) => api.get('/stock/suggest', { params: { q, enrich } }),
  getOverview: (ticker) => api.get(`/stock/${ticker}/overview`),
  getQuote: (ticker) => api.get(`/stock/${ticker}/quote`),
  getPrice: (ticker, period = '1y') => api.get(`/stock/${ticker}/price`, { params: { period } }),
  getFinancials: (ticker) => api.get(`/stock/${ticker}/financials`),
  getMetricHistory: (ticker) => api.get(`/stock/${ticker}/metric-history`),
  getFilings: (ticker) => api.get(`/stock/${ticker}/filings`),
  getNews: (ticker) => api.get(`/stock/${ticker}/news`),
  getEarnings: (ticker) => api.get(`/stock/${ticker}/earnings`),
  getGuidance: (ticker, maxQuarters = 20) => api.get(`/stock/${ticker}/guidance`, { params: { max_quarters: maxQuarters }, timeout: 120000 }),
  getCompetitors: (ticker) => api.get(`/stock/${ticker}/competitors`),
  getEarningsCalendar: (ticker) => api.get(`/stock/${ticker}/earnings-calendar`),
  getGuidanceAccuracy: (ticker) => api.get(`/stock/${ticker}/guidance-accuracy`),
  getAnalystVsAI: (ticker) => api.get(`/stock/${ticker}/analyst-vs-ai`),
  translateText: (text) => api.post('/stock/translate', { text }),
}

// 텔레그램
export const telegramAPI = {
  getFeed: (hours = 24, limit = 60) => api.get('/telegram/feed', { params: { hours, limit } }),
}

// 수집 파이프라인 스토리
export const storiesAPI = {
  getLatest: () => api.get('/stories/latest'),
  refresh: () => api.post('/stories/refresh'),
  refreshStatus: () => api.get('/stories/refresh/status'),
}

// 포트폴리오
export const portfolioAPI = {
  getAll: () => api.get('/portfolio'),
  add: (item) => api.post('/portfolio', item),
  remove: (id) => api.delete(`/portfolio/${id}`),
}
