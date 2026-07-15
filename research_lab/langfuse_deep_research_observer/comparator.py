"""Deep Research 트레이스 비교기 — 결정론적 '품질' 채점 + pairwise 상대비교.

재설계 배경 (Fable 리뷰 → 수량 편향 제거):
구버전은 전 항목이 len(...)/N 카운트 채점이라 '많이 수집한 엔진'이 무조건
이겼다(쿼리 20개 > 좋은 쿼리 3개, 긴 답변 > 짧고 정확한 답변). 또 엔진이
자기신고한 reliability_score를 엔진 간 비교해 자기채점 인플레가 그대로
순위가 됐다. 이 버전의 원칙:

1. 카운트가 아니라 비율/품질: 모든 지표는 [0,1] 비율(앵커율·다양성·공식출처
   비중·검색 수율 등). 수집량을 늘려도 비율이 나빠지면 점수가 떨어진다.
2. 자기신고 배제: 근거 품질은 결정론적 도메인 티어로만 계산한다
   (backend/app/deep_research/sources/source_registry.py 티어와 동기화된 로컬 표.
   research_lab은 서비스 코드와 격리되므로 import 하지 않고 미러만 유지).
3. pairwise 상대비교: 절대 점수의 임계값 논쟁 대신, 같은 질의를 수행한 엔진
   쌍끼리 '무엇을 상대만 찾았는가'(고유 공식 도메인, 인용 겹침, 항목별 승패)를
   병기한다.
4. 데이터 가용성: 로그 형식 때문에 측정 불가한 항목(예: 텍스트 로그에 인용
   구분 없음)은 0점이 아니라 N/A로 빼고 가중치를 재정규화한다 — 로그 형식이
   점수를 좌우하는 편향 제거.

LLM은 어디에도 관여하지 않는다(무할루시네이션 원칙과 동일 계열).
"""
from __future__ import annotations

import json
import re
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from schema import ResearchTrace, SourceItem, model_to_dict


WEIGHTS = {
    "jurisdiction_detection": 15,
    "query_generation": 15,
    "official_source_coverage": 20,
    "evidence_quality": 15,
    "search_behavior": 10,
    "cross_validation": 10,
    "gap_handling": 10,
    "final_answer_structure": 5,
}

# ── 결정론적 도메인 티어 (backend source_registry와 동기화된 미러) ──
_TIER1_OFFICIAL = (
    "sec.gov", "edgar", "csrc.gov.cn", "sse.com.cn", "szse.cn", "hkexnews.hk",
    "dart.fss.or.kr", "fsc.go.kr", "krx.co.kr", "jpx.co.jp", "edinet-fsa.go.jp",
    "federalreserve.gov", "esma.europa.eu", "pbc.gov.cn", "cninfo.com.cn",
)
_COMPANY_IR = ("investor", "ir.")
_TIER2_MEDIA = (
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "nikkei.com", "nytimes.com", "bbc.com", "caixin.com", "scmp.com",
    "yonhapnews.co.kr", "yna.co.kr",
)
_TIER3_MEDIA = ("cnbc.com", "marketwatch.com", "techcrunch.com", "barrons.com")
_LOW_TRUST = (
    "seekingalpha.com", "fool.com", "benzinga.com", "reddit.com",
    "stockinsights.ai", "pitchgrade.com", "stockanalysis.com", "simplywall.st",
)

_TIER_WEIGHT = {
    "official": 1.0, "company_ir": 0.85, "media_t2": 0.75,
    "media_t3": 0.55, "unknown": 0.40, "low_trust": 0.15,
}

