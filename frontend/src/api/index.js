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
  aiAnalyze: (ticker) => api.post(`/stock/${ticker}/ai-analyze`),
  getEarnings: (ticker) => api.get(`/stock/${ticker}/earnings`),
  getGuidance: (ticker, maxQuarters = 20) => api.get(`/stock/${ticker}/guidance`, { params: { max_quarters: maxQuarters }, timeout: 120000 }),
}

// 포트폴리오
export const portfolioAPI = {
  getAll: () => api.get('/portfolio'),
  add: (item) => api.post('/portfolio', item),
  remove: (id) => api.delete(`/portfolio/${id}`),
}
