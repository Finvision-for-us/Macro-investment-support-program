"""이벤트 간 인과 edge 추론.

두 가지 방법:
1. pairwise_llm: Top N 이벤트 쌍에 사전 필터(티커/시간/유사도) 적용 후 LLM 검증
2. deep_research_claim: 기존 deep research의 direct_causes 텍스트를 다른 이벤트
   임베딩과 매칭해 후보 추출 → LLM이 일치 여부 검증 + 한국어 메커니즘 생성
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

import numpy as np
from google.genai import types
from pydantic import BaseModel, Field
from sklearn.metrics.pairwise import cosine_similarity

from src.causal.schema import CausalEdge
from src.cluster.embed import embed_texts
from src.config import GEMINI_MODEL_FAST
from src.ingest.schema import Event
from src.llm import gemini_client, retry_gemini

# ---- 필터 임계값 ----
TOP_N = 20
TIME_WINDOW_DAYS = 14
EMBEDDING_SIM_THRESHOLD = 0.55
LLM_CONFIDENCE_THRESHOLD = 0.5
CLAIM_MATCH_THRESHOLD = 0.62  # claim ↔ event 텍스트 매칭 임계값 (1차 후보)
CLAIM_VERIFY_CONFIDENCE_THRESHOLD = 0.5  # LLM 검증 후 채택 임계값
MAX_CANDIDATE_PAIRS = 120  # LLM 호출 상한 — 유사도↓ 티커 순으로 상위만
# 배치 크기: 후보쌍을 묶어 LLM 1회로 검증 → 무료티어 RPD 절감.
# 쌍 프롬프트가 중간 크기(양쪽 이벤트 요약)라 6으로 균형. 배치 실패·개수 불일치 시
# 단건 폴백하므로 정확성은 보존.
DEFAULT_PAIR_BATCH_SIZE = 6


# ============================================================
# 사전 필터링
# ============================================================
def _to_embed_text(ev: Event) -> str:
    return f"{ev.title}\n\n{ev.summary[:500]}"


def candidate_pairs(
    events: list[Event],
    embeddings: np.ndarray,
) -> list[dict]:
    """공유 티커 or 의미 유사도가 있는 (i, j) 쌍 — 상위 MAX_CANDIDATE_PAIRS만 반환.

    time_close 단독은 제거: 수집 창이 12~48h이면 모든 쌍이 통과해
    LLM 호출이 폭발하는 문제를 방지한다. 인과 후보는 내용 유사성/티커 공유가
    기준이어야 한다.
    """
    n = len(events)
    if n < 2:
        return []

    sim = cosine_similarity(embeddings) if embeddings.size else None
    pairs: list[dict] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = events[i], events[j]
            shared = set(a.tickers_mentioned) & set(b.tickers_mentioned)
            sim_val = float(sim[i, j]) if sim is not None else 0.0
            sim_close = sim_val >= EMBEDDING_SIM_THRESHOLD

            if shared or sim_close:
                pairs.append(
                    {
                        "i": i,
                        "j": j,
                        "shared_tickers": sorted(shared),
                        "sim": sim_val,
                    }
                )

    # 공유 티커 수 → 유사도 내림차순으로 정렬 후 상한 적용
    pairs.sort(key=lambda p: (-len(p["shared_tickers"]), -p["sim"]))
    return pairs[:MAX_CANDIDATE_PAIRS]


# ============================================================
# 방법 1: pairwise LLM 검증
# ============================================================
_PAIR_PROMPT = """You evaluate the causal relationship between two market events.

EVENT A
Title: {a_title}
Summary: {a_summary}
Tickers: {a_tickers}
Date: {a_date}

EVENT B
Title: {b_title}
Summary: {b_summary}
Tickers: {b_tickers}
Date: {b_date}

Question: Is there a DIRECT causal link?
- "A_causes_B": developments in A directly led to B
- "B_causes_A": developments in B directly led to A
- "correlated_not_causal": both stem from a common cause but no direct causation
- "unrelated": no meaningful relationship

Be CONSERVATIVE. Only choose A_causes_B / B_causes_A when there's a clear mechanism.
Temporal proximity alone is NOT causation. Shared tickers alone is NOT causation.

LANGUAGE: Write "mechanism" in Korean (한국어 한 문장).

