/**
 * LifecycleStory 타입 + 순수 헬퍼 (서버/클라이언트 양쪽 안전).
 *
 * 파일 IO는 ``stories-server.ts`` 에 분리되어 있다 — webpack 이 node:fs/path 를
 * 클라이언트 번들로 끌고 가지 않도록.
 */

export type LifecycleState = "active" | "evolving" | "resolved";

// M3.5
export type RippleTier = "direct" | "adjacent" | "macro";
export type RippleHorizon = "1w" | "1m" | "1q";
export type Direction = "positive" | "negative" | "uncertain";

export interface RippleEffect {
  tier: RippleTier;
  target: string;
  direction: Direction;
  horizon: RippleHorizon;
  confidence: number;
  mechanism: string;
}

export interface MacroEvent {
  id: string;
  series_id: string;
  series_label_ko: string;
  unit: string;
  observed_at: string;  // ISO datetime
  value: number;
  prev_value: number;
  change: number;
  sigma_z: number;
  summary_ko: string;
}

export interface Theme {
  id: string;
  name: string;
  description: string;
  story_ids: string[];
  aggregate_score: number;
  affected_tickers: string[];
  direction: Direction;
}

export interface LifecycleStory {
  story_id: string;
  title: string;
  narrative_short: string;
  narrative_long: string;
  tickers: string[];
  score: number;
  event_ids: string[];
  state: LifecycleState;
  parent_story_id: string | null;
  similarity: number | null;
  linked_at: string | null;
  first_seen_date: string;
  last_seen_date: string;
  ripple_effects: RippleEffect[];
}

export interface StoriesLatest {
  generated_at: string;
  date: string;
  stories: LifecycleStory[];
  macro_events: MacroEvent[];
  themes: Theme[];
}

/**
 * 표시용 Top N — narratives top N 외 스토리 (title="") 제외 + 점수 내림차순.
 *
 * ``onlyDate`` 지정 시 그 날짜에 신호가 있는 스토리만 (``last_seen_date === onlyDate``).
 * 이렇게 해야 어제 이월된 unmatched carry-over 가 Top 10 슬롯을 차지해
 * 테마/리플 데이터와 어긋나는 현상을 막을 수 있다.
 */
export function topStories(
  stories: LifecycleStory[],
  n = 10,
  onlyDate?: string
): LifecycleStory[] {
  return stories
    .filter((s) => s.title.trim().length > 0)
    .filter((s) => (onlyDate ? s.last_seen_date === onlyDate : true))
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return b.first_seen_date.localeCompare(a.first_seen_date);
    })
    .slice(0, n);
}

export function countByState(
  stories: LifecycleStory[]
): Record<LifecycleState, number> {
  const out: Record<LifecycleState, number> = {
    active: 0,
    evolving: 0,
    resolved: 0,
  };
  for (const s of stories) out[s.state] += 1;
  return out;
}
