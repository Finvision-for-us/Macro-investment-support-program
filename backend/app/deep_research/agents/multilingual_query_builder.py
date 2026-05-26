"""다언어 쿼리 빌더 — 관할·이벤트·기업 컨텍스트를 조합해 현지어 검색어 생성."""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.deep_research.agents.jurisdiction_detector import JurisdictionResult
from app.deep_research.sources.source_registry import get_sources_for_country

logger = logging.getLogger(__name__)

# 거래소/기관 약어 (티커 오인 방지) — jurisdiction_detector와 동일 집합
_EXCHANGE_ABBREVIATIONS: frozenset[str] = frozenset([
    "SSE", "SZSE", "CSRC", "HKEX", "SEC", "KRX", "DART", "NYSE", "NASDAQ",
    "ESMA", "ECB", "FCA", "FSC", "BOK", "BOJ", "JPX", "FSA", "PBOC",
    "SAFE", "MOFCOM", "SAMR", "BIS", "IMF", "WTO", "OECD", "TSE", "OSE",
    "LSE", "BSE", "NSE", "ASX", "SGX", "MOEF", "FRED", "BEA", "BLS",
    "ADR", "ETF", "IPO", "AUM", "EPS", "GDP", "CPI", "PPI",
    "FOMC", "FED", "BOE", "RBA", "SNB", "M&A",
    "CEO", "CFO", "COO", "CTO", "RSU", "ESG",
    "US", "CN", "KR", "JP", "EU", "UK", "GB", "HK", "MX", "IN",
])

# ── 지역명 별칭 사전 (en/ko → 현지어 漢字 또는 현지 문자) ──
_LOCATION_ALIAS_MAP: dict[str, str] = {
    "wuxi": "无锡", "china": "中国", "shanghai": "上海",
    "shenzhen": "深圳", "beijing": "北京", "hong kong": "香港",
    "guangzhou": "广州", "chengdu": "成都", "hangzhou": "杭州",
    "nanjing": "南京", "tianjin": "天津", "wuhan": "武汉",
    "suzhou": "苏州", "xi'an": "西安",
    # Korean labels for Chinese locations
    "우시": "无锡", "상하이": "上海", "선전": "深圳",
    "베이징": "北京", "홍콩": "香港", "광저우": "广州",
    "중국": "中国", "청두": "成都", "항저우": "杭州",
}

# ── 기업명 별칭 사전 (lower-case → 현지어) ──
_COMPANY_ALIAS_MAP: dict[str, str] = {
    "indie semiconductor": "英迪半导体",
    "indi": "英迪半导体",
    "wuxi apptec": "药明康德",
    "alibaba": "阿里巴巴", "tencent": "腾讯", "baidu": "百度",
    "jd.com": "京东", "byd": "比亚迪", "catl": "宁德时代",
    "xiaomi": "小米", "nio": "蔚来", "xpeng": "小鹏",
    "li auto": "理想汽车",
    "samsung electronics": "삼성전자", "sk hynix": "SK하이닉스",
    "lg electronics": "LG전자", "hyundai motor": "현대자동차",
    "toyota": "トヨタ", "softbank": "ソフトバンク", "sony": "ソニー",
}

# ── 이벤트 키워드 사전 ──
_EVENT_KEYWORD_MAP: dict[str, dict[str, list[str]]] = {
    "asset_sale": {
        "en": ["sale", "divestiture", "disposal", "stake sale"],
        "zh": ["出售", "股权转让", "资产出售"],
        "ko": ["매각", "지분 매각"],
        "ja": ["売却", "持分売却"],
        "sec_form": ["8-K", "divestiture"],
    },
    "acquisition": {
        "en": ["acquisition", "merger", "takeover"],
        "zh": ["收购", "并购"],
        "ko": ["인수", "합병"],
        "ja": ["買収", "合併"],
        "sec_form": ["8-K", "merger agreement"],
    },
    "export_control": {
        "en": ["export restriction", "export control", "sanctions"],
        "zh": ["出口管制", "制裁", "限制"],
        "ko": ["수출 규제", "제재"],
        "ja": ["輸出規制", "制裁"],
        "sec_form": ["10-K", "export control"],
    },
    "supply_chain": {
        "en": ["factory", "manufacturing", "supply chain"],
        "zh": ["工厂", "制造", "供应链"],
        "ko": ["공장", "생산"],
        "ja": ["工場", "製造"],
        "sec_form": ["10-K", "manufacturing"],
    },
    "dual_listing": {
        "en": ["ADR", "dual listing", "disclosure"],
        "zh": ["ADR", "双重上市", "公告"],
        "ko": ["이중 상장", "ADR"],
        "sec_form": ["20-F", "6-K"],
    },
    "regulatory": {
        "en": ["regulatory", "approval", "disclosure", "filing"],
        "zh": ["监管", "批准", "公告", "披露"],
        "ko": ["규제", "공시"],
        "sec_form": ["8-K", "regulatory"],
    },
}