# 관할 → 해당 관할의 공식/지역 도메인 신호 (evidenced 관할 판정용)
_JURISDICTION_DOMAINS = {
    "US": ("sec.gov", "federalreserve.gov", "nyse.com", "nasdaq.com"),
    "CN": ("csrc.gov.cn", "sse.com.cn", "szse.cn", "pbc.gov.cn", "cninfo.com.cn", ".cn"),
    "HK": ("hkexnews.hk", "hkex.com.hk", ".hk"),
    "KR": ("dart.fss.or.kr", "krx.co.kr", "fsc.go.kr", ".kr"),
    "JP": ("edinet-fsa.go.jp", "jpx.co.jp", ".jp"),
}
_JURISDICTION_QUERY_HINTS = {
    "US": ("sec.gov", "sec ", "8-k", "10-k", "10-q", "edgar", "nasdaq", "nyse"),
    "CN": ("csrc", "sse.com.cn", "szse", "cninfo", "上海", "深圳", "证监会"),
    "HK": ("hkex", "hong kong", "홍콩", "港交所"),
    "KR": ("dart", "krx", "금융위", "공시"),
    "JP": ("edinet", "jpx", "japan"),
}

_UNCERTAINTY_MARKERS = (
    "unverified", "unknown", "not confirmed", "uncertain", "could not verify",
    "미확인", "불확실", "확인 필요", "미검증", "검증되지 않",
)
_QUERY_STOPWORDS = {
    "the", "and", "for", "with", "from", "about", "what", "how", "why",
    "analysis", "latest", "news", "report", "impact", "meaning", "site",
}

# pairwise 승패 판정: 카테고리 점수차가 만점의 10% 초과면 승, 이하면 무승부
_VERDICT_MARGIN_RATIO = 0.10
# 종합 승패 판정: 정규화 총점(100점 만점) 차이 3점 초과면 승
_OVERALL_MARGIN = 3.0


