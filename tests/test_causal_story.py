from datetime import UTC, datetime

from src.causal.schema import Story
from src.causal.story import _NARRATIVE_PROMPT, _format_events_block
from src.ingest.schema import Event


def test_format_events_block_keeps_quiet_period_context_after_300_chars():
    summary = (
        "SpaceX is poised for significant stock movement on July 7, 2026, due to "
        "two converging catalysts: eligibility for inclusion in the Nasdaq-100 "
        "index, which will trigger automatic buying from index funds, and the end "
        "of the 25-calendar-day quiet period for participating underwriters, "
        "allowing them to issue buy recommendations and price targets. However, "
        "insider share lockups expire after the first quarterly earnings release."
    )
    event = Event(
        id="e1",
        title="2 Reasons July 7 Is Shaping Up as a Monster Day for SpaceX",
        summary=summary,
        occurred_at=datetime(2026, 6, 29, tzinfo=UTC),
        source_urls=["https://example.com/spacex"],
        publishers=["The Motley Fool"],
        tickers_mentioned=["SPCX"],
        spread=1,
    )
    story = Story(id="s1", event_ids=["e1"], affected_tickers=["SPCX"])

    block = _format_events_block(story, {"e1": event})

    assert "buy recommendations and price targets" in block
    assert "insider share lockups expire" in block


def test_narrative_prompt_generates_title_after_narrative():
    """제목↔본문 용어 불일치를 구조적으로 방지: 출력 순서상 title 은 narrative 뒤에 온다.

    (특정 용어를 나열해 경고하는 band-aid 대신, 본문을 먼저 확정하고 그 어휘를 그대로
    재사용해 제목을 뽑게 하는 일반 규칙으로 대체함.)
    """
    # JSON 출력 스키마에서 title 이 narrative_long 보다 뒤에 위치해야 함
    assert _NARRATIVE_PROMPT.index('"narrative_long"') < _NARRATIVE_PROMPT.index('"title"')
    # 제목은 본문의 핵심 용어를 그대로 재사용하라는 일반 지시가 있어야 함
    assert "Reuse the exact key terms" in _NARRATIVE_PROMPT


def test_glossary_maps_quiet_period_distinctly():
    """termbase 는 quiet period → 침묵기간, lock-up → 보호예수 를 구분해야 한다."""
    from src.glossary import render_glossary_block

    block = render_glossary_block()
    assert "quiet period" in block and "침묵기간" in block
    assert "lock-up" in block and "보호예수" in block
    # quiet period 줄은 '보호예수 아님'으로 혼동어를 배제해야 함
    qp_line = next(ln for ln in block.splitlines() if "quiet period" in ln)
    assert "침묵기간" in qp_line and "보호예수" in qp_line


def test_narrative_prompt_injects_glossary():
    """프롬프트 렌더 시 glossary 가 실제로 주입돼 혼동쌍 매핑이 포함돼야 한다."""
    from src.glossary import render_glossary_block

    rendered = _NARRATIVE_PROMPT.format(
        tickers_direct="SPCX", tickers_indirect="(none)", direction="positive",
        n_events=1, n_edges=0, events_block="E", edges_block="X", claims_block="C",
        glossary_block=render_glossary_block(),
    )
    assert "침묵기간" in rendered and "quiet period" in rendered
