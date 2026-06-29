"""finvision CLI 진입점.

사용법:
    python -m src.cli ingest NVDA --days 7
    python -m src.cli ingest top30 --universe --days 7   # 다종목
    python -m src.cli batch top30 [--days 7]             # one-shot 9단계 (~lifecycle)
    python -m src.cli costs [--days 30]                  # API 사용량
    python -m src.cli cluster NVDA [--threshold 0.82]
    python -m src.cli score NVDA [--top 10]
    python -m src.cli research NVDA [--shallow-top 10 --deep-top 3]
    python -m src.cli edges NVDA [--top 20]
    python -m src.cli stories NVDA
    python -m src.cli narratives NVDA [--top 10]
    python -m src.cli report NVDA [--top 10]
    python -m src.cli lifecycle link top30 [--date 2026-05-27]   # M4
    python -m src.cli lifecycle list [--days 7]                  # M4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.cluster.cluster import build_events
from src.cluster.embed import MissingGeminiKeyError, load_or_compute_embeddings
from src.cost_guard import (
    PARALLEL_INITIAL_CREDIT_USD,
    parallel_remaining_pct,
    parallel_remaining_usd,
    should_skip_deep,
    usage_summary,
    warn_message,
)
from src.config import CLUSTER_SIMILARITY_THRESHOLD, OUTPUTS_DIR, ROOT
from src.ingest.polygon_news import MissingAPIKeyError, fetch_news
from src.ingest.schema import Event, RawNews
from src.lifecycle import link as life_link, state as life_state, store as life_store
from src.macro import fred as macro_fred, themes as macro_themes
from src.causal.edges import (
    TOP_N as EDGES_TOP_N,
    candidate_pairs,
    event_embeddings,
    infer_from_claims,
    infer_pairwise,
    merge_edges,
)
from src.causal.graph import (
    build_graph,
    build_story_skeletons,
    extract_components,
    filter_size,
)
from src.causal.schema import CausalEdge, Story
from src.causal.ripple import generate_ripples
from src.causal.story import generate_narrative
from src.cluster.embed import embed_texts
from src.report.markdown import render_report
from src.research.deep import deep_research
from src.research.shallow import shallow_research
from src.score.impact import fetch_market_caps, load_events, score_events, serialize_scored
from src.score.novelty import compute_novelty, load_historical_events
from src.score.price_reaction import compute_price_reactions
from src.universe.seeds import get_universe

console = Console()


POLYGON_FREE_TIER_INTER_CALL_SEC = 13.0  # 5 calls/min 안전 마진


def cmd_ingest(label: str, days: int, is_universe: bool = False) -> None:
    """Day 1~2 (단일) + M3 Day 8~9 (다종목): 뉴스 수집.

    is_universe=True면 ``label``을 universe 이름으로 해석.
    """
    if is_universe:
        _cmd_ingest_universe(label, days)
    else:
        _cmd_ingest_single(label, days)


def _cmd_ingest_single(ticker: str, days: int) -> None:
    console.print(f"[bold cyan]Fetching {ticker} news (last {days} days)...[/]")
    try:
        news = fetch_news(ticker, days=days)
    except MissingAPIKeyError as e:
        console.print(f"[red]Config error:[/] {e}")
        return

    console.print(f"[green]Got {len(news)} articles.[/]")
    if not news:
        console.print("[yellow]No news returned. Check ticker or API key.[/]")
        return

    _save_and_summarize(ticker, news)


def _cmd_ingest_universe(name: str, days: int) -> None:
    try:
        tickers = get_universe(name)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return

    console.print(
        f"[bold cyan]Multi-ingest universe '{name}' "
        f"({len(tickers)} tickers, last {days} days)...[/]"
    )
    console.print(
        f"[dim]Polygon free tier: {POLYGON_FREE_TIER_INTER_CALL_SEC}s 대기 사이마다 "
        f"~{int(POLYGON_FREE_TIER_INTER_CALL_SEC * len(tickers) / 60)}분 소요[/]"
    )

    all_news: dict[str, RawNews] = {}
    failures: list[str] = []

    for i, t in enumerate(tickers, 1):
        try:
            ns = fetch_news(t, days=days)
        except MissingAPIKeyError as e:
            console.print(f"[red]Config error: {e}[/]")
            return
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]({i}/{len(tickers)}) {t} failed: {str(e)[:80]}[/]")
            failures.append(t)
        else:
            added = 0
            for n in ns:
                if n.id not in all_news:
                    all_news[n.id] = n
                    added += 1
            console.print(
                f"[dim]({i:>2}/{len(tickers)}) {t:6s}  "
                f"{len(ns):>3} fetched, +{added:>3} new  "
                f"total {len(all_news)}[/]"
            )

        if i < len(tickers):
            time.sleep(POLYGON_FREE_TIER_INTER_CALL_SEC)

    if failures:
        console.print(f"[yellow]Failed tickers: {failures}[/]")

    news = list(all_news.values())
    if not news:
        console.print("[red]No news collected.[/]")
        return

    _save_and_summarize(name, news, label_type="universe")


def _save_and_summarize(
    label: str, news: list[RawNews], label_type: str = "ticker"
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{label}_raw_{timestamp}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(
            [n.model_dump(mode="json") for n in news],
            f, ensure_ascii=False, indent=2, default=str,
        )
    console.print(f"[green]Saved -> {out_path} ({len(news)} unique articles)[/]")

    pub_counts = Counter(n.publisher or "(unknown)" for n in news)
    pt = Table(title=f"{label} News by Publisher (Top 10)")
    pt.add_column("Publisher")
    pt.add_column("Count", justify="right")
    for pub, cnt in pub_counts.most_common(10):
        pt.add_row(pub, str(cnt))
    console.print(pt)

    if label_type == "universe":
        # 종목별 기사 수도 보여줌
        tk_counts: Counter = Counter()
        for n in news:
            for t in n.tickers:
                tk_counts[t] += 1
        tk = Table(title="Articles by Ticker (Top 15)")
        tk.add_column("Ticker")
        tk.add_column("Articles", justify="right")
        for t, c in tk_counts.most_common(15):
            tk.add_row(t, str(c))
        console.print(tk)

    earliest = min(n.published_at for n in news)
    latest = max(n.published_at for n in news)
    console.print(f"[dim]Range: {earliest.isoformat()} -> {latest.isoformat()}[/]")


def _latest_file(ticker: str, kind: str) -> Path | None:
    files = sorted(OUTPUTS_DIR.glob(f"{ticker}_{kind}_*.json"))
    return files[-1] if files else None


def _latest_raw_file(ticker: str) -> Path | None:
    return _latest_file(ticker, "raw")


def _load_raw_news(path: Path) -> list[RawNews]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return [RawNews(**d) for d in data]


def cmd_cluster(ticker: str, threshold: float, input_path: Path | None) -> None:
    """Day 3~4: 임베딩 + 코사인 군집화로 Event 리스트 생성."""
    path = input_path or _latest_raw_file(ticker)
    if path is None or not path.exists():
        console.print(f"[red]No raw file found for {ticker}. Run `ingest` first.[/]")
        return

    console.print(f"[bold cyan]Loading {path.name}...[/]")
    news = _load_raw_news(path)
    console.print(f"[green]Loaded {len(news)} articles.[/]")

    console.print("[bold cyan]Loading embeddings (cache or compute)...[/]")
    try:
        embeddings, cache_hit = load_or_compute_embeddings(path, news)
    except MissingGeminiKeyError as e:
        console.print(f"[red]Config error:[/] {e}")
        return
    src = "cache hit" if cache_hit else "freshly embedded + cached"
    console.print(f"[green]Got embeddings shape {embeddings.shape} ({src}).[/]")

    console.print(f"[bold cyan]Clustering (threshold={threshold})...[/]")
    events = build_events(news, embeddings, threshold=threshold)
    console.print(
        f"[green]{len(news)} articles -> {len(events)} events "
        f"(largest cluster: {events[0].spread if events else 0})[/]"
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_events_{timestamp}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(
            [e.model_dump(mode="json") for e in events],
            f, ensure_ascii=False, indent=2, default=str,
        )
    console.print(f"[green]Saved -> {out_path}[/]")

    spread_buckets = Counter()
    for ev in events:
        if ev.spread == 1:
            spread_buckets["singleton (1)"] += 1
        elif ev.spread <= 3:
            spread_buckets["small (2-3)"] += 1
        elif ev.spread <= 7:
            spread_buckets["medium (4-7)"] += 1
        else:
            spread_buckets["large (8+)"] += 1
    dist = Table(title="Cluster Size Distribution")
    dist.add_column("Bucket")
    dist.add_column("# Clusters", justify="right")
    for k in ["large (8+)", "medium (4-7)", "small (2-3)", "singleton (1)"]:
        dist.add_row(k, str(spread_buckets.get(k, 0)))
    console.print(dist)

    top = Table(title=f"Top 10 Events by Spread")
    top.add_column("#", justify="right")
    top.add_column("Spread", justify="right")
    top.add_column("Title", overflow="fold")
    top.add_column("Tickers", overflow="fold")
    for i, ev in enumerate(events[:10], 1):
        top.add_row(
            str(i),
            str(ev.spread),
            ev.title[:90],
            ", ".join(ev.tickers_mentioned[:6]),
        )
    console.print(top)


def cmd_score(ticker: str, top: int, input_path: Path | None) -> None:
    """Day 5~7 + M3 Day 1~2: 4신호 영향력 점수 (Spread/MCap/Novelty/+곧 PriceReaction)."""
    path = input_path or _latest_file(ticker, "events")
    if path is None or not path.exists():
        console.print(f"[red]No events file for {ticker}. Run `cluster` first.[/]")
        return

    console.print(f"[bold cyan]Loading {path.name}...[/]")
    events = load_events(path)
    console.print(f"[green]Loaded {len(events)} events.[/]")

    # Novelty: 최근 30일 historical과 비교
    console.print("[bold cyan]Computing Novelty (historical 30d scan)...[/]")
    historical = load_historical_events(ticker, exclude_path=path)
    console.print(f"[dim]  historical pool: {len(historical)} events[/]")
    novelty_scores: dict[str, float] = {}
    if events:
        current_texts = [f"{e.title}\n\n{e.summary[:500]}" for e in events]
        current_embs = embed_texts(current_texts)
        if historical:
            hist_texts = [f"{e.title}\n\n{e.summary[:500]}" for e in historical]
            hist_embs = embed_texts(hist_texts)
            novelty_scores = compute_novelty(events, current_embs, historical, hist_embs)
        else:
            novelty_scores = {e.id: 1.0 for e in events}
        avg_nov = sum(novelty_scores.values()) / len(novelty_scores)
        console.print(f"[dim]  avg novelty: {avg_nov:.3f}[/]")

    # PriceReaction 내부 가중치용으로 시총 fetch (mcap 신호는 제거됨)
    all_tickers = sorted({t for e in events for t in e.tickers_mentioned})
    console.print(
        f"[bold cyan]Fetching market caps for {len(all_tickers)} tickers (PR 가중치용, 캐시 7d)...[/]"
    )
    caps = fetch_market_caps(all_tickers)

    # PriceReaction: yfinance historical prices (cached)
    console.print("[bold cyan]Computing PriceReaction (yfinance history)...[/]")
    pr_scores = compute_price_reactions(events, ticker_caps=caps)
    if pr_scores:
        avg_pr = sum(pr_scores.values()) / len(pr_scores)
        console.print(
            f"[dim]  {len(pr_scores)}/{len(events)} events have price data, "
            f"avg PR={avg_pr:.3f}[/]"
        )
    else:
        console.print("[yellow]  no price data available[/]")

    scored = score_events(
        events,
        novelty_scores=novelty_scores,
        price_reaction_scores=pr_scores,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_scored_{timestamp}.json"
    out_path.write_text(
        json.dumps(serialize_scored(scored), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    console.print(f"[green]Saved -> {out_path}[/]")

    table = Table(title=f"Top {top} Events by Impact Score (3-signal, mcap 제거)")
    table.add_column("#", justify="right")
    table.add_column("Impact", justify="right")
    table.add_column("Spread", justify="right")
    table.add_column("Nov", justify="right")
    table.add_column("PR", justify="right")
    table.add_column("EffSp", justify="right")  # effective spread
    table.add_column("DF", justify="right")      # discount factor
    table.add_column("Title", overflow="fold")
    for i, s in enumerate(scored[:top], 1):
        table.add_row(
            str(i),
            f"{s.impact_score:.3f}",
            f"{s.spread_score:.2f}",
            f"{s.novelty_score:.2f}",
            f"{s.price_reaction_score:.2f}",
            f"{s.effective_spread:.1f}",
            f"{s.spread_discount_factor:.2f}",
            s.event.title[:70],
        )
    console.print(table)


def cmd_research(ticker: str, shallow_top: int, deep_top: int, input_path: Path | None) -> None:
    """Day 8~11: 상위 클러스터에 얕은+깊은 리서치 수행."""
    path = input_path or _latest_file(ticker, "scored")
    if path is None or not path.exists():
        console.print(f"[red]No scored file for {ticker}. Run `score` first.[/]")
        return

    console.print(f"[bold cyan]Loading {path.name}...[/]")
    scored_data = json.loads(path.read_text(encoding="utf-8"))
    events = [Event(**item["event"]) for item in scored_data[:shallow_top]]
    console.print(f"[green]Researching top {len(events)} events "
                  f"(shallow all, deep top {deep_top})...[/]")

    shallow_reports: dict[str, dict] = {}
    deep_reports: dict[str, dict] = {}
    evidence_log: dict[str, list[dict]] = {}

    for i, ev in enumerate(events, 1):
        if i > 1:
            time.sleep(7.0)
        console.print(f"[dim]({i}/{len(events)}) shallow:[/] {ev.title[:70]}")
        try:
            shallow = shallow_research(ev)
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]shallow failed: {type(e).__name__}: {str(e)[:120]}[/]")
            continue
        shallow_reports[ev.id] = shallow.model_dump(mode="json")
        console.print(
            f"  [dim]-> {shallow.direction} "
            f"(conf={shallow.confidence:.2f}, sources={len(shallow.sources)})[/]"
        )

        if i <= deep_top:
            console.print(f"  [bold cyan]deep:[/] planning + searching...")
            try:
                deep, evidence = deep_research(ev, shallow)
                deep_reports[ev.id] = deep.model_dump(mode="json")
                evidence_log[ev.id] = [e.model_dump(mode="json") for e in evidence]
                n_claims = sum(
                    len(getattr(deep, k))
                    for k in (
                        "background", "direct_causes", "affected_entities",
                        "counter_evidence", "watch_points",
                    )
                )
                console.print(
                    f"  [green]-> {deep.direction} (conf={deep.confidence:.2f}, "
                    f"claims={n_claims}, sources={len(deep.all_sources)})[/]"
                )
            except Exception as e:  # noqa: BLE001
                console.print(f"  [red]deep failed: {type(e).__name__}: {str(e)[:600]}[/]")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_research_{timestamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "source_scored": path.name,
                "shallow": shallow_reports,
                "deep": deep_reports,
                "evidence": evidence_log,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]Saved -> {out_path}[/]")
    console.print(
        f"[bold]Summary:[/] shallow={len(shallow_reports)}, deep={len(deep_reports)}"
    )


def cmd_edges(ticker: str, top_n: int, scored_input: Path | None, research_input: Path | None) -> None:
    """M2 Day 1~3: 인과 edge 추론 (pairwise LLM + claim 매칭)."""
    scored_path = scored_input or _latest_file(ticker, "scored")
    research_path = research_input or _latest_file(ticker, "research")

    if scored_path is None or not scored_path.exists():
        console.print(f"[red]No scored file for {ticker}. Run `score` first.[/]")
        return

    console.print(f"[bold cyan]Loading scored: {scored_path.name}[/]")
    scored_data = json.loads(scored_path.read_text(encoding="utf-8"))
    events: list[Event] = [Event(**item["event"]) for item in scored_data[:top_n]]
    console.print(f"[green]Top {len(events)} events selected.[/]")

    console.print("[bold cyan]Embedding events for filtering...[/]")
    embs = event_embeddings(events)
    console.print(f"[green]Embeddings shape {embs.shape}.[/]")

    pairs = candidate_pairs(events, embs)
    n_pairs_total = len(events) * (len(events) - 1) // 2
    console.print(
        f"[bold cyan]Filter: {len(pairs)}/{n_pairs_total} pairs passed "
        f"(ticker/time/sim)[/]"
    )

    def _progress(idx, total, a, b, error=None):
        msg = f"  [{idx:>3}/{total}] {a.title[:50]} <-> {b.title[:50]}"
        if error:
            console.print(f"[red]{msg} ERR {error}[/]")
        else:
            console.print(f"[dim]{msg}[/]")

    pairwise_edges = infer_pairwise(events, embs, on_progress=_progress)
    console.print(
        f"[green]Pairwise LLM → {len(pairwise_edges)} edges "
        f"(confidence ≥ threshold)[/]"
    )

    deep_reports: dict[str, dict] = {}
    if research_path and research_path.exists():
        rdata = json.loads(research_path.read_text(encoding="utf-8"))
        deep_reports = rdata.get("deep", {}) or {}
        console.print(
            f"[bold cyan]Claim-based: scanning {len(deep_reports)} deep reports...[/]"
        )

        def _claim_progress(idx, total, cause_ev, effect_ev, error=None):
            msg = (
                f"  [{idx:>2}/{total}] verify: "
                f"{cause_ev.title[:40]} → {effect_ev.title[:40]}"
            )
            if error:
                console.print(f"[red]{msg} ERR {error}[/]")
            else:
                console.print(f"[dim]{msg}[/]")

        claim_edges = infer_from_claims(
            events, embs, deep_reports, on_progress=_claim_progress
        )
        console.print(f"[green]Claim-based (LLM verified) → {len(claim_edges)} edges[/]")
    else:
        claim_edges = []
        console.print("[yellow]No research file; skipping claim-based inference.[/]")

    edges = merge_edges(pairwise_edges + claim_edges)
    console.print(f"[green]Merged total: {len(edges)} unique edges[/]")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_edges_{timestamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "source_scored": scored_path.name,
                "source_research": research_path.name if research_path else None,
                "n_events": len(events),
                "n_candidate_pairs": len(pairs),
                "edges": [e.model_dump(mode="json") for e in edges],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]Saved -> {out_path}[/]")

    if edges:
        table = Table(title=f"Causal Edges (Top {min(15, len(edges))})")
        table.add_column("Conf", justify="right")
        table.add_column("Dir")
        table.add_column("By")
        table.add_column("From → To", overflow="fold")
        table.add_column("Mechanism", overflow="fold")
        id_to_title = {ev.id: ev.title for ev in events}
        for e in sorted(edges, key=lambda x: -x.confidence)[:15]:
            from_t = id_to_title.get(e.from_event_id, e.from_event_id)[:40]
            to_t = id_to_title.get(e.to_event_id, e.to_event_id)[:40]
            table.add_row(
                f"{e.confidence:.2f}",
                e.direction[:3],
                e.inferred_by[:8],
                f"{from_t} → {to_t}",
                e.mechanism[:80],
            )
        console.print(table)


def cmd_stories(ticker: str, scored_input: Path | None, edges_input: Path | None) -> None:
    """M2 Day 4~5: edges → graph → 연결 컴포넌트 → Story 스켈레톤."""
    scored_path = scored_input or _latest_file(ticker, "scored")
    edges_path = edges_input or _latest_file(ticker, "edges")

    if scored_path is None or not scored_path.exists():
        console.print(f"[red]No scored file for {ticker}. Run `score` first.[/]")
        return
    if edges_path is None or not edges_path.exists():
        console.print(f"[red]No edges file for {ticker}. Run `edges` first.[/]")
        return

    console.print(f"[bold cyan]Loading scored: {scored_path.name}[/]")
    console.print(f"[bold cyan]Loading edges: {edges_path.name}[/]")

    scored_data = json.loads(scored_path.read_text(encoding="utf-8"))
    edges_data = json.loads(edges_path.read_text(encoding="utf-8"))
    n_events_in_edges = edges_data.get("n_events", len(scored_data))

    events: list[Event] = [Event(**item["event"]) for item in scored_data[:n_events_in_edges]]
    edges = [CausalEdge(**e) for e in edges_data.get("edges", [])]

    console.print(f"[green]Loaded {len(events)} events, {len(edges)} edges.[/]")

    g = build_graph(events, edges)
    components = extract_components(g)
    filtered = filter_size(components)
    console.print(
        f"[green]{len(components)} weak components → "
        f"{len(filtered)} after size filter (1~20)[/]"
    )

    events_by_id = {ev.id: ev for ev in events}
    scored_by_id = {item["event"]["id"]: item for item in scored_data}
    stories = build_story_skeletons(filtered, events_by_id, scored_by_id)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_stories_{timestamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "source_scored": scored_path.name,
                "source_edges": edges_path.name,
                "stories": [s.model_dump(mode="json") for s in stories],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]Saved -> {out_path}[/]")

    # 분포 표
    size_buckets = Counter()
    for s in stories:
        n = len(s.event_ids)
        if n == 1:
            size_buckets["singleton (1)"] += 1
        elif n <= 3:
            size_buckets["small (2-3)"] += 1
        elif n <= 7:
            size_buckets["medium (4-7)"] += 1
        else:
            size_buckets["large (8+)"] += 1
    dist = Table(title="Story Size Distribution")
    dist.add_column("Bucket")
    dist.add_column("# Stories", justify="right")
    for k in ["large (8+)", "medium (4-7)", "small (2-3)", "singleton (1)"]:
        dist.add_row(k, str(size_buckets.get(k, 0)))
    console.print(dist)

    # Top 10 표
    top = Table(title=f"Top {min(10, len(stories))} Stories (영향력 합산)")
    top.add_column("#", justify="right")
    top.add_column("Size", justify="right")
    top.add_column("Edges", justify="right")
    top.add_column("Impact", justify="right")
    top.add_column("Dir")
    top.add_column("Tickers", overflow="fold")
    top.add_column("Lead event", overflow="fold")
    for i, s in enumerate(stories[:10], 1):
        # 대표 이벤트 = 영향력 가장 높은 멤버
        lead_ev = None
        for eid in s.event_ids:
            if eid in events_by_id and (
                lead_ev is None
                or scored_by_id.get(eid, {}).get("impact_score", 0)
                > scored_by_id.get(lead_ev.id, {}).get("impact_score", 0)
            ):
                lead_ev = events_by_id[eid]
        top.add_row(
            str(i),
            str(len(s.event_ids)),
            str(len(s.edges)),
            f"{s.aggregated_impact:.2f}",
            s.direction[:3],
            ", ".join(s.affected_tickers[:5]),
            (lead_ev.title[:60] if lead_ev else "(?)"),
        )
    console.print(top)


def cmd_narratives(
    ticker: str,
    top_n: int,
    stories_input: Path | None,
    research_input: Path | None,
    scored_input: Path | None,
) -> None:
    """M2 Day 6~8: 각 Story에 title + narrative_short + narrative_long 생성."""
    stories_path = stories_input or _latest_file(ticker, "stories")
    research_path = research_input or _latest_file(ticker, "research")
    scored_path = scored_input or _latest_file(ticker, "scored")

    if stories_path is None or not stories_path.exists():
        console.print(f"[red]No stories file for {ticker}. Run `stories` first.[/]")
        return
    if scored_path is None or not scored_path.exists():
        console.print(f"[red]No scored file. Run `score` first.[/]")
        return

    console.print(f"[bold cyan]Loading stories: {stories_path.name}[/]")
    console.print(f"[bold cyan]Loading scored: {scored_path.name}[/]")
    stories_data = json.loads(stories_path.read_text(encoding="utf-8"))
    scored_data = json.loads(scored_path.read_text(encoding="utf-8"))

    deep_reports: dict[str, dict] = {}
    if research_path and research_path.exists():
        console.print(f"[bold cyan]Loading research: {research_path.name}[/]")
        rdata = json.loads(research_path.read_text(encoding="utf-8"))
        deep_reports = rdata.get("deep", {}) or {}

    events_by_id: dict[str, Event] = {
        item["event"]["id"]: Event(**item["event"]) for item in scored_data
    }

    stories: list[Story] = [Story(**s) for s in stories_data.get("stories", [])]
    target = stories[:top_n]
    console.print(f"[green]Generating narratives for top {len(target)} stories...[/]")

    narrated: list[Story] = []
    for i, story in enumerate(target, 1):
        console.print(
            f"[dim]({i}/{len(target)}) size={len(story.event_ids)}, "
            f"edges={len(story.edges)}, impact={story.aggregated_impact:.2f}[/]"
        )
        new_story = generate_narrative(story, events_by_id, deep_reports)
        title_preview = (new_story.title or "(no title)")[:80]
        console.print(f"  [green]-> {title_preview}[/]")
        # M3.5: narrative 직후 파급효과 생성 — 실패 시 빈 list
        try:
            ripples = generate_ripples(new_story)
            if ripples:
                console.print(f"  [cyan]   파급효과 {len(ripples)}건 추가[/]")
            new_story = new_story.model_copy(update={"ripple_effects": ripples})
        except Exception as e:  # noqa: BLE001
            console.print(f"  [yellow]   파급효과 생성 skip ({e})[/]")
        narrated.append(new_story)
        if i < len(target):
            time.sleep(2.0)

    # 나머지 (narrate 안 한 것들)도 같이 저장 (skeleton 그대로)
    remainder = stories[top_n:]
    all_stories = narrated + remainder

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_narratives_{timestamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "source_stories": stories_path.name,
                "source_research": research_path.name if research_path else None,
                "narrated_count": len(narrated),
                "stories": [s.model_dump(mode="json") for s in all_stories],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]Saved -> {out_path}[/]")


def cmd_report(
    ticker: str,
    top_n: int,
    narratives_input: Path | None,
    scored_input: Path | None,
) -> None:
    """Day 9~10 (M2): narratives + scored → Story 단위 Markdown 리포트."""
    narratives_path = narratives_input or _latest_file(ticker, "narratives")
    scored_path = scored_input or _latest_file(ticker, "scored")

    if narratives_path is None or not narratives_path.exists():
        console.print(
            f"[red]No narratives file for {ticker}. Run `narratives` first.[/]"
        )
        return
    if scored_path is None or not scored_path.exists():
        console.print(f"[red]No scored file. Run `score` first.[/]")
        return

    console.print(f"[bold cyan]Loading narratives: {narratives_path.name}[/]")
    console.print(f"[bold cyan]Loading scored: {scored_path.name}[/]")

    narratives_data = json.loads(narratives_path.read_text(encoding="utf-8"))
    scored_data = json.loads(scored_path.read_text(encoding="utf-8"))

    stories: list[dict] = narratives_data.get("stories", []) or []
    events_by_id: dict[str, dict] = {
        item["event"]["id"]: item["event"] for item in scored_data
    }

    md = render_report(ticker, stories, events_by_id, top_n=top_n)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{ticker}_report_{timestamp}.md"
    out_path.write_text(md, encoding="utf-8")
    console.print(f"[green]Saved -> {out_path}[/]")
    console.print(f"[dim]({len(md)} chars, ~{md.count(chr(10))} lines)[/]")


def cmd_costs(days: int) -> None:
    """API 사용량 요약 + Parallel 잔여 크레딧 추정."""
    rows = usage_summary(days=days)
    if not rows:
        console.print(f"[yellow]최근 {days}일 기록된 API 호출 없음.[/]")
    else:
        table = Table(title=f"API Usage Summary ({days}d)")
        table.add_column("Provider")
        table.add_column("Endpoint")
        table.add_column("Calls", justify="right")
        table.add_column("Cost (USD)", justify="right")
        table.add_column("Last")
        total = 0.0
        for r in rows:
            table.add_row(
                r["provider"],
                r["endpoint"],
                str(r["calls"]),
                f"${r['cost_usd']:.4f}",
                (r["last_at"] or "")[:19],
            )
            total += r["cost_usd"]
        console.print(table)
        console.print(f"[bold]총 추정 비용: ${total:.4f}[/]")

    rem = parallel_remaining_usd()
    pct = parallel_remaining_pct()
    color = "green" if pct >= 0.20 else ("yellow" if pct >= 0.05 else "red")
    console.print(
        f"[{color}]Parallel 잔여 크레딧: ${rem:.2f} / ${PARALLEL_INITIAL_CREDIT_USD:.2f}  "
        f"({pct * 100:.1f}%)[/]"
    )
    warn = warn_message()
    if warn:
        console.print(f"[yellow]⚠ {warn}[/]")


UI_STORIES_LATEST = ROOT / "data" / "stories_latest.json"


def _write_stories_latest(
    stories: list[life_store.LifecycleStory],
    date_str: str,
    *,
    macro_events: list | None = None,
    themes: list | None = None,
) -> Path:
    """미니 UI(`/today`)가 읽는 단일 JSON 갱신.

    매 lifecycle link 후 호출됨. 스냅샷 디렉터리는 일자별 보존용이고,
    이 파일은 항상 최신만 가리킨다 (UI 가 디렉터리 스캔할 필요 없도록).
    M3.5: macro_events / themes 도 같은 파일에 포함.
    """
    UI_STORIES_LATEST.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "date": date_str,
        "stories": [s.model_dump(mode="json") for s in stories],
        "macro_events": [m.model_dump(mode="json") for m in (macro_events or [])],
        "themes": [t.model_dump(mode="json") for t in (themes or [])],
    }
    UI_STORIES_LATEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return UI_STORIES_LATEST


def cmd_lifecycle_link(
    label: str,
    date_str: str | None = None,
    *,
    skip_macro: bool = False,
    skip_themes: bool = False,
) -> None:
    """M4 Day 7 + M3.5 Day 4: 최신 narratives → LifecycleStory → 어제 매칭 → 상태 라벨 → 스냅샷.

    M3.5 추가: FRED 거시 이벤트 fetch + Story 단위 테마 클러스터링까지 같은 스냅샷에 묶음.
    동시에 ``data/stories_latest.json`` 도 갱신. 결정론적 + 임베딩 1배치라
    추가 LLM 비용 거의 없음 (테마 명명만 클러스터당 1회).
    """
    date_str = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    console.print(f"[bold magenta]== LIFECYCLE LINK: '{label}' {date_str} ==[/]")

    narr_path = _latest_file(label, "narratives")
    if narr_path is None:
        console.print(
            f"[red]No narratives file for {label}. Run `narratives` (or `batch`) first.[/]"
        )
        return
    console.print(f"[cyan]Loading narratives: {narr_path.name}[/]")
    data = json.loads(narr_path.read_text(encoding="utf-8"))
    raw_stories = [Story(**s) for s in data.get("stories", [])]
    if not raw_stories:
        console.print("[yellow]narratives 비어있음. skip.[/]")
        return

    today_raw = [life_store.from_story(s, on_date=date_str) for s in raw_stories]

    prev = life_store.load_previous_snapshot(date_str)
    if prev is not None:
        console.print(
            f"[cyan]어제 스냅샷 {prev.date} 로드 ({len(prev.stories)} stories)[/]"
        )
    else:
        console.print("[dim]어제 스냅샷 없음 — 모두 신규 active 처리.[/]")

    today_linked = life_link.link_to_previous(today_raw, prev)
    n_evolving = sum(1 for s in today_linked if s.parent_story_id is not None)
    console.print(
        f"[green]Linked: {n_evolving}/{len(today_linked)} 가 어제 parent 발견.[/]"
    )

    final_stories = life_state.label_today(today_linked, prev, today_date=date_str)

    # M3.5: 거시 이벤트 (FRED) — 실패해도 전체 lifecycle 진행
    macro_events = []
    if not skip_macro:
        try:
            macro_events = macro_fred.fetch_macro_events(emit_days=14, sigma_threshold=1.0)
            console.print(
                f"[green]Macro events: {len(macro_events)} (1σ+, 최근 14일)[/]"
            )
        except macro_fred.MissingFredKeyError:
            console.print("[yellow]FRED_API_KEY 없음 — macro skip[/]")
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]Macro fetch 실패 — skip ({e})[/]")

    # M3.5: 테마 클러스터링 (narrative 가 있는 스토리만)
    themes = []
    if not skip_themes:
        try:
            narr_stories = [s for s in raw_stories if s.title]
            themes = macro_themes.build_themes(narr_stories)
            console.print(f"[green]Themes: {len(themes)}개 추출[/]")
            for t in themes[:5]:
                console.print(
                    f"  [dim]· {t.name} ({len(t.story_ids)}건, score {t.aggregate_score:.2f})[/]"
                )
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]Themes 생성 실패 — skip ({e})[/]")

    snap_path = life_store.save_snapshot(
        final_stories,
        date_str=date_str,
        source_narratives=narr_path.name,
        macro_events=macro_events,
        themes=themes,
    )
    latest_path = _write_stories_latest(
        final_stories, date_str, macro_events=macro_events, themes=themes
    )

    counts = Counter(s.state for s in final_stories)
    console.print(f"[bold green]Saved snapshot: {snap_path}[/]")
    console.print(f"[bold green]Updated UI source: {latest_path}[/]")
    console.print(
        f"[bold]총 {len(final_stories)}개 — "
        f"🟢 active {counts.get('active', 0)} / "
        f"🟡 evolving {counts.get('evolving', 0)} / "
        f"⚫ resolved {counts.get('resolved', 0)}[/]"
    )


def cmd_lifecycle_list(days: int) -> None:
    """최근 N일 스냅샷 추이를 표로 출력."""
    dates = life_store.list_snapshot_dates(days=days)
    if not dates:
        console.print(f"[yellow]최근 {days}일 스냅샷 없음.[/]")
        return
    table = Table(title=f"Lifecycle ({days}d)")
    table.add_column("Date")
    table.add_column("Total", justify="right")
    table.add_column("🟢 Active", justify="right")
    table.add_column("🟡 Evolving", justify="right")
    table.add_column("⚫ Resolved", justify="right")
    for d in dates:
        snap = life_store.load_snapshot(d)
        if snap is None:
            continue
        c = Counter(s.state for s in snap.stories)
        table.add_row(
            d,
            str(len(snap.stories)),
            str(c.get("active", 0)),
            str(c.get("evolving", 0)),
            str(c.get("resolved", 0)),
        )
    console.print(table)


def cmd_batch(universe: str, days: int, shallow_top: int, deep_top: int) -> None:
    """M3 Day 12~13 + M4 Day 7: 9단계 (ingest~lifecycle) 한 줄 실행. Parallel 한도 가드 포함."""
    label = universe
    console.print(f"[bold magenta]== BATCH: '{label}' ({days}d) ==[/]\n")

    # 잔여 크레딧 사전 체크
    warn = warn_message()
    if warn:
        console.print(f"[yellow]⚠ {warn}[/]\n")
    if should_skip_deep():
        deep_top = 0
        console.print("[yellow]Deep skip 모드 활성: shallow만 실행[/]\n")

    # 1. Ingest
    console.print("[bold cyan][1/9] Ingest[/]")
    cmd_ingest(label, days, is_universe=True)

    # 2. Cluster
    console.print("\n[bold cyan][2/9] Cluster[/]")
    cmd_cluster(label, CLUSTER_SIMILARITY_THRESHOLD, None)

    # 3. Score
    console.print("\n[bold cyan][3/9] Score (4-signal)[/]")
    cmd_score(label, 10, None)

    # 4. Research
    console.print(f"\n[bold cyan][4/9] Research (shallow {shallow_top}, deep {deep_top})[/]")
    cmd_research(label, shallow_top, deep_top, None)

    # 5. Edges
    console.print("\n[bold cyan][5/9] Edges[/]")
    cmd_edges(label, EDGES_TOP_N, None, None)

    # 6. Stories
    console.print("\n[bold cyan][6/9] Stories[/]")
    cmd_stories(label, None, None)

    # 7. Narratives
    console.print("\n[bold cyan][7/9] Narratives[/]")
    cmd_narratives(label, 10, None, None, None)

    # 8. Report
    console.print("\n[bold cyan][8/9] Report[/]")
    cmd_report(label, 10, None, None)

    # 9. Lifecycle link (M4) — 어제 ↔ 오늘 매칭, 상태 라벨, UI 소스 갱신
    console.print("\n[bold cyan][9/9] Lifecycle link[/]")
    cmd_lifecycle_link(label)

    console.print(f"\n[bold green]== BATCH COMPLETE ({label}) ==[/]")
    # 사후 비용 요약
    console.print()
    cmd_costs(30)


def main() -> None:
    parser = argparse.ArgumentParser(prog="finvision")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch raw news (single ticker or universe)")
    p_ingest.add_argument("label", help="Ticker symbol (e.g. NVDA) OR universe name (e.g. top30) with --universe")
    p_ingest.add_argument("--days", type=int, default=7, help="Lookback window (days)")
    p_ingest.add_argument(
        "--universe", action="store_true",
        help="Treat `label` as universe name (multi-ticker batch)",
    )

    p_cluster = sub.add_parser("cluster", help="Embed + cluster a raw news dump into events")
    p_cluster.add_argument("ticker", help="Ticker to look up latest raw file")
    p_cluster.add_argument(
        "--threshold", type=float, default=CLUSTER_SIMILARITY_THRESHOLD,
        help=f"Cosine similarity threshold (default {CLUSTER_SIMILARITY_THRESHOLD})",
    )
    p_cluster.add_argument(
        "--input", type=Path, default=None,
        help="Explicit raw JSON path (overrides auto-detection)",
    )

    p_score = sub.add_parser("score", help="Compute impact scores and rank events")
    p_score.add_argument("ticker", help="Ticker to look up latest events file")
    p_score.add_argument("--top", type=int, default=10, help="Show top N (default 10)")
    p_score.add_argument(
        "--input", type=Path, default=None,
        help="Explicit events JSON path (overrides auto-detection)",
    )

    p_research = sub.add_parser("research", help="Shallow + deep research on top events")
    p_research.add_argument("ticker", help="Ticker to look up latest scored file")
    p_research.add_argument(
        "--shallow-top", type=int, default=10,
        help="Run shallow research on top N events (default 10)",
    )
    p_research.add_argument(
        "--deep-top", type=int, default=3,
        help="Run deep research on top K events (default 3, must be <= shallow-top)",
    )
    p_research.add_argument(
        "--input", type=Path, default=None,
        help="Explicit scored JSON path (overrides auto-detection)",
    )

    p_edges = sub.add_parser("edges", help="Infer causal edges between top events")
    p_edges.add_argument("ticker", help="Ticker to look up latest scored/research files")
    p_edges.add_argument(
        "--top", type=int, default=EDGES_TOP_N,
        help=f"Number of top events to consider (default {EDGES_TOP_N})",
    )
    p_edges.add_argument(
        "--scored-input", type=Path, default=None, help="Explicit scored JSON path"
    )
    p_edges.add_argument(
        "--research-input", type=Path, default=None, help="Explicit research JSON path"
    )

    p_stories = sub.add_parser("stories", help="Build story skeletons from causal edges")
    p_stories.add_argument("ticker", help="Ticker to look up latest scored/edges files")
    p_stories.add_argument(
        "--scored-input", type=Path, default=None, help="Explicit scored JSON path"
    )
    p_stories.add_argument(
        "--edges-input", type=Path, default=None, help="Explicit edges JSON path"
    )

    p_narr = sub.add_parser("narratives", help="Generate Story title + narrative_short/long")
    p_narr.add_argument("ticker", help="Ticker to look up latest stories/research files")
    p_narr.add_argument("--top", type=int, default=10, help="Generate for top N stories")
    p_narr.add_argument("--stories-input", type=Path, default=None)
    p_narr.add_argument("--research-input", type=Path, default=None)
    p_narr.add_argument("--scored-input", type=Path, default=None)

    p_report = sub.add_parser("report", help="Render Markdown report from narratives + scored")
    p_report.add_argument("ticker", help="Ticker to look up latest narratives/scored files")
    p_report.add_argument("--top", type=int, default=10, help="Top N stories to include")
    p_report.add_argument(
        "--narratives-input", type=Path, default=None,
        help="Explicit narratives JSON path",
    )
    p_report.add_argument(
        "--scored-input", type=Path, default=None,
        help="Explicit scored JSON path",
    )

    p_batch = sub.add_parser("batch", help="Run all 8 stages end-to-end on a universe")
    p_batch.add_argument("universe", help="Universe name (e.g. top30)")
    p_batch.add_argument("--days", type=int, default=7, help="Lookback window")
    p_batch.add_argument("--shallow-top", type=int, default=10)
    p_batch.add_argument("--deep-top", type=int, default=3)

    p_costs = sub.add_parser("costs", help="Show API usage + Parallel credit remaining")
    p_costs.add_argument("--days", type=int, default=30, help="Lookback window")

    p_life = sub.add_parser("lifecycle", help="Story lifecycle (link / list) — M4")
    life_sub = p_life.add_subparsers(dest="life_cmd", required=True)
    p_life_link = life_sub.add_parser(
        "link", help="Link today narratives ↔ yesterday snapshot + label states"
    )
    p_life_link.add_argument("label", help="Universe or ticker label (e.g. top30, NVDA)")
    p_life_link.add_argument(
        "--date", default=None, help="YYYY-MM-DD (default: today UTC)"
    )
    p_life_list = life_sub.add_parser(
        "list", help="Show recent lifecycle snapshots (active/evolving/resolved counts)"
    )
    p_life_list.add_argument("--days", type=int, default=7)

    args = parser.parse_args()

    if args.cmd == "ingest":
        label = args.label if args.universe else args.label.upper()
        cmd_ingest(label, args.days, is_universe=args.universe)
    elif args.cmd == "batch":
        cmd_batch(args.universe, args.days, args.shallow_top, args.deep_top)
    elif args.cmd == "costs":
        cmd_costs(args.days)
    elif args.cmd == "cluster":
        cmd_cluster(args.ticker.upper(), args.threshold, args.input)
    elif args.cmd == "score":
        cmd_score(args.ticker.upper(), args.top, args.input)
    elif args.cmd == "research":
        cmd_research(args.ticker.upper(), args.shallow_top, args.deep_top, args.input)
    elif args.cmd == "edges":
        cmd_edges(args.ticker.upper(), args.top, args.scored_input, args.research_input)
    elif args.cmd == "stories":
        cmd_stories(args.ticker.upper(), args.scored_input, args.edges_input)
    elif args.cmd == "narratives":
        cmd_narratives(
            args.ticker.upper(),
            args.top,
            args.stories_input,
            args.research_input,
            args.scored_input,
        )
    elif args.cmd == "report":
        cmd_report(args.ticker.upper(), args.top, args.narratives_input, args.scored_input)
    elif args.cmd == "lifecycle":
        if args.life_cmd == "link":
            # label은 universe(top30) 또는 ticker(NVDA) 둘 다. 대소문자는 그대로 둠 (파일명 매칭).
            cmd_lifecycle_link(args.label, args.date)
        elif args.life_cmd == "list":
            cmd_lifecycle_list(args.days)


if __name__ == "__main__":
    main()
