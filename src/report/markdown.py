"""narratives + scored → Story 단위 Markdown 리포트 렌더링."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

DIRECTION_ICON = {
    "positive": "📈 호재",
    "negative": "📉 악재",
    "uncertain": "❓ 불확실",
}


def _domain(url: str) -> str:
    try:
        net = urlparse(url).netloc or url
        return net.removeprefix("www.")
    except Exception:
        return url[:30]


def _src_links(urls: list[str], limit: int = 4) -> str:
    if not urls:
        return "*(출처 없음)*"
    parts = [f"[{_domain(u)}]({u})" for u in urls[:limit]]
    extra = len(urls) - limit
    suffix = f" *(+{extra} more)*" if extra > 0 else ""
    return ", ".join(parts) + suffix


def _render_claim(claim: dict) -> str:
    """단일 CausalNode → 줄. 단일출처면 ⚠️ 표시. (deep research용, story 안에서도 재사용 가능)"""
    text = claim.get("claim", "").strip()
    sources = claim.get("source_urls", []) or []
    flag = "⚠️ *단일출처* — " if len(sources) == 1 else ""
    src = _src_links(sources)
    return f"- {flag}{text} ({src})"


def _render_edge(edge: dict, events_by_id: dict[str, dict]) -> list[str]:
    """인과 edge 한 줄 + 메커니즘."""
    from_id = edge.get("from_event_id", "")
    to_id = edge.get("to_event_id", "")
    from_ev = events_by_id.get(from_id, {})
    to_ev = events_by_id.get(to_id, {})
    from_t = (from_ev.get("title") or from_id)[:70]
    to_t = (to_ev.get("title") or to_id)[:70]
    conf = edge.get("confidence", 0.0)
    direction = edge.get("direction", "uncertain")
    mech = edge.get("mechanism", "")
    by = edge.get("inferred_by", "")
    method = "🤖 pairwise LLM" if by == "pairwise_llm" else "📎 claim 매칭"
    dir_label = DIRECTION_ICON.get(direction, direction)

    lines = [f"- **{from_t}** → **{to_t}**"]
    lines.append(f"  ↳ 신뢰도 {conf:.2f}  |  {dir_label}  |  {method}")
    if mech:
        lines.append(f"  ↳ 메커니즘: {mech}")
    return lines


def _render_story(rank: int, story: dict, events_by_id: dict[str, dict]) -> str:
    title = story.get("title") or "(제목 없음)"
    impact = story.get("aggregated_impact", 0.0)
    direction = story.get("direction", "uncertain")
    confidence = story.get("confidence", 0.0)
    event_ids = story.get("event_ids", []) or []
    edges = story.get("edges", []) or []
    tickers = story.get("affected_tickers", []) or []
    sources = story.get("all_sources", []) or []
    narrative_short = story.get("narrative_short", "")
    narrative_long = story.get("narrative_long", "")

    dir_label = DIRECTION_ICON.get(direction, direction)

    lines: list[str] = []
    lines.append(f"### {rank}. [영향력 {impact:.2f}] {title}")
    lines.append(
        f"- **방향**: {dir_label}  |  **신뢰도**: {confidence:.2f}  |  "
        f"**포함 이벤트**: {len(event_ids)}개  |  **인과 링크**: {len(edges)}개  |  "
        f"**출처**: {len(sources)}개"
    )
    lines.append(f"- **언급 종목**: {', '.join(tickers[:10]) or '(없음)'}")

    if narrative_short:
        lines.append("")
        lines.append(f"> {narrative_short.strip()}")

    if edges:
        lines.append("")
        lines.append("#### 🔗 인과 그래프")
        for e in edges:
            lines.extend(_render_edge(e, events_by_id))

    if narrative_long:
        lines.append("")
        lines.append("#### 📝 상세 분석")
        lines.append(narrative_long.strip())

    if event_ids:
        lines.append("")
        lines.append("#### 📰 구성 이벤트")
        for i, eid in enumerate(event_ids, 1):
            ev = events_by_id.get(eid)
            if not ev:
                continue
            t = (ev.get("title") or "")[:100]
            date = (ev.get("occurred_at") or "")[:10]
            ev_tickers = (ev.get("tickers_mentioned") or [])[:6]
            spread = ev.get("spread", 0)
            lines.append(
                f"{i}. **{t}** _(spread {spread}, {date})_  — {', '.join(ev_tickers)}"
            )

    if sources:
        lines.append("")
        lines.append(f"#### 🌐 출처 ({len(sources)}개)")
        for u in sources[:15]:
            lines.append(f"- [{_domain(u)}]({u})")
        if len(sources) > 15:
            lines.append(f"- *(+{len(sources) - 15}개 더)*")

    return "\n".join(lines)


def render_report(
    ticker: str,
    stories: list[dict],
    events_by_id: dict[str, dict],
    *,
    top_n: int = 10,
    generated_at: datetime | None = None,
) -> str:
    """Story 리스트 + 이벤트 인덱스 → Markdown 리포트."""
    generated_at = generated_at or datetime.now()
    top = stories[:top_n]
    n_events = sum(len(s.get("event_ids", []) or []) for s in top)
    n_edges = sum(len(s.get("edges", []) or []) for s in top)
    n_narrated = sum(1 for s in top if s.get("narrative_long"))

    head = (
        f"# {ticker} 심층 리서치 보고서 ({generated_at:%Y-%m-%d})\n\n"
        f"_생성: {generated_at:%Y-%m-%d %H:%M:%S}_  \n"
        f"_Top {len(top)} 스토리_  |  _포함 이벤트: {n_events}건_  |  "
        f"_인과 링크: {n_edges}건_  |  _내러티브 생성: {n_narrated}건_\n\n"
        f"---\n\n"
        f"## 영향력 상위 Top {len(top)} 스토리\n\n"
    )

    parts = [_render_story(i + 1, s, events_by_id) for i, s in enumerate(top)]
    body = "\n\n---\n\n".join(parts)
    return head + body + "\n"