Return ONLY JSON:
{{
  "relationship": "A_causes_B|B_causes_A|correlated_not_causal|unrelated",
  "confidence": 0.0,
  "mechanism": "...",
  "direction": "positive|negative|uncertain"
}}
"""


def _strip_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    return m.group(1) if m else text


class _PairVerdict(BaseModel):
    """한 쌍 인과 판정 — 배치 구조화 출력의 원소."""

    relationship: Literal[
        "A_causes_B", "B_causes_A", "correlated_not_causal", "unrelated"
    ] = "unrelated"
    confidence: float = 0.0
    mechanism: str = ""
    direction: Literal["positive", "negative", "uncertain"] = "uncertain"


class _PairVerdictBatch(BaseModel):
    """여러 쌍을 LLM 1회로 판정 — verdicts[k]가 k번째 쌍 결과."""

    verdicts: list[_PairVerdict] = Field(default_factory=list)


_PAIR_BATCH_HEADER = """You evaluate the causal relationship between PAIRS of market events.
You will receive MULTIPLE pairs below, each under a "===== PAIR k =====" marker.
For EACH pair, choose the relationship:
- "A_causes_B": event A causes event B
- "B_causes_A": event B causes event A
- "correlated_not_causal": related but no direct causation
- "unrelated": no meaningful relationship

Be CONSERVATIVE. Only choose A_causes_B / B_causes_A when there's a clear mechanism.
Temporal proximity alone is NOT causation. Shared tickers alone is NOT causation.

LANGUAGE: Write each "mechanism" in Korean (한국어 한 문장).

