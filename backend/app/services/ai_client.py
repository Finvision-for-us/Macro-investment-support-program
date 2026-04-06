from app.config import GOOGLE_API_KEY

class AIClient:
    def __init__(self):
        self.enabled = bool(GOOGLE_API_KEY)
        self._model = None

    def _get_model(self):
        if self._model is None and self.enabled:
            import google.generativeai as genai
            genai.configure(api_key=GOOGLE_API_KEY)
            self._model = genai.GenerativeModel("gemini-2.5-flash-lite")
        return self._model

    def analyze_stock(self, ticker: str, overview: dict, financials: dict, recent_news: list) -> dict:
        if not self.enabled:
            return {
                "status": "disabled",
                "message": ".env 파일에 GOOGLE_API_KEY를 추가하면 AI 분석이 활성화됩니다.",
            }
        try:
            model = self._get_model()
            news_text = "\n".join([f"- {a['title']} ({a['published_at'][:10]})" for a in recent_news[:5]])
            prompt = f"""다음 종목에 대해 간결한 투자 리서치 요약을 한국어로 작성해주세요.

종목: {ticker} ({overview.get('name', '')})
섹터: {overview.get('sector', '-')}
현재가: ${overview.get('current_price', '-')}
PER: {overview.get('pe_ratio', '-')}
시가총액: {overview.get('market_cap', '-')}

최근 뉴스:
{news_text}

다음 항목을 포함해주세요:
1. 비즈니스 요약 (2~3줄)
2. 현재 밸류에이션 평가
3. 주요 리스크 요인
4. 주요 기회 요인
5. 전반적인 투자 견해 (중립적으로)"""

            response = model.generate_content(prompt)
            return {
                "status": "success",
                "analysis": response.text,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def analyze_macro(self, cycle_state: dict) -> dict:
        if not self.enabled:
            return {
                "status": "disabled",
                "message": ".env 파일에 GOOGLE_API_KEY를 추가하면 AI 분석이 활성화됩니다.",
            }
        try:
            model = self._get_model()
            metrics = cycle_state.get("metrics", {})
            matching = cycle_state.get("matching_indicators", {})

            matched_list = []
            unmatched_list = []
            for ind_id, info in matching.items():
                if info.get("no_pattern"):
                    continue
                label = f"{ind_id}: {info.get('value', '-')}"
                if info.get("match"):
                    rng = info.get("pattern_range", [])
                    matched_list.append(f"{label} (패턴 범위: {rng[0]}~{rng[1]})")
                else:
                    unmatched_list.append(label)

            matched_text = "\n".join(matched_list) if matched_list else "없음"
            unmatched_text = "\n".join(unmatched_list) if unmatched_list else "없음"

            prompt = f"""현재 미국 거시경제 지표와 경기 사이클 분석을 한국어로 작성해주세요.

## 경기 사이클 판단 결과
- 현재 위치: {cycle_state.get('phase_name', '-')} ({cycle_state.get('phase_name_en', '-')})
- 8단계 중 {cycle_state.get('phase', '-')}번째 단계
- 신뢰도: {cycle_state.get('confidence', 0) * 100:.0f}%

## 현재 주요 지표
- GDP YoY: {metrics.get('GDP', '-')}%
- 실업률: {metrics.get('UNRATE', '-')}%
- CPI YoY: {metrics.get('CPIAUCSL', '-')}%
- 기준금리: {metrics.get('DFF', '-')}%
- 장단기 금리차: {metrics.get('T10Y2Y', '-')}%
- 설비가동률: {metrics.get('TCU', '-')}%
- 소비자심리: {metrics.get('UMCSENT', '-')}

## 패턴 매칭 결과
일치하는 지표:
{matched_text}

불일치 지표:
{unmatched_text}

## 추천 섹터: {', '.join(cycle_state.get('recommended_sectors', []))}
## 주의 섹터: {', '.join(cycle_state.get('caution_sectors', []))}

다음을 포함해주세요:
1. 현재 경기 사이클 위치에 대한 해석 (8단계 사이클 기준)
2. 패턴 매칭 결과의 의미와 신뢰도에 대한 평가
3. 주요 거시 리스크
4. 투자자 관점에서 주목할 점 (섹터 로테이션 포함)
(3~5문단, 중립적 관점, 매수/매도 판단은 하지 마세요)"""

            response = model.generate_content(prompt)
            return {
                "status": "success",
                "analysis": response.text,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

ai_client = AIClient()