_DEFAULT_KW: dict[str, list[str]] = {
    "en": ["disclosure"], "zh": ["公告"], "ko": ["공시"], "sec_form": ["filing"],
}


@dataclass
class LocalizedQuery:
    query: str
    language: str
    country: str
    query_type: str  # official_site / local_language / english_cross
    site_domain: str = ""


@dataclass
class MultilingualQueries:
    original: str
    queries: list[LocalizedQuery] = field(default_factory=list)

    def all_query_strings(self) -> list[str]:
        return [q.query for q in self.queries]


def _get_entity(query: str, context: Optional[dict]) -> tuple[str, str]:
    """
    (ticker, company_name) 반환.
    우선순위: context.ticker > query 한국어조사티커 > query 독립티커 > Title-case 명사
    위치명·거래소 약어 제외.
    """
    ctx = context or {}
    ticker = ctx.get("ticker", "")
    company_name = ctx.get("company_name", "") or ticker
    if ticker:
        return ticker, company_name

    # 한국어 조사 붙은 티커
    ko_hits = [
        m.group(1)
        for m in re.finditer(r'\b([A-Z]{2,5})[의가는은을를이와과도만]\b', query)
        if m.group(1) not in _EXCHANGE_ABBREVIATIONS
    ]
    if ko_hits:
        return ko_hits[0], ko_hits[0]

    # 독립 ALL-CAPS 티커
    standalone = [
        m.group(1)
        for m in re.finditer(r'\b([A-Z]{2,5})\b', query)
        if m.group(1) not in _EXCHANGE_ABBREVIATIONS
    ]
    if standalone:
        return standalone[0], standalone[0]

    # Title-case 고유명사 (위치명·stop-word 제외)
    _loc_words = {k.lower() for k in _LOCATION_ALIAS_MAP}
    _stopwords = {
        "The", "In", "Of", "For", "And", "Or", "Is", "Are", "Has",
        "Was", "Were", "Will", "Can", "Should", "What", "How",
        "China", "Japan", "Korea", "India", "Mexico", "Europe",
    }
    for m in re.finditer(r'\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\b', query):
        c = m.group(1)
        if c not in _stopwords and c.lower() not in _loc_words and len(c) > 2:
            return c, c

    return "", ""


def _location_aliases_zh(query: str) -> list[str]:
    """쿼리에서 지명을 감지해 중국어 별칭으로 반환 (한자만)."""
    q_lower = query.lower()
    found: list[str] = []
    for name, zh in _LOCATION_ALIAS_MAP.items():
        if name in q_lower and zh not in found:
            if any('一' <= ch <= '鿿' for ch in zh):
                found.append(zh)
    return found


def _ev(event_type: str, lang: str) -> list[str]:
    """이벤트 타입 + 언어 조합으로 키워드 리스트 반환."""
    return _EVENT_KEYWORD_MAP.get(event_type, {}).get(lang) or _DEFAULT_KW.get(lang, [])