def compare_traces(traces: list[ResearchTrace], output_dir: str | Path) -> dict[str, Any]:
    if not traces:
        raise ValueError("At least one ResearchTrace is required.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    scores = {trace.engine_name: _score_trace(trace) for trace in traces}
    pairwise = _pairwise_comparisons(traces, scores)
    improvements = _finvision_improvement_raw_material(traces, scores)
    raw = {
        "score_weights": WEIGHTS,
        "scores": scores,
        "pairwise": pairwise,
        "traces": [model_to_dict(trace) for trace in traces],
        "finvision_improvement_raw_material": improvements,
    }

    raw_file = output_path / "comparison_raw_material.json"
    report_file = output_path / "comparison_report.md"
    raw_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    report_file.write_text(_render_report(traces, scores, pairwise, improvements), encoding="utf-8")
    return raw


# ─────────────────────────────────────────────────────────────
# 채점 (카테고리별 결정론 품질 지표 — None = 데이터 없어 측정 불가)
# ─────────────────────────────────────────────────────────────

def _score_trace(trace: ResearchTrace) -> dict[str, Any]:
    ratios: dict[str, Optional[float]] = {
        "jurisdiction_detection": _jurisdiction_score(trace),
        "query_generation": _query_generation_score(trace),
        "official_source_coverage": _official_source_score(trace),
        "evidence_quality": _evidence_quality_score(trace),
        "search_behavior": _search_behavior_score(trace),
        "cross_validation": _cross_validation_score(trace),
        "gap_handling": _gap_handling_score(trace),
        "final_answer_structure": _final_answer_score(trace),
    }

    result: dict[str, Any] = {}
    total = 0.0
    available_weight = 0
    unavailable: list[str] = []
    for key, ratio in ratios.items():
        if ratio is None:
            result[key] = None
            unavailable.append(key)
            continue
        weighted = round(ratio * WEIGHTS[key], 2)
        result[key] = weighted
        total += ratio * WEIGHTS[key]
        available_weight += WEIGHTS[key]

    result["total"] = round(total, 2)
    # N/A 항목 가중치를 제외하고 100점 만점으로 재정규화 — 로그 형식 편향 제거
    result["total_normalized"] = (
        round(100.0 * total / available_weight, 2) if available_weight else 0.0
    )
    result["unavailable"] = unavailable
    return result


def _jurisdiction_score(trace: ResearchTrace) -> Optional[float]:
    """주장(detected)과 증거(도메인/쿼리에서 유추) 간 자카드 — 과다 주장·누락 양쪽 감점.

    구버전 len/3은 관할을 '많이 주장'할수록 점수를 줬다(근거 무관).
    """
    detected = {j.strip().upper() for j in trace.detected_jurisdictions if j.strip()}
    detected = {j for j in detected if j in _JURISDICTION_DOMAINS}
    evidenced = _evidenced_jurisdictions(trace)
    if not detected and not evidenced:
        return None  # 국경간 신호 자체가 없음 — 측정 불가(0점 아님)
    union = detected | evidenced
    return len(detected & evidenced) / len(union) if union else None


def _evidenced_jurisdictions(trace: ResearchTrace) -> set[str]:
    domains = _all_domains(trace)
    queries = [q.lower() for q in trace.generated_queries + trace.official_source_queries]
    evidenced: set[str] = set()
    for jur, dom_hints in _JURISDICTION_DOMAINS.items():
        if any(_domain_matches(d, dom_hints) for d in domains):
            evidenced.add(jur)
            continue
        q_hints = _JURISDICTION_QUERY_HINTS.get(jur, ())
        if any(h in q for q in queries for h in q_hints):
            evidenced.add(jur)
    return evidenced


def _query_generation_score(trace: ResearchTrace) -> Optional[float]:
    """쿼리 '품질' 비율 평균: 앵커율·비중복률·공식쿼리 비중·다국어. 개수 무관."""
    queries = [q for q in trace.generated_queries if q.strip()]
    if not queries:
        return None

    components: list[float] = []

    # 1) 앵커율: 사용자 질의의 식별자(티커/고유명)를 포함한 쿼리 비율.
    #    제네릭 쿼리("semiconductor market outlook")는 앵커 실패로 감점.
    identifiers = _query_identifiers(trace.query)
    if identifiers:
        anchored = sum(1 for q in queries if _is_anchored(q, identifiers))
        components.append(anchored / len(queries))

    # 2) 비중복률: 토큰집합 기준 고유 쿼리 비율 — 같은 쿼리 20번은 1번과 동일.
    signatures = {_token_signature(q) for q in queries}
    components.append(len(signatures) / len(queries))

    # 3) 공식쿼리 비중: official/전체 비율, 목표 25%에서 포화(카운트 아님).
    official_ratio = _official_query_ratio(trace) or 0.0
    components.append(_cap(official_ratio / 0.25))

    # 4) 다국어: 영문+비영문 혼합이면 1 (국경간 리서치 커버리지 신호).
    has_ascii = any(all(ord(ch) < 128 for ch in q) for q in queries)
    has_non_ascii = any(any(ord(ch) > 127 for ch in q) for q in queries)
    components.append(1.0 if (has_ascii and has_non_ascii) else 0.0)

    return _cap(mean(components))


def _official_source_score(trace: ResearchTrace) -> Optional[float]:
    """인용 중 공식(티어1/IR) '비중' + 관할별 공식 도메인 매칭률."""
    cite_domains = _citation_domains(trace)
    if not cite_domains:
        return None

    components: list[float] = []
    official = [d for d in cite_domains if _domain_tier(d) in ("official", "company_ir")]
    components.append(len(official) / len(cite_domains))

    # 관할별: 이 리서치가 다루는 관할마다 그 관할 공식 도메인을 인용했는가
    jurisdictions = _evidenced_jurisdictions(trace) | {
        j.strip().upper() for j in trace.detected_jurisdictions
        if j.strip().upper() in _JURISDICTION_DOMAINS
    }
    if jurisdictions:
        matched = sum(
            1 for jur in jurisdictions
            if any(_domain_matches(d, _JURISDICTION_DOMAINS[jur]) and
                   _domain_tier(d) in ("official", "company_ir")
                   for d in _all_domains(trace))
        )
        components.append(matched / len(jurisdictions))

    return _cap(mean(components))


def _evidence_quality_score(trace: ResearchTrace) -> Optional[float]:
    """인용 도메인의 결정론 티어 가중 평균. 자기신고 reliability는 사용하지 않는다.

    구버전은 엔진이 자기 로그에 적어온 score를 평균했다 — 자기채점 인플레가
    그대로 순위가 되는 구조. 도메인 티어는 모든 엔진에 동일 잣대다.
    """
    cite_domains = _citation_domains(trace)
    if not cite_domains:
        return None
    return _cap(mean(_TIER_WEIGHT[_domain_tier(d)] for d in cite_domains))


def _search_behavior_score(trace: ResearchTrace) -> Optional[float]:
    """검색 '효율': 쿼리당 고유 도메인 수율 + 쿼리 비중복률. 호출 수 자체는 무보상.

    구버전은 tool_calls/쿼리/소스 개수를 그대로 점수화 — 비효율(같은 검색 반복)이
    고득점하는 역설이 있었다.
    """
    queries = [q for q in trace.generated_queries if q.strip()]
    if not queries and not trace.tool_calls:
        return None
    n_queries = max(1, len(queries) or len(trace.tool_calls))

    unique_domains = {d for s in trace.sources_found for d in [_domain_of(s)] if d}
    yield_rate = _cap(len(unique_domains) / (2.0 * n_queries))  # 목표: 쿼리당 고유 2도메인

    if queries:
        non_redundancy = len({_token_signature(q) for q in queries}) / len(queries)
    else:
        non_redundancy = 1.0
    return _cap((yield_rate + non_redundancy) / 2)


def _cross_validation_score(trace: ResearchTrace) -> Optional[float]:
    """교차검증 존재(3건에서 포화) × 다도메인 게이트(인용 도메인 2개 미만이면 반감).

    단일 도메인만 인용한 '교차검증'은 자기 확인에 불과하므로 절반만 인정.
    """
    entries = [e for e in trace.cross_source_consistency if e.strip()]
    if not entries:
        return 0.0
    base = _cap(len(entries) / 3.0)
    if len(set(_citation_domains(trace))) < 2:
        base *= 0.5
    return base


def _gap_handling_score(trace: ResearchTrace) -> float:
    if trace.unverified_gaps:
        return 1.0
    if any(m in trace.final_answer.lower() for m in _UNCERTAINTY_MARKERS):
        return 0.5
    if any(m in note.lower() for note in trace.notes for m in _UNCERTAINTY_MARKERS):
        return 0.4
    return 0.0


def _final_answer_score(trace: ResearchTrace) -> float:
    """답변 '구조' 품질: 인용 표기 + 섹션/불릿 구조 + 한계 명시. 길이 무보상.

    구버전 len/500은 장문일수록 고득점 — 길고 환각 있는 보고서가
    짧고 정확한 보고서를 이기는 역설(합성 프롬프트의 규칙 7과 정면충돌).
    """
    answer = trace.final_answer.strip()
    if not answer:
        return 0.0
    lower = answer.lower()
    score = 0.0
    # 인용 표기: URL, [n], [source: ...] 중 하나라도 본문에 존재
    if re.search(r"https?://|\[\d+\]|\[source", lower):
        score += 0.4
    # 구조: 마크다운 헤더 또는 불릿 3줄 이상
    bullet_lines = sum(1 for ln in answer.splitlines() if ln.strip().startswith(("-", "*", "•")))
    if re.search(r"^#{1,3}\s", answer, re.MULTILINE) or bullet_lines >= 3:
        score += 0.3
    # 한계/미검증 명시
    if any(m in lower for m in _UNCERTAINTY_MARKERS):
        score += 0.3
    return _cap(score)


# ─────────────────────────────────────────────────────────────
# Pairwise 상대비교 — 절대 임계값 대신 같은 질의를 수행한 엔진끼리 직접 대조
# ─────────────────────────────────────────────────────────────

def _pairwise_comparisons(
    traces: list[ResearchTrace], scores: dict[str, Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a, b in combinations(traces, 2):
        sa, sb = scores[a.engine_name], scores[b.engine_name]
        dom_a, dom_b = set(_citation_domains(a)), set(_citation_domains(b))
        official_a = {d for d in _all_domains(a) if _domain_tier(d) in ("official", "company_ir")}
        official_b = {d for d in _all_domains(b) if _domain_tier(d) in ("official", "company_ir")}

        verdicts: dict[str, str] = {}
        for cat, weight in WEIGHTS.items():
            va, vb = sa.get(cat), sb.get(cat)
            if va is None or vb is None:
                verdicts[cat] = "n/a"
            elif va - vb > weight * _VERDICT_MARGIN_RATIO:
                verdicts[cat] = a.engine_name
            elif vb - va > weight * _VERDICT_MARGIN_RATIO:
                verdicts[cat] = b.engine_name
            else:
                verdicts[cat] = "tie"

        delta = sa["total_normalized"] - sb["total_normalized"]
        if delta > _OVERALL_MARGIN:
            winner = a.engine_name
        elif delta < -_OVERALL_MARGIN:
            winner = b.engine_name
        else:
            winner = "tie"

        union = dom_a | dom_b
        out.append({
            "engines": [a.engine_name, b.engine_name],
            "citation_domain_jaccard": round(len(dom_a & dom_b) / len(union), 3) if union else 0.0,
            "unique_official_domains": {
                a.engine_name: sorted(official_a - official_b),
                b.engine_name: sorted(official_b - official_a),
            },
            "category_verdicts": verdicts,
            "overall": {
                "winner": winner,
                "normalized_delta": round(delta, 2),
            },
        })
    return out


# ─────────────────────────────────────────────────────────────
# FinVision 개선 원석 (pairwise 데이터 기반)
# ─────────────────────────────────────────────────────────────

def _finvision_improvement_raw_material(
    traces: list[ResearchTrace], scores: dict[str, Any]
) -> list[dict[str, str]]:
    finvision = next((t for t in traces if t.engine_name.lower() == "finvision"), None)
    if not finvision:
        return []

    external = [t for t in traces if t is not finvision]
    improvements: list[dict[str, str]] = []

    # 1) 상대만 찾은 공식 도메인 (티어 판정 기반 — 구버전 키워드 휴리스틱 대체)
    fin_domains = _all_domains(finvision)
    external_official = set().union(*[
        {d for d in _all_domains(t) if _domain_tier(d) in ("official", "company_ir")}
        for t in external
    ]) if external else set()
    for domain in sorted(external_official - fin_domains):
        improvements.append({
            "type": "missing_official_source",
            "description": f"External research checked {domain} but FinVision did not.",
            "suggested_fix": f"Add {domain} to official source discovery when the query context matches.",
            "priority": "high",
        })

    # 2) 상대는 증거로 뒷받침한 관할을 FinVision이 놓침
    fin_jurs = _evidenced_jurisdictions(finvision) | {
        j.strip().upper() for j in finvision.detected_jurisdictions
    }
    external_jurs = set().union(*[_evidenced_jurisdictions(t) for t in external]) if external else set()
    for jur in sorted(external_jurs - fin_jurs):
        improvements.append({
            "type": "missing_jurisdiction",
            "description": f"External research evidenced {jur} sources but FinVision did not.",
            "suggested_fix": f"Expand jurisdiction detector keywords and source registry coverage for {jur}.",
            "priority": "medium",
        })

    # 3) 갭 명시 여부
    if not finvision.unverified_gaps and any(t.unverified_gaps for t in external):
        improvements.append({
            "type": "gap_handling",
            "description": "External research explicitly listed unverified gaps but FinVision did not.",
            "suggested_fix": "Add a required uncertainty/gap section to FinVision synthesis output.",
            "priority": "medium",
        })

    # 4) 공식쿼리 '비중' 열세 (구버전은 개수 비교 — 쿼리를 늘리면 가려지는 지표였다)
    fin_ratio = _official_query_ratio(finvision)
    ext_ratios = [_official_query_ratio(t) for t in external]
    ext_best = max((r for r in ext_ratios if r is not None), default=None)
    if fin_ratio is not None and ext_best is not None and fin_ratio + 0.10 < ext_best:
        improvements.append({
            "type": "official_query_generation",
            "description": (
                f"FinVision official-source query ratio {fin_ratio:.0%} vs "
                f"best external {ext_best:.0%}."
            ),
            "suggested_fix": "Generate more site-specific queries for regulators, exchanges, and issuer IR pages.",
            "priority": "medium",
        })

    # 5) 저신뢰 인용 의존 (인용의 절반 이상이 low_trust/unknown 티어)
    fin_cites = _citation_domains(finvision)
    if fin_cites:
        weak = sum(1 for d in fin_cites if _domain_tier(d) in ("low_trust", "unknown"))
        if weak / len(fin_cites) >= 0.5:
            improvements.append({
                "type": "low_tier_citation_reliance",
                "description": (
                    f"{weak}/{len(fin_cites)} FinVision citations are low-trust or "
                    "unclassified domains."
                ),
                "suggested_fix": "Prioritize tier-1/IR sources in evidence ranking before citation.",
                "priority": "high",
            })

    return improvements


# ─────────────────────────────────────────────────────────────
# 리포트 렌더링
# ─────────────────────────────────────────────────────────────

def _fmt(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value}"


def _render_report(
    traces: list[ResearchTrace],
    scores: dict[str, Any],
    pairwise: list[dict[str, Any]],
    improvements: list[dict[str, str]],
) -> str:
    lines = [
        "# Deep Research Comparison Report",
        "",
        "채점은 전부 결정론적 '품질 비율' 지표다(카운트·자기신고 점수 미사용).",
        "N/A = 로그에 해당 데이터가 없어 측정 불가 — 총점(normalized)에서 가중치 제외.",
        "",
        "## Scores",
        "",
        "| Engine | Total(norm) | Jurisdiction | Queries | Official Sources | Evidence | Search | Cross Check | Gaps | Answer |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for engine, score in scores.items():
        lines.append(
            f"| {engine} | {score['total_normalized']} | {_fmt(score['jurisdiction_detection'])} | "
            f"{_fmt(score['query_generation'])} | {_fmt(score['official_source_coverage'])} | "
            f"{_fmt(score['evidence_quality'])} | {_fmt(score['search_behavior'])} | "
            f"{_fmt(score['cross_validation'])} | {_fmt(score['gap_handling'])} | "
            f"{_fmt(score['final_answer_structure'])} |"
        )

    lines.extend(["", "## Pairwise", ""])
    for pair in pairwise:
        a, b = pair["engines"]
        lines.extend([
            f"### {a} vs {b}",
            "",
            f"- Overall: **{pair['overall']['winner']}** (normalized Δ {pair['overall']['normalized_delta']})",
            f"- Citation domain Jaccard: {pair['citation_domain_jaccard']}",
        ])
        for engine, domains in pair["unique_official_domains"].items():
            if domains:
                lines.append(f"- Official domains only {engine} found: {', '.join(domains)}")
        verdict_str = ", ".join(f"{cat}→{v}" for cat, v in pair["category_verdicts"].items())
        lines.extend([f"- Category verdicts: {verdict_str}", ""])

    lines.extend(["## Trace Summary", ""])
    for trace in traces:
        lines.extend([
            f"### {trace.engine_name}",
            "",
            f"- Query: {trace.query or '(empty)'}",
            f"- Detected jurisdictions: {', '.join(trace.detected_jurisdictions) or '(none)'}",
            f"- Evidenced jurisdictions: {', '.join(sorted(_evidenced_jurisdictions(trace))) or '(none)'}",
            f"- Generated queries: {len(trace.generated_queries)}",
            f"- Official source queries: {len(trace.official_source_queries)}",
            f"- Sources found: {len(trace.sources_found)}",
            f"- Citations: {len(trace.citations)}",
            f"- Unverified gaps: {len(trace.unverified_gaps)}",
            "",
        ])

    lines.extend(["## FinVision Improvement Raw Material", ""])
    if improvements:
        for item in improvements:
            lines.extend([
                f"### {item['type']}",
                "",
                f"- Description: {item['description']}",
                f"- Suggested fix: {item['suggested_fix']}",
                f"- Priority: {item['priority']}",
                "",
            ])
    else:
        lines.append("No obvious FinVision improvement raw material found from the provided logs.")

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────
# 공용 헬퍼
# ─────────────────────────────────────────────────────────────

def _domain_of(source: SourceItem) -> Optional[str]:
    if source.url and "://" in source.url:
        return source.url.split("://", 1)[1].split("/", 1)[0].lower().removeprefix("www.")
    if source.title and "." in (source.title or "") and " " not in source.title.strip():
        return source.title.strip().lower().removeprefix("www.")
    return None


def _citation_domains(trace: ResearchTrace) -> list[str]:
    """인용 도메인 리스트(비중 계산용이라 중복 유지). 인용이 없으면 발견 소스로 폴백."""
    domains = [d for s in trace.citations for d in [_domain_of(s)] if d]
    if domains:
        return domains
    return [d for s in trace.sources_found for d in [_domain_of(s)] if d]


def _all_domains(trace: ResearchTrace) -> set[str]:
    domains = {d for s in trace.sources_found + trace.citations for d in [_domain_of(s)] if d}
    for raw in trace.searched_sources:
        cleaned = raw.strip().lower().removeprefix("www.")
        if cleaned and "." in cleaned and " " not in cleaned:
            domains.add(cleaned)
    return domains


def _domain_matches(domain: str, hints: tuple[str, ...]) -> bool:
    for hint in hints:
        if hint.startswith("."):
            if domain.endswith(hint) or (hint + "/") in domain:
                return True
        elif hint in domain:
            return True
    return False


def _domain_tier(domain: str) -> str:
    d = domain.lower()
    if _domain_matches(d, _TIER1_OFFICIAL) or d.endswith(".gov") or ".gov." in d:
        return "official"
    if _domain_matches(d, _LOW_TRUST):
        return "low_trust"
    if _domain_matches(d, _TIER2_MEDIA):
        return "media_t2"
    if _domain_matches(d, _TIER3_MEDIA):
        return "media_t3"
    if any(h in d for h in _COMPANY_IR):
        return "company_ir"
    return "unknown"


def _query_identifiers(user_query: str) -> set[str]:
    """사용자 질의에서 앵커 식별자 추출: 티커형 대문자, 고유명(TitleCase),
    5자 이상 소문자 토큰(예: 'indie'). 제네릭 불용어 제외."""
    identifiers: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9\-\.]*", user_query or ""):
        clean = token.strip(".-")
        if len(clean) < 2 or clean.lower() in _QUERY_STOPWORDS:
            continue
        if clean.isupper() and 2 <= len(clean) <= 6:
            identifiers.add(clean.lower())          # 티커 (INDI)
        elif clean[0].isupper() and len(clean) >= 3:
            identifiers.add(clean.lower())          # 고유명 (Wuxi)
        elif len(clean) >= 5:
            identifiers.add(clean.lower())          # 소문자 사명 (indie)
    return identifiers


def _is_anchored(query: str, identifiers: set[str]) -> bool:
    q = query.lower()
    return any(ident in q for ident in identifiers)


def _token_signature(query: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9가-힣一-鿿]+", query.lower()))


def _official_query_ratio(trace: ResearchTrace) -> Optional[float]:
    """공식 쿼리 비중. 분모는 generated∪official 합집합 — 파서가 두 리스트를
    독립적으로 채워(공식 쿼리가 generated에 없을 수 있음) 순진한 official/generated는
    1.0을 초과할 수 있다. 토큰 시그니처로 중복을 합쳐 실제 고유 쿼리 대비 비율."""
    official_sigs = {_token_signature(q) for q in trace.official_source_queries if q.strip()}
    all_sigs = {_token_signature(q) for q in trace.generated_queries if q.strip()} | official_sigs
    if not all_sigs:
        return None
    return len(official_sigs) / len(all_sigs)


def _cap(value: float) -> float:
    return max(0.0, min(1.0, value))