Return JSON with a "verdicts" array of EXACTLY {n} objects — one per pair, in the SAME
ORDER as the pairs. Each object:
{{"relationship": "...", "confidence": 0.0, "mechanism": "...", "direction": "positive|negative|uncertain"}}
"""


def _pair_block(idx: int, a: Event, b: Event) -> str:
    return (
        f"===== PAIR {idx} =====\n"
        f"EVENT A\nTitle: {a.title}\nSummary: {a.summary[:600]}\n"
        f"Tickers: {', '.join(a.tickers_mentioned[:6]) or '(none)'}\n"
        f"Date: {a.occurred_at.isoformat()}\n\n"
        f"EVENT B\nTitle: {b.title}\nSummary: {b.summary[:600]}\n"
        f"Tickers: {', '.join(b.tickers_mentioned[:6]) or '(none)'}\n"
        f"Date: {b.occurred_at.isoformat()}"
    )


def _build_pair_batch_prompt(pairs_ab: list[tuple[Event, Event]]) -> str:
    blocks = [_pair_block(i, a, b) for i, (a, b) in enumerate(pairs_ab, 1)]
    return _PAIR_BATCH_HEADER.format(n=len(pairs_ab)) + "\n" + "\n\n".join(blocks)


@retry_gemini
def _check_pair(a: Event, b: Event) -> dict:
    client = gemini_client()
    prompt = _PAIR_PROMPT.format(
        a_title=a.title,
        a_summary=a.summary[:600],
        a_tickers=", ".join(a.tickers_mentioned[:6]) or "(none)",
        a_date=a.occurred_at.isoformat(),
        b_title=b.title,
        b_summary=b.summary[:600],
        b_tickers=", ".join(b.tickers_mentioned[:6]) or "(none)",
        b_date=b.occurred_at.isoformat(),
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return json.loads(_strip_json(response.text or "{}"))


@retry_gemini
def _check_pairs_batch(pairs_ab: list[tuple[Event, Event]]) -> list[dict]:
    """여러 쌍 → LLM 1회 구조화 호출 → list[dict](verdict). 순서 보존."""
    if not pairs_ab:
        return []
    client = gemini_client()
    resp = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=_build_pair_batch_prompt(pairs_ab),
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=_PairVerdictBatch,
        ),
    )
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, _PairVerdictBatch):
        batch = parsed
    else:
        batch = _PairVerdictBatch.model_validate_json(resp.text or "{}")
    return [v.model_dump() for v in batch.verdicts]


def _verdict_to_edge(res: dict, a: Event, b: Event) -> CausalEdge | None:
    """LLM 판정(dict) → CausalEdge. 인과 아님/저신뢰는 None. 단건·배치 공용."""
    rel = res.get("relationship", "unrelated")
    conf = float(res.get("confidence", 0.0) or 0.0)
    if rel not in ("A_causes_B", "B_causes_A") or conf < LLM_CONFIDENCE_THRESHOLD:
        return None
    from_id = a.id if rel == "A_causes_B" else b.id
    to_id = b.id if rel == "A_causes_B" else a.id
    return CausalEdge(
        from_event_id=from_id,
        to_event_id=to_id,
        confidence=conf,
        direction=res.get("direction", "uncertain"),
        mechanism=str(res.get("mechanism", ""))[:300],
        source_urls=[],
        inferred_by="pairwise_llm",
    )


def infer_pairwise(
    events: list[Event],
    embeddings: np.ndarray,
    *,
    on_progress=None,
    pair_fn=None,
    batch_pair_fn=None,
    batch_size: int = DEFAULT_PAIR_BATCH_SIZE,
) -> list[CausalEdge]:
    """Top N 이벤트 쌍에 pairwise LLM 인과 검증.

    후보쌍을 batch_size개씩 묶어 LLM 1회로 판정(무료티어 RPD 절감). 배치 실패·개수
    불일치 시 pair_fn으로 단건 폴백하므로 정확성은 보존된다.

    - pair_fn: (a, b) -> dict 단건 검증. 기본 실 Gemini(_check_pair). 테스트 주입용.
    - batch_pair_fn: [(a, b), ...] -> [dict, ...] 배치 검증. 기본 실 Gemini.
    """
    pairs = candidate_pairs(events, embeddings)
    if not pairs:
        return []

    pair_fn = pair_fn or _check_pair
    if batch_pair_fn is None:
        batch_pair_fn = _check_pairs_batch

    ab = [(events[p["i"]], events[p["j"]]) for p in pairs]
    size = max(1, batch_size)
    chunks = [ab[i:i + size] for i in range(0, len(ab), size)]

    def _process_chunk(chunk: list[tuple[Event, Event]]) -> list[CausalEdge]:
        try:
            verdicts = batch_pair_fn(chunk)
            if len(verdicts) != len(chunk):
                raise ValueError(f"쌍 배치 개수 불일치: {len(verdicts)} != {len(chunk)}")
        except Exception:  # noqa: BLE001
            verdicts = None  # 단건 폴백

        out: list[CausalEdge] = []
        if verdicts is None:
            for a, b in chunk:
                try:
                    res = pair_fn(a, b)
                except Exception:  # noqa: BLE001
                    continue
                edge = _verdict_to_edge(res, a, b)
                if edge is not None:
                    out.append(edge)
        else:
            for (a, b), res in zip(chunk, verdicts):
                edge = _verdict_to_edge(res, a, b)
                if edge is not None:
                    out.append(edge)
        if on_progress is not None:
            try:
                on_progress(len(chunk))
            except Exception:  # noqa: BLE001
                pass
        return out

    edges: list[CausalEdge] = []
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
        for chunk_edges in pool.map(_process_chunk, chunks):
            edges.extend(chunk_edges)
    return edges


# ============================================================
# 방법 2: deep research의 claim 재활용 + LLM 한국어 검증
# ============================================================
_CLAIM_VERIFY_PROMPT = """You verify whether one event causally relates to a claim
recorded as a "direct cause" of another event.

CAUSE CANDIDATE (Event A)
Title: {a_title}
Summary: {a_summary}

EFFECT (Event B)
Title: {b_title}
Summary: {b_summary}