class MultilingualQueryBuilder:

    def build(
        self,
        original_query: str,
        jurisdiction: JurisdictionResult,
        context: Optional[dict] = None,
    ) -> MultilingualQueries:
        result = MultilingualQueries(original=original_query)
        ticker, company_name = _get_entity(original_query, context)
        event_type = jurisdiction.event_type
        all_countries = [jurisdiction.primary] + list(jurisdiction.secondary)

        for country in all_countries:
            if country == "US":
                self._add_us_queries(result, original_query, ticker, company_name, event_type)
            elif country == "CN":
                self._add_cn_queries(result, original_query, ticker, company_name, event_type)
            elif country == "KR":
                self._add_kr_queries(result, original_query, ticker, company_name, event_type)
            elif country == "JP":
                self._add_jp_queries(result, original_query, ticker, company_name, event_type)
            elif country == "EU":
                self._add_eu_queries(result, original_query, ticker, company_name, event_type)

        logger.debug(
            f"[multilingual] {len(result.queries)}개 쿼리 생성 "
            f"(primary={jurisdiction.primary}, ticker={ticker!r}, "
            f"company={company_name!r}, event={event_type!r})"
        )
        return result

    def _add_us_queries(
        self, result: MultilingualQueries,
        query: str, ticker: str, company_name: str, event_type: str,
    ):
        result.queries.append(LocalizedQuery(
            query=query, language="en", country="US", query_type="english_cross",
        ))
        entity = ticker or company_name
        if not entity:
            return

        sec_kws = _ev(event_type, "sec_form")
        sec_str = " ".join(sec_kws[:2])

        result.queries.append(LocalizedQuery(
            query=f"site:sec.gov {entity} {sec_str}".strip(),
            language="en", country="US",
            query_type="official_site", site_domain="sec.gov",
        ))
        # 전체 회사명이 티커와 다를 때 추가 쿼리
        if company_name and company_name.lower() != entity.lower():
            en_kws = _ev(event_type, "en")
            result.queries.append(LocalizedQuery(
                query=f'site:sec.gov "{company_name}" {en_kws[0] if en_kws else "disclosure"}',
                language="en", country="US",
                query_type="official_site", site_domain="sec.gov",
            ))

    def _add_cn_queries(
        self, result: MultilingualQueries,
        query: str, ticker: str, company_name: str, event_type: str,
    ):
        zh_kws = _ev(event_type, "zh")
        locs_zh = _location_aliases_zh(query)

        entity_zh = (
            _COMPANY_ALIAS_MAP.get((company_name or "").lower())
            or _COMPANY_ALIAS_MAP.get((ticker or "").lower())
            or ""
        )
        entity_en = company_name or ticker

        # 공식 소스 site: 쿼리 — CN tier-1 exchange/regulator 도메인
        cn_domains = [
            s.domain for s in get_sources_for_country("CN")
            if s.tier == 1 and s.category in ("exchange", "regulator")
        ]
        for domain in cn_domains[:5]:
            parts = []
            if locs_zh:
                parts.extend(locs_zh[:1])
            if zh_kws:
                parts.append(zh_kws[0])
            keyword = " ".join(parts) if parts else (entity_zh or entity_en or "")
            if keyword:
                result.queries.append(LocalizedQuery(
                    query=f"site:{domain} {keyword}",
                    language="zh", country="CN",
                    query_type="official_site", site_domain=domain,
                ))

        # 중국어 자연어 쿼리 1: entity_zh + 지역 + 이벤트
        if entity_zh:
            parts = [entity_zh] + locs_zh[:1] + zh_kws[:2]
            result.queries.append(LocalizedQuery(
                query=" ".join(p for p in parts if p),
                language="zh", country="CN", query_type="local_language",
            ))

        # 중국어 자연어 쿼리 2: ticker/company + 지역(zh) + 이벤트(zh)
        if (locs_zh or zh_kws) and entity_en:
            parts = [entity_en] + locs_zh[:1] + zh_kws[:1]
            combo = " ".join(p for p in parts if p)
            if not any(q.query == combo for q in result.queries):
                result.queries.append(LocalizedQuery(
                    query=combo,
                    language="zh", country="CN", query_type="local_language",
                ))

        # HKEx 영문 쿼리 (외국인 접근성)
        if entity_en:
            en_kws = _ev(event_type, "en")
            result.queries.append(LocalizedQuery(
                query=f"site:hkexnews.hk {entity_en} {en_kws[0] if en_kws else 'disclosure'}",
                language="en", country="CN",
                query_type="official_site", site_domain="hkexnews.hk",
            ))

    def _add_kr_queries(
        self, result: MultilingualQueries,
        query: str, ticker: str, company_name: str, event_type: str,
    ):
        ko_kws = _ev(event_type, "ko")
        entity = company_name or ticker
        if entity:
            result.queries.append(LocalizedQuery(
                query=f"site:dart.fss.or.kr {entity} {' '.join(ko_kws[:1])}".strip(),
                language="ko", country="KR",
                query_type="official_site", site_domain="dart.fss.or.kr",
            ))
        parts = [p for p in [entity, " ".join(ko_kws[:2])] if p]
        result.queries.append(LocalizedQuery(
            query=" ".join(parts),
            language="ko", country="KR", query_type="local_language",
        ))

    def _add_jp_queries(
        self, result: MultilingualQueries,
        query: str, ticker: str, company_name: str, event_type: str,
    ):
        ja_kws = _ev(event_type, "ja")
        entity = company_name or ticker
        entity_ja = _COMPANY_ALIAS_MAP.get((entity or "").lower(), "")
        if entity:
            result.queries.append(LocalizedQuery(
                query=f"site:edinet-fsa.go.jp {entity} {' '.join(ja_kws[:1])}".strip(),
                language="ja", country="JP",
                query_type="official_site", site_domain="edinet-fsa.go.jp",
            ))
        if entity_ja:
            result.queries.append(LocalizedQuery(
                query=" ".join([entity_ja] + ja_kws[:1]),
                language="ja", country="JP", query_type="local_language",
            ))

    def _add_eu_queries(
        self, result: MultilingualQueries,
        query: str, ticker: str, company_name: str, event_type: str,
    ):
        en_kws = _ev(event_type, "en")
        entity = company_name or ticker
        if entity:
            result.queries.append(LocalizedQuery(
                query=f"site:esma.europa.eu {entity} {en_kws[0] if en_kws else 'disclosure'}",
                language="en", country="EU",
                query_type="official_site", site_domain="esma.europa.eu",
            ))
        result.queries.append(LocalizedQuery(
            query=f"{query} EU ESMA regulatory disclosure",
            language="en", country="EU", query_type="official_site",
        ))


multilingual_query_builder = MultilingualQueryBuilder()
