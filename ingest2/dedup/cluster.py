"""3차 중복 제거 — 같은 사건을 한 EventCluster로 묶음.

기존 src/cluster(코사인 임베딩만)와 달리 ingest2의 구조적 신호를 활용한다:
  ① 정확 일치  : 같은 canonical_url / 정규화 제목
  ② 구조 블로킹 : 공유 직접티커 + 시간창(48h) 안에서만 비교 ("같은 기업·같은 시간대")
  ③ 어휘 유사  : 그 블록 안에서 제목+요약 토큰 Jaccard ≥ 임계값
  ④ 의미 유사  : (embedder 주입 시) 임베딩 코사인 ≥ 임계값 — 티커 없어도 의미로 병합
네 신호를 Union-Find로 전이 병합한다. embedder=None이면 ①~③ 결정론만.

SEC(tier 1) 공시는 제목이 보일러플레이트("8-K - Company")라 제목/어휘/임베딩 병합 시
서로 다른 공시가 잘못 뭉친다 → 제목·어휘·임베딩 병합은 tier≥2(뉴스)만 적용, SEC는 각
공시를 독립 사건으로 유지(병합은 정확 url만).
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from ..schema import EventCluster, NewsItem

DEFAULT_JACCARD = 0.5
DEFAULT_WINDOW_HOURS = 48
# 라이브 캘리브레이션: 같은 장마감 시황쌍이 cos 0.82였음(0.85는 놓침).
DEFAULT_COS = 0.80

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "as", "is",
    "at", "by", "its", "with", "amid", "after", "over", "from", "this", "that",
}
_FAR = datetime.max.replace(tzinfo=UTC)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def _norm_title(s: str) -> str:
    return " ".join(_WORD_RE.findall(s.lower()))


def _tokens(s: str) -> set[str]:
    return {w for w in _WORD_RE.findall(s.lower()) if len(w) > 2 and w not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _uniq(seq) -> list:
    out: list = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def dedup(
    items: list[NewsItem],
    jaccard_threshold: float = DEFAULT_JACCARD,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    embedder=None,
    cos_threshold: float = DEFAULT_COS,
) -> list[EventCluster]:
    n = len(items)
    if n == 0:
        return []
    uf = _UnionFind(n)
    toks = [_tokens(f"{it.title} {it.summary}") for it in items]
    norm = [_norm_title(it.title) for it in items]

    # ① 정확 일치. url은 전 tier, 제목은 tier≥2만(SEC 보일러플레이트 오병합 방지).
    url_seen: dict[str, int] = {}
    title_seen: dict[str, int] = {}
    for i, it in enumerate(items):
        cu = (it.canonical_url or "").strip().lower()
        if cu:
            if cu in url_seen:
                uf.union(i, url_seen[cu])
            else:
                url_seen[cu] = i
        if it.trust_tier >= 2 and norm[i]:
            if norm[i] in title_seen:
                uf.union(i, title_seen[norm[i]])
            else:
                title_seen[norm[i]] = i

    # ②③ 구조 블로킹(공유 티커) + 시간창 + 어휘 유사 — tier≥2만.
    ticker_idx: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        if it.trust_tier < 2:
            continue
        for t in it.tickers_direct:
            ticker_idx.setdefault(t, []).append(i)

    win = timedelta(hours=window_hours)
    checked: set[tuple[int, int]] = set()
    for idxs in ticker_idx.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                key = (i, j) if i < j else (j, i)
                if key in checked:
                    continue
                checked.add(key)
                if uf.find(i) == uf.find(j):
                    continue
                pi, pj = items[i].published_at, items[j].published_at
                if pi and pj and abs(pi - pj) > win:   # 같은 시간대 아님
                    continue
                if _jaccard(toks[i], toks[j]) >= jaccard_threshold:
                    uf.union(i, j)

    # ④ 의미 유사(임베딩) — tier≥2만, 시간창 내. 티커 없어도 의미로 병합("어려운 중복").
    #    SEC(tier1)은 공시 보일러플레이트가 서로 cos-유사라 제외(오병합 방지).
    if embedder is not None:
        news_idx = [i for i, it in enumerate(items) if it.trust_tier >= 2]
        if len(news_idx) >= 2:
            from sklearn.metrics.pairwise import cosine_similarity

            embs = embedder([f"{items[i].title}\n\n{items[i].summary}" for i in news_idx])
            sim = cosine_similarity(embs)
            for a in range(len(news_idx)):
                for b in range(a + 1, len(news_idx)):
                    i, j = news_idx[a], news_idx[b]
                    if uf.find(i) == uf.find(j):
                        continue
                    pi, pj = items[i].published_at, items[j].published_at
                    if pi and pj and abs(pi - pj) > win:
                        continue
                    if sim[a, b] >= cos_threshold:
                        uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    clusters = [_build_cluster([items[k] for k in g]) for g in groups.values()]
    clusters.sort(key=lambda c: (-c.spread, c.trust_tier_best))
    return clusters


def _build_cluster(members: list[NewsItem]) -> EventCluster:
    # 대표 = 최상 신뢰도(최소 tier) → 가장 먼저 발행
    rep = min(members, key=lambda m: (m.trust_tier, m.published_at or _FAR))
    pts = [m.published_at for m in members if m.published_at]
    longest = max((m.summary for m in members), key=len, default="")
    cluster_id = hashlib.sha1(
        "|".join(sorted(m.item_id for m in members)).encode()
    ).hexdigest()[:16]
    return EventCluster(
        cluster_id=cluster_id,
        member_ids=[m.item_id for m in members],
        representative_id=rep.item_id,
        title=rep.title,
        summary=longest or rep.title,
        tickers_direct=_uniq(t for m in members for t in m.tickers_direct),
        tickers_indirect=_uniq(t for m in members for t in m.tickers_indirect),
        event_types=_uniq(m.event_type for m in members),
        source_ids=_uniq(m.source_id for m in members),
        urls=_uniq(m.url for m in members),
        trust_tier_best=min(m.trust_tier for m in members),
        spread=len(members),
        published_start=min(pts) if pts else None,
        published_end=max(pts) if pts else None,
    )


def dedup_passed(news_store, **kwargs) -> list[EventCluster]:
    """필터 통과 항목만 모아 중복 제거."""
    items = [it for it in news_store.iter_items() if it.filter_status == "passed"]
    return dedup(items, **kwargs)