CLAIM (recorded in B's direct_causes section)
{claim_text}

QUESTIONS
1. Does Event A actually describe the same cause stated in the CLAIM, or is the
   match coincidental (different topic with overlapping wording)?
2. If genuine, write a clean mechanism (한국어 한 문장) explaining how A leads to B,
   integrating the claim's substance.
3. What's the direction of market impact on the affected stocks?

LANGUAGE: Write "mechanism" in natural Korean (한국어). Keep tickers/companies/numbers
in original form (NVDA, $80B, 110x). Do NOT paste raw English claim text.

Return ONLY JSON:
{{
  "is_match": true,
  "mechanism": "...",
  "direction": "positive|negative|uncertain",
  "confidence": 0.0
}}
"""


@retry_gemini
def _verify_claim_edge(cause_ev: Event, effect_ev: Event, claim_text: str) -> dict:
    client = gemini_client()
    prompt = _CLAIM_VERIFY_PROMPT.format(
        a_title=cause_ev.title,
        a_summary=cause_ev.summary[:500],
        b_title=effect_ev.title,
        b_summary=effect_ev.summary[:500],
        claim_text=claim_text[:500],
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return json.loads(_strip_json(response.text or "{}"))


def infer_from_claims(
    events: list[Event],
    event_embeddings: np.ndarray,
    deep_reports: dict[str, dict],
    *,
    on_progress=None,
) -> list[CausalEdge]:
    """deep research direct_causes 텍스트를 다른 이벤트와 임베딩 매칭한 뒤,
    LLM이 (a) 실제 일치 여부 검증, (b) 한국어 메커니즘 생성, (c) 방향 판정.
    """
    if not deep_reports or len(events) < 2:
        return []

    id_to_idx = {ev.id: i for i, ev in enumerate(events)}

    # 1) 모든 claim 텍스트 수집
    claim_records: list[tuple[str, str, list[str]]] = []
    for target_id, report in deep_reports.items():
        if target_id not in id_to_idx:
            continue
        for c in report.get("direct_causes", []) or []:
            claim_text = (c or {}).get("claim", "")
            if claim_text:
                claim_records.append(
                    (claim_text, target_id, (c or {}).get("source_urls", []) or [])
                )

    if not claim_records:
        return []

    # 2) claim 임베딩 + 매칭
    claim_embeds = embed_texts([r[0] for r in claim_records])
    sim = cosine_similarity(claim_embeds, event_embeddings)

    # 3) 후보 → LLM 검증
    edges: list[CausalEdge] = []
    candidates: list[tuple[Event, str, str, list[str], float]] = []
    for k, (claim_text, target_id, src_urls) in enumerate(claim_records):
        target_idx = id_to_idx[target_id]
        scores = sim[k].copy()
        scores[target_idx] = -1.0
        best_j = int(np.argmax(scores))
        best_score = float(scores[best_j])
        if best_score < CLAIM_MATCH_THRESHOLD:
            continue
        cause_ev = events[best_j]
        effect_ev = events[target_idx]
        candidates.append((cause_ev, effect_ev.id, claim_text, src_urls, best_score))

    for idx, (cause_ev, target_id, claim_text, src_urls, sim_score) in enumerate(candidates, 1):
        effect_ev = events[id_to_idx[target_id]]
        if on_progress:
            on_progress(idx, len(candidates), cause_ev, effect_ev)
        try:
            res = _verify_claim_edge(cause_ev, effect_ev, claim_text)
        except Exception as e:  # noqa: BLE001
            if on_progress:
                on_progress(idx, len(candidates), cause_ev, effect_ev, error=str(e)[:80])
            continue

        if not res.get("is_match"):
            continue
        llm_conf = float(res.get("confidence", 0.0) or 0.0)
        if llm_conf < CLAIM_VERIFY_CONFIDENCE_THRESHOLD:
            continue

        mechanism = str(res.get("mechanism") or "").strip()
        if not mechanism:
            continue
        direction = res.get("direction", "uncertain")
        if direction not in ("positive", "negative", "uncertain"):
            direction = "uncertain"

        # 임베딩 sim + LLM conf의 평균을 최종 confidence로
        final_conf = min(1.0, 0.5 * sim_score + 0.5 * llm_conf)

        edges.append(
            CausalEdge(
                from_event_id=cause_ev.id,
                to_event_id=target_id,
                confidence=final_conf,
                direction=direction,
                mechanism=mechanism[:400],
                source_urls=src_urls[:5],
                inferred_by="deep_research_claim",
            )
        )
    return edges


# ============================================================
# 합치기 + 중복 제거
# ============================================================
def merge_edges(edges: list[CausalEdge]) -> list[CausalEdge]:
    """같은 (from, to) 쌍에 여러 edge가 있으면 confidence 높은 쪽 채택."""
    best: dict[tuple[str, str], CausalEdge] = {}
    for e in edges:
        key = (e.from_event_id, e.to_event_id)
        if key not in best or e.confidence > best[key].confidence:
            best[key] = e
    return list(best.values())


def event_embeddings(events: list[Event]) -> np.ndarray:
    """이벤트 N개 → 임베딩 행렬 (N, D)."""
    return embed_texts([_to_embed_text(e) for e in events])
