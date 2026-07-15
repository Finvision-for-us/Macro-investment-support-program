"""실적 발표 시뮬레이터 API 라우트"""

from fastapi import APIRouter, HTTPException
from app.services.earnings_analyzer import (
    get_full_earnings_analysis,
    _get_revenue_estimates,
    _get_revenue_history,
    _get_sec_cik,
)
from app.services.gemini_guidance import (
    analyze_guidance_for_stock,
    compute_theme_patterns,
    is_available as gemini_available,
    _get_cached_guidance,
    compute_guidance_track_record,
)

router = APIRouter(prefix="/api/stock", tags=["earnings"])


@router.get("/{ticker}/earnings")
async def earnings(ticker: str):
    """종목의 실적 발표 분석 데이터 반환"""
    try:
        result = await get_full_earnings_analysis(ticker)
        # 매출 데이터 보강
        if not result.get("revenue_estimates"):
            result["revenue_estimates"] = _get_revenue_estimates(ticker)
        if not result.get("revenue_history"):
            result["revenue_history"] = _get_revenue_history(ticker)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"어닝 분석 오류: {str(e)}")


@router.get("/{ticker}/guidance")
async def guidance(ticker: str, max_quarters: int = 20):
    """종목의 가이던스 AI 분석 데이터 반환

    SEC 8-K → Gemini AI 분석 → 가이던스 요약/테마/감성점수
    결과는 DB에 영구 캐싱됨 (과거 가이던스는 변하지 않음)

    ★ 최적화: 캐시에 데이터가 이미 있으면 SEC/Yahoo 호출 없이 즉시 반환
    """
    if not gemini_available():
        raise HTTPException(status_code=503, detail="Google API 키가 설정되지 않았습니다")

    try:
        ticker = ticker.upper()

        # ★ 캐시 우선 확인 — 데이터가 이미 있으면 즉시 반환 (source_type 무관)
        cached = await _get_cached_guidance(ticker)
        if cached and len(cached) >= min(max_quarters, 5):
            theme_patterns = _compute_theme_patterns_from_cache(cached)
            return {
                "ticker": ticker,
                "guidance": cached,
                "theme_patterns": theme_patterns,
                "track_record": compute_guidance_track_record(ticker, cached),
                "total_analyzed": len(cached),
                "gemini_enabled": True,
                "cached": True,
            }

        # 캐시 없거나 부족 → 전체 분석 실행
        cik = _get_sec_cik(ticker)
        if not cik:
            raise HTTPException(status_code=404, detail=f"{ticker}의 SEC CIK를 찾을 수 없습니다")

        # 어닝 히스토리 가져오기 (매칭용)
        result = await get_full_earnings_analysis(ticker)
        history = result.get("history", [])

        # 회사명 가져오기 (Motley Fool URL 생성용)
        try:
            from app.services import yfinance_client
            import asyncio
            ov = await asyncio.to_thread(yfinance_client.get_overview, ticker)
            company_name = ov.get("name", "")
        except Exception:
            company_name = ""

        if not history:
            return {"ticker": ticker, "guidance": [], "theme_patterns": {}}

        # Gemini 분석 (캐싱된 것은 건너뜀)
        guidance_list = await analyze_guidance_for_stock(
            ticker, cik, history, max_quarters=max_quarters,
            company_name=company_name
        )

        # 테마별 주가 패턴 계산
        theme_patterns = compute_theme_patterns(guidance_list, history)

        return {
            "ticker": ticker,
            "guidance": guidance_list,
            "theme_patterns": theme_patterns,
            "track_record": compute_guidance_track_record(ticker, guidance_list),
            "total_analyzed": len(guidance_list),
            "gemini_enabled": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"가이던스 분석 오류: {str(e)}")


