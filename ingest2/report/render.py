"""최종 후보(§9 RankedStory) → HTML 대시보드 + JSON 덤프.

코드로 생성하므로 후보 수에 무관하게 확장된다. 매 라이브 실행마다 같은 화면을
재생성할 수 있어 denylist 적용 전후 비교 등에 그대로 재사용한다.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.causal.schema import Story

from ..candidates.pipeline import CandidateResult
from ..rank.final import RankedStory

_DIR_LABEL = {"positive": "상승", "negative": "하락", "uncertain": "불확실"}


@dataclass(frozen=True)
class ReportPaths:
    html: Path
    json: Path


def _esc(text: str) -> str:
    return html.escape(text or "")


def _kind(story: Story) -> str:
    return "STORY" if len(story.event_ids) > 1 else "SIGNAL"


def _has_deep(story: Story, result: CandidateResult) -> bool:
    return any(eid in result.deep_reports for eid in story.event_ids)


def _to_record(rank: int, item: RankedStory, result: CandidateResult) -> dict:
    """JSON 직렬화용 평탄한 레코드 — 화면/외부소비 공용."""
    story = item.story
    chain = []
    for edge in story.edges:
        frm = result.events_by_id.get(edge.from_event_id)
        to = result.events_by_id.get(edge.to_event_id)
        chain.append(
            {
                "from": frm.title if frm else edge.from_event_id[:8],
                "to": to.title if to else edge.to_event_id[:8],
                "mechanism": edge.mechanism,
                "confidence": round(edge.confidence, 3),
                "direction": edge.direction,
                "inferred_by": edge.inferred_by,
            }
        )
    events = []
    for eid in story.event_ids:
        ev = result.events_by_id.get(eid)
        if ev:
            events.append(
                {
                    "title": ev.title,
                    "publishers": ev.publishers,
                    "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
                    "url": ev.source_urls[0] if ev.source_urls else "",
                }
            )
    ripples = [
        {
            "tier": r.tier,
            "target": r.target,
            "direction": r.direction,
            "horizon": r.horizon,
            "confidence": round(r.confidence, 3),
            "mechanism": r.mechanism,
        }
        for r in story.ripple_effects
    ]
    return {
        "rank": rank,
        "kind": _kind(story),
        "final_score": round(item.final_score, 4),
        "impact": round(story.aggregated_impact, 4),
        "direction": story.direction,
        "confidence": round(story.confidence, 3),
        "n_events": len(story.event_ids),
        "n_sources": len(story.all_sources),
        "has_deep": _has_deep(story, result),
        "tickers": story.affected_tickers,
        "title": story.title,
        "narrative_short": story.narrative_short,
        "narrative_long": story.narrative_long,
        "reasons": item.reasons,
        "chain": chain,
        "events": events,
        "ripples": ripples,
        "sources": story.all_sources,
    }


def to_records(final_items: list[RankedStory], result: CandidateResult) -> list[dict]:
    return [_to_record(i, item, result) for i, item in enumerate(final_items, 1)]


# ----------------------------- HTML -----------------------------

_CSS = """
:root{--bg:#0d1117;--card:#161b22;--card2:#1c2230;--bd:#2d333b;--fg:#e6edf3;
--mut:#8b949e;--pos:#3fb950;--neg:#f85149;--unc:#8b949e;--acc:#58a6ff;--chip:#21262d;}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,Segoe UI,Roboto,'Malgun Gothic',sans-serif;}
.wrap{max-width:960px;margin:0 auto;padding:28px 18px 64px;}
header h1{margin:0 0 4px;font-size:22px;letter-spacing:-.3px;}
header .sub{color:var(--mut);font-size:13px;}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 26px;}
.stat{background:var(--card);border:1px solid var(--bd);border-radius:8px;
padding:8px 12px;font-size:12px;color:var(--mut);}
.stat b{color:var(--fg);font-size:15px;font-weight:600;}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;
padding:16px 18px;margin-bottom:14px;position:relative;}
.row1{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;}
.rank{font-size:20px;font-weight:700;color:var(--mut);min-width:32px;}
.badge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;
letter-spacing:.4px;}
.b-story{background:rgba(88,166,255,.16);color:var(--acc);}
.b-signal{background:var(--chip);color:var(--mut);}
.dir{font-size:12px;font-weight:700;padding:2px 9px;border-radius:6px;}
.d-positive{background:rgba(63,185,80,.16);color:var(--pos);}
.d-negative{background:rgba(248,81,73,.16);color:var(--neg);}
.d-uncertain{background:rgba(139,148,158,.16);color:var(--unc);}
.spacer{flex:1;}
.score{font-size:13px;color:var(--mut);}
.score b{color:var(--fg);font-size:16px;}
.bar{height:5px;border-radius:3px;background:var(--bd);margin:8px 0 12px;overflow:hidden;}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff);}
.title{font-size:16px;font-weight:600;margin:2px 0 8px;line-height:1.4;}
.tickers{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px;}
.tk{background:var(--chip);border:1px solid var(--bd);border-radius:6px;
padding:1px 8px;font-size:12px;font-weight:600;color:var(--acc);}
.narr{color:#c9d1d9;font-size:13.5px;margin:6px 0 12px;}
.chain{background:var(--card2);border:1px solid var(--bd);border-radius:8px;
padding:10px 12px;margin:10px 0;font-size:12.5px;}
.chain .hd{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.5px;
margin-bottom:6px;}
.edge{margin:4px 0;}
.edge .ev{color:var(--fg);}
.edge .ar{color:var(--acc);font-weight:700;margin:0 5px;}
.edge .mech{color:var(--mut);display:block;margin:1px 0 0 2px;font-size:12px;}
.ripples{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}
.rip{font-size:11.5px;border:1px solid var(--bd);border-radius:6px;padding:3px 8px;
background:var(--card2);}
.rip .t{color:var(--mut);}
.meta{display:flex;gap:14px;flex-wrap:wrap;color:var(--mut);font-size:12px;
margin-top:10px;padding-top:10px;border-top:1px solid var(--bd);}
.reasons{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;
color:var(--mut);}
details{margin-top:8px;}summary{cursor:pointer;color:var(--acc);font-size:12px;}
details ul{margin:6px 0;padding-left:18px;}
details a{color:var(--acc);word-break:break-all;font-size:12px;}
.empty{color:var(--mut);text-align:center;padding:40px;}
"""


def _stat(label: str, value) -> str:
    return f'<div class="stat">{_esc(label)} <b>{_esc(str(value))}</b></div>'


def _card_html(rec: dict) -> str:
    dirc = rec["direction"]
    dir_label = _DIR_LABEL.get(dirc, dirc)
    tickers = "".join(f'<span class="tk">{_esc(t)}</span>' for t in rec["tickers"])
    tickers_html = f'<div class="tickers">{tickers}</div>' if tickers else ""

    chain_html = ""
    if rec["chain"]:
        edges = "".join(
            f'<div class="edge"><span class="ev">{_esc(e["from"])}</span>'
            f'<span class="ar">→</span><span class="ev">{_esc(e["to"])}</span>'
            f'<span class="mech">{_esc(e["mechanism"])} '
            f'· conf {e["confidence"]:.2f} · {_esc(e["inferred_by"])}</span></div>'
            for e in rec["chain"]
        )
        chain_html = (
            f'<div class="chain"><div class="hd">인과 체인 '
            f'({len(rec["chain"])})</div>{edges}</div>'
        )

    ripples_html = ""
    if rec["ripples"]:
        chips = "".join(
            f'<span class="rip"><span class="t">{_esc(r["tier"])}·{_esc(r["horizon"])}</span> '
            f'{_esc(r["target"])} {_DIR_LABEL.get(r["direction"], r["direction"])}</span>'
            for r in rec["ripples"]
        )
        ripples_html = f'<div class="ripples">{chips}</div>'

    sources_html = ""
    if rec["sources"]:
        links = "".join(
            f'<li><a href="{_esc(u)}" target="_blank" rel="noopener">{_esc(u)}</a></li>'
            for u in rec["sources"][:20]
        )
        sources_html = (
            f'<details><summary>출처 {len(rec["sources"])}건</summary>'
            f"<ul>{links}</ul></details>"
        )

    narr = (
        f'<div class="narr">{_esc(rec["narrative_short"])}</div>'
        if rec["narrative_short"]
        else ""
    )
    title = rec["title"] or (rec["events"][0]["title"] if rec["events"] else "(제목 없음)")

    return f"""<div class="card">
  <div class="row1">
    <span class="rank">{rec["rank"]}</span>
    <span class="badge {'b-story' if rec['kind']=='STORY' else 'b-signal'}">{rec["kind"]}</span>
    <span class="dir d-{dirc}">{dir_label}</span>
    <span class="spacer"></span>
    <span class="score">final <b>{rec["final_score"]:.3f}</b></span>
    <span class="score">impact {rec["impact"]:.3f}</span>
    <span class="score">conf {rec["confidence"]:.2f}</span>
  </div>
  <div class="bar"><i style="width:{min(100, rec['final_score']*100):.0f}%"></i></div>
  <div class="title">{_esc(title)}</div>
  {tickers_html}
  {narr}
  {chain_html}
  {ripples_html}
  <div class="meta">
    <span>이벤트 {rec["n_events"]}</span>
    <span>출처 {rec["n_sources"]}</span>
    <span>deep {'✓' if rec["has_deep"] else '—'}</span>
    <span class="reasons">{_esc(' · '.join(rec["reasons"]))}</span>
  </div>
  {sources_html}
</div>"""


def render_html(
    records: list[dict],
    *,
    generated_at: datetime | None = None,
    window_hours: int | None = None,
    pipeline_stats: dict | None = None,
) -> str:
    generated_at = generated_at or datetime.now(UTC)
    n_story = sum(1 for r in records if r["kind"] == "STORY")
    n_signal = sum(1 for r in records if r["kind"] == "SIGNAL")

    stats = [
        _stat("Top", len(records)),
        _stat("스토리", n_story),
        _stat("시그널", n_signal),
    ]
    if window_hours:
        stats.append(_stat("수집창", f"{window_hours}h"))
    for key in ("clusters_in", "top_k", "edges", "shallow", "deep"):
        if pipeline_stats and key in pipeline_stats:
            stats.append(_stat(key, pipeline_stats[key]))

    cards = (
        "".join(_card_html(r) for r in records)
        if records
        else '<div class="empty">후보가 없습니다.</div>'
    )
    ts = generated_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>finvision · 오늘의 시장 재료 Top {len(records)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>오늘의 시장 재료 Top {len(records)}</h1>
  <div class="sub">finvision ingest2 · 생성 {_esc(ts)}</div>
</header>
<div class="stats">{''.join(stats)}</div>
{cards}
</div></body></html>"""


def write_report(
    final_items: list[RankedStory],
    result: CandidateResult,
    out_dir: str | Path = "data/ingest2",
    *,
    window_hours: int | None = None,
) -> ReportPaths:
    """final_items → top10.html + top10.json. 경로 반환."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = to_records(final_items, result)

    html_path = out / "top10.html"
    json_path = out / "top10.json"
    html_path.write_text(
        render_html(
            records,
            window_hours=window_hours,
            pipeline_stats=result.stats,
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "window_hours": window_hours,
                "pipeline_stats": result.stats,
                "items": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ReportPaths(html=html_path, json=json_path)
