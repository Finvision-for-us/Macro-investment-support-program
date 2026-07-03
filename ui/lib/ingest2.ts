/**
 * ingest2 top10.json 스키마 타입 + 헬퍼.
 * 파일 IO는 ingest2-server.ts 에 분리.
 */

export type Direction = "positive" | "negative" | "uncertain";
export type Kind = "STORY" | "SIGNAL";

export interface CausalEdge {
  from: string;
  to: string;
  mechanism: string;
  confidence: number;
  direction: Direction;
  inferred_by: string;
}

export interface RankedEvent {
  title: string;
  publishers: string[];
  occurred_at: string;
  url: string;
}

export interface RippleEffect {
  tier: string;
  target: string;
  direction: Direction;
  horizon: string;
  confidence: number;
  mechanism: string;
}

export interface RankedItem {
  rank: number;
  kind: Kind;
  final_score: number;
  impact: number;
  direction: Direction;
  confidence: number;
  n_events: number;
  n_sources: number;
  has_deep: boolean;
  tickers: string[];
  title: string;
  narrative_short: string;
  narrative_long: string;
  reasons: string[];
  chain: CausalEdge[];
  events: RankedEvent[];
  ripples: RippleEffect[];
  sources: string[];
}

export interface PipelineStats {
  clusters_in: number;
  top_k: number;
  edges_pairwise: number;
  shallow: number;
  deep: number;
  edges: number;
  components: number;
  signals: number;
  stories: number;
}

export interface Ingest2Report {
  generated_at: string;
  window_hours: number;
  pipeline_stats: PipelineStats;
  items: RankedItem[];
}

export const DIR_LABEL: Record<Direction, string> = {
  positive: "상승",
  negative: "하락",
  uncertain: "불확실",
};