@router.get("/{ticker}/guidance-accuracy")
async def get_guidance_accuracy(ticker: str):
    """가이던스 감성 vs 실제 어닝 서프라이즈 정확도 분석"""
    import asyncio

    ticker = ticker.upper()

    # 캐싱된 가이던스 데이터 가져오기
    cached = await _get_cached_guidance(ticker)
    if not cached:
        return {"ticker": ticker, "accuracy": None, "quarters": [], "message": "가이던스 데이터 없음"}

    # 어닝 히스토리 가져오기
    try:
        earnings = await get_full_earnings_analysis(ticker)
    except Exception:
        return {"ticker": ticker, "accuracy": None, "quarters": [], "message": "어닝 데이터 조회 실패"}

    history = earnings.get("history", [])

    # period_end → 실제 결과 매핑
    actual_map = {}
    for h in history:
        pe = h.get("period_end")
        if pe and h.get("surprise_pct") is not None:
            actual_map[pe] = {
                "surprise_pct": h["surprise_pct"],
                "beat": h.get("surprise_pct", 0) > 0,
                "reaction_1d": h.get("reaction_1d_change"),
            }

    quarters = []
    correct = 0
    total = 0

    for g in cached:
        pe = g.get("period_end")
        if not pe or pe not in actual_map:
            continue

        actual = actual_map[pe]
        # 키가 존재하되 값이 None(예: parse_error 행)이면 .get 기본값이 안 먹는다
        # → 아래 sentiment > 50 비교에서 TypeError로 티커 전체 500.
        sentiment = g.get("sentiment_score")
        if sentiment is None:
            sentiment = 50

        # 가이던스 긍정(>50)이고 실제 beat이면 정확, 부정(<50)이고 miss이면 정확
        guidance_positive = sentiment > 50
        actually_beat = actual["beat"]

        is_correct = guidance_positive == actually_beat
        if is_correct:
            correct += 1
        total += 1

        quarters.append({
            "period_end": pe,
            "period": g.get("period", pe),
            "sentiment_score": sentiment,
            "guidance_positive": guidance_positive,
            "actually_beat": actually_beat,
            "surprise_pct": actual["surprise_pct"],
            "reaction_1d": actual["reaction_1d"],
            "correct": is_correct,
            "guidance_summary": g.get("guidance_summary", ""),
            "themes": (g.get("key_themes") or [])[:3],
        })

    accuracy = round(correct / total * 100, 1) if total > 0 else None

    return {
        "ticker": ticker,
        "accuracy": accuracy,
        "correct": correct,
        "total": total,
        "quarters": sorted(quarters, key=lambda x: x["period_end"], reverse=True),
    }


def _compute_theme_patterns_from_cache(cached_guidance: list) -> dict:
    """캐시된 가이던스 데이터만으로 테마 패턴 계산 (earnings_history 불필요 버전)"""
    themes = {}
    for g in cached_guidance:
        key_themes = g.get("key_themes", [])
        if isinstance(key_themes, str):
            try:
                import json
                key_themes = json.loads(key_themes)
            except Exception:
                key_themes = []

        sentiment = g.get("sentiment_score")
        if sentiment is None:  # parse_error 행 방어 (.get 기본값은 None 값에 안 먹음)
            sentiment = 50

        for theme in key_themes:
            if theme not in themes:
                themes[theme] = {"count": 0, "total_sentiment": 0}
            themes[theme]["count"] += 1
            themes[theme]["total_sentiment"] += sentiment

    # 무할루시네이션: 캐시 경로에는 주가 데이터가 없다. 감성점수를 %반응으로
    # 환산해 실측처럼 내보내지 않는다(이전: (sentiment-50)*0.1 합성값).
    # avg_reaction은 None으로 명시하고, 실데이터인 평균 감성만 제공한다.
    result_themes = {}
    for theme, data in themes.items():
        if data["count"] >= 1:
            result_themes[theme] = {
                "count": data["count"],
                "avg_reaction": None,           # 실측 주가 반응 아님 — 비캐시 경로에서만 제공
                "avg_sentiment": round(data["total_sentiment"] / data["count"], 1),
                "estimated": True,
            }

    return {"themes": result_themes}
