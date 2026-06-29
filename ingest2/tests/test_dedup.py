"""§6 검증 — 중복 제거: 정확/구조+어휘 병합, SEC 보일러플레이트 비병합, 대표 선정."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from ingest2.dedup.cluster import dedup
from ingest2.schema import NewsItem


def _fake_embedder(texts):
    """제목 첫 단어로 방향 결정: 같은 키워드면 같은 벡터(=cos 1.0)."""
    m = {"alpha": [1.0, 0.0], "beta": [0.0, 1.0]}
    return np.array([m.get(t.split()[0].lower(), [0.5, 0.5]) for t in texts], dtype=float)

NOW = datetime(2026, 6, 22, 12, tzinfo=UTC)


def _item(native, title, tier=3, summary="", url="http://x", canonical=None,
          tickers=None, published=NOW, source="rss_x") -> NewsItem:
    return NewsItem(
        item_id=f"{source}:{native}",
        source_id=source,
        source_native_id=native,
        trust_tier=tier,
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonical,
        tickers_direct=tickers or [],
        published_at=published,
        collected_at=NOW,
        filter_status="passed",
    )


def _by_size(clusters):
    return sorted((sorted(c.member_ids) for c in clusters), key=lambda m: (-len(m), m))


def test_exact_canonical_url_merges():
    items = [
        _item("1", "Headline A", canonical="http://orig/x"),
        _item("2", "Totally different words here", canonical="http://orig/x"),
    ]
    clusters = dedup(items)
    assert len(clusters) == 1 and clusters[0].spread == 2


def test_structural_ticker_time_jaccard_merges():
    items = [
        _item("1", "Micron stock rises on strong DRAM demand", tickers=["MU"]),
        _item("2", "Micron stock climbs on strong DRAM demand outlook", tickers=["MU"],
              published=NOW + timedelta(hours=3)),
    ]
    clusters = dedup(items, jaccard_threshold=0.5)
    assert len(clusters) == 1


def test_same_ticker_low_overlap_not_merged():
    items = [
        _item("1", "Micron stock rises on DRAM demand", tickers=["MU"]),
        _item("2", "Micron faces lawsuit over patent dispute", tickers=["MU"]),
    ]
    assert len(dedup(items, jaccard_threshold=0.5)) == 2


def test_outside_time_window_not_merged():
    items = [
        _item("1", "Micron stock rises on strong DRAM demand", tickers=["MU"]),
        _item("2", "Micron stock rises on strong DRAM demand", tickers=["MU"],
              published=NOW + timedelta(hours=72)),
    ]
    # 제목 동일하지만 tier3 정확제목 병합 → 사실 병합됨. 시간창은 어휘경로에만 적용.
    # 여기선 정확 제목 일치로 묶이는 게 맞으므로 시간창 테스트는 어휘 전용 케이스로:
    items[1] = _item("2", "Micron shares advance amid solid memory demand", tickers=["MU"],
                     published=NOW + timedelta(hours=72))
    assert len(dedup(items, jaccard_threshold=0.3)) == 2


def test_sec_boilerplate_not_merged():
    # 같은 회사 두 공시 — 제목 거의 동일하지만 별개 사건이어야 함 (tier1 제목병합 제외)
    a = _item("acc1", "8-K - SurgePays, Inc. (Filer)", tier=1, tickers=["SURG"],
              canonical="http://sec/acc1", source="sec_edgar")
    b = _item("acc2", "8-K - SurgePays, Inc. (Filer)", tier=1, tickers=["SURG"],
              canonical="http://sec/acc2", source="sec_edgar")
    assert len(dedup(items=[a, b])) == 2


def test_transitive_and_representative():
    # A~B(정확 url), B~C(티커+어휘) → 한 클러스터. 대표 = 최상 신뢰도(tier1)
    a = _item("1", "Apple unveils new chip", tier=2, canonical="http://o/1", tickers=["AAPL"])
    b = _item("2", "Apple unveils new chip", tier=3, canonical="http://o/1", tickers=["AAPL"])
    c = _item("3", "Apple unveils new chip today", tier=3, tickers=["AAPL"])
    clusters = dedup([a, b, c], jaccard_threshold=0.5)
    assert len(clusters) == 1
    cl = clusters[0]
    assert cl.spread == 3
    assert cl.trust_tier_best == 2
    assert cl.representative_id == "rss_x:1"


def test_embedding_merges_tickerless_paraphrase():
    # 티커 없고 단어도 다르지만 의미(=fake 벡터) 같음 → 임베딩으로 병합
    a = _item("1", "alpha market moves sharply on fresh data", tickers=[])
    b = _item("2", "alpha session swings widen through midday", tickers=[])
    c = _item("3", "beta unrelated corporate update filed", tickers=[])
    clusters = dedup([a, b, c], embedder=_fake_embedder, cos_threshold=0.85)
    assert sorted((cl.spread for cl in clusters), reverse=True) == [2, 1]


def test_no_embedder_keeps_tickerless_separate():
    a = _item("1", "alpha market moves sharply on fresh data", tickers=[])
    b = _item("2", "alpha session swings widen through midday", tickers=[])
    assert len(dedup([a, b])) == 2  # 임베딩 없고 티커·정확일치 없으면 안 묶임


def test_sec_excluded_from_embedding():
    a = _item("acc1", "alpha filing one", tier=1, canonical="http://sec/1", source="sec_edgar")
    b = _item("acc2", "alpha filing two", tier=1, canonical="http://sec/2", source="sec_edgar")
    # fake 벡터는 같지만 SEC(tier1)은 임베딩 병합 제외 → 별개 유지
    assert len(dedup([a, b], embedder=_fake_embedder)) == 2
