"""국가·관할별 공식 소스 레지스트리."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class OfficialSource:
    domain: str          # site: 쿼리에 사용될 도메인
    country: str         # ISO-3166 2글자
    name: str            # 사람이 읽을 수 있는 명칭
    category: str        # regulator / exchange / central_bank / ministry / index_provider
    tier: int            # 1=최고 신뢰, 2=높음, 3=보통
    languages: list[str] = field(default_factory=list)  # 주 언어 코드


# ── 미국 ──
US_SOURCES: list[OfficialSource] = [
    OfficialSource("sec.gov",          "US", "SEC EDGAR",            "regulator",      1, ["en"]),
    OfficialSource("federalreserve.gov","US", "Federal Reserve",      "central_bank",   1, ["en"]),
    OfficialSource("treasury.gov",     "US", "US Treasury",          "ministry",       1, ["en"]),
    OfficialSource("bls.gov",          "US", "Bureau of Labor Stats", "ministry",       1, ["en"]),
    OfficialSource("bea.gov",          "US", "Bureau of Econ Analysis","ministry",      1, ["en"]),
    OfficialSource("nasdaq.com",       "US", "NASDAQ",               "exchange",       2, ["en"]),
    OfficialSource("nyse.com",         "US", "NYSE",                 "exchange",       2, ["en"]),
    OfficialSource("cmegroup.com",     "US", "CME Group",            "exchange",       2, ["en"]),
    OfficialSource("fred.stlouisfed.org","US","FRED",                 "central_bank",   1, ["en"]),
]

# ── 중국 ──
CN_SOURCES: list[OfficialSource] = [
    OfficialSource("csrc.gov.cn",      "CN", "中国证监会 (CSRC)",      "regulator",      1, ["zh"]),
    OfficialSource("sse.com.cn",       "CN", "상하이거래소 (SSE)",     "exchange",       1, ["zh", "en"]),
    OfficialSource("szse.cn",          "CN", "선전거래소 (SZSE)",      "exchange",       1, ["zh", "en"]),
    OfficialSource("hkexnews.hk",      "CN", "홍콩거래소 공시 (HKEx)", "exchange",       1, ["zh", "en"]),
    OfficialSource("hkex.com.hk",      "CN", "홍콩거래소",            "exchange",       2, ["zh", "en"]),
    OfficialSource("bse.com.cn",       "CN", "북경거래소 (BSE)",       "exchange",       1, ["zh"]),
    OfficialSource("pbc.gov.cn",       "CN", "中국인민은행 (PBOC)",    "central_bank",   1, ["zh"]),
    OfficialSource("safe.gov.cn",      "CN", "국가외환관리국 (SAFE)",  "regulator",      1, ["zh"]),
    OfficialSource("mofcom.gov.cn",    "CN", "상무부 (MOFCOM)",       "ministry",       2, ["zh"]),
    OfficialSource("samr.gov.cn",      "CN", "시장감독관리총국 (SAMR)","regulator",      2, ["zh"]),
]

# ── 한국 ──
KR_SOURCES: list[OfficialSource] = [
    OfficialSource("dart.fss.or.kr",   "KR", "DART 전자공시",         "regulator",      1, ["ko"]),
    OfficialSource("fsc.go.kr",        "KR", "금융위원회 (FSC)",       "regulator",      1, ["ko"]),
    OfficialSource("krx.co.kr",        "KR", "한국거래소 (KRX)",       "exchange",       1, ["ko"]),
    OfficialSource("bok.or.kr",        "KR", "한국은행 (BOK)",         "central_bank",   1, ["ko"]),
    OfficialSource("moef.go.kr",       "KR", "기획재정부 (MOEF)",      "ministry",       2, ["ko"]),
    OfficialSource("mosf.go.kr",       "KR", "기획재정부 구URL",       "ministry",       2, ["ko"]),
]

# ── 일본 ──
JP_SOURCES: list[OfficialSource] = [
    OfficialSource("jpx.co.jp",        "JP", "도쿄거래소 (JPX)",       "exchange",       1, ["ja", "en"]),
    OfficialSource("fsa.go.jp",        "JP", "금융청 (FSA)",           "regulator",      1, ["ja"]),
    OfficialSource("boj.or.jp",        "JP", "일본은행 (BOJ)",         "central_bank",   1, ["ja", "en"]),
    OfficialSource("mof.go.jp",        "JP", "재무성 (MOF)",           "ministry",       2, ["ja"]),
    OfficialSource("meti.go.jp",       "JP", "경제산업성 (METI)",      "ministry",       2, ["ja"]),
    OfficialSource("edinet-fsa.go.jp", "JP", "EDINET 공시",           "regulator",      1, ["ja"]),
]

# ── 유럽 ──
EU_SOURCES: list[OfficialSource] = [
    OfficialSource("esma.europa.eu",   "EU", "ESMA",                 "regulator",      1, ["en"]),
    OfficialSource("ecb.europa.eu",    "EU", "유럽중앙은행 (ECB)",     "central_bank",   1, ["en"]),
    OfficialSource("eurostat.ec.europa.eu","EU","유로스탯",            "ministry",       1, ["en"]),
    OfficialSource("fca.org.uk",       "GB", "영국 FCA",              "regulator",      1, ["en"]),
    OfficialSource("bankofengland.co.uk","GB","영란은행 (BoE)",        "central_bank",   1, ["en"]),
    OfficialSource("euronext.com",     "EU", "유로넥스트",             "exchange",       2, ["en"]),
    OfficialSource("londonstockexchange.com","GB","런던거래소 (LSE)",  "exchange",       2, ["en"]),
]

# ── 국제기구 ──
INTL_SOURCES: list[OfficialSource] = [
    OfficialSource("imf.org",          "INTL","IMF",                  "ministry",       1, ["en"]),
    OfficialSource("worldbank.org",    "INTL","World Bank",           "ministry",       1, ["en"]),
    OfficialSource("bis.org",          "INTL","국제결제은행 (BIS)",    "central_bank",   1, ["en"]),
    OfficialSource("wto.org",          "INTL","WTO",                  "ministry",       2, ["en"]),
    OfficialSource("oecd.org",         "INTL","OECD",                 "ministry",       1, ["en"]),
    OfficialSource("iadb.org",         "INTL","미주개발은행 (IDB)",    "ministry",       2, ["en"]),
]

# ── 전체 맵 ──
ALL_SOURCES: list[OfficialSource] = (
    US_SOURCES + CN_SOURCES + KR_SOURCES + JP_SOURCES + EU_SOURCES + INTL_SOURCES
)

_COUNTRY_MAP: dict[str, list[OfficialSource]] = {}
for _src in ALL_SOURCES:
    _COUNTRY_MAP.setdefault(_src.country, []).append(_src)

_DOMAIN_MAP: dict[str, OfficialSource] = {s.domain: s for s in ALL_SOURCES}


def get_sources_for_country(country_code: str) -> list[OfficialSource]:
    """ISO-3166 코드로 공식 소스 목록 반환."""
    return _COUNTRY_MAP.get(country_code.upper(), [])


def get_source_by_domain(domain: str) -> OfficialSource | None:
    """도메인으로 OfficialSource 조회."""
    domain = domain.lstrip("www.")
    return _DOMAIN_MAP.get(domain)


def get_tier1_domains(country_code: str) -> list[str]:
    """해당 국가의 tier-1 도메인 목록."""
    return [s.domain for s in get_sources_for_country(country_code) if s.tier == 1]
