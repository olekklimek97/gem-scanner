// Shapes returned by the Flask backend's JSON endpoints. Keep in sync with
// dashboard_local.py — these are the documented contract surface.

export type PositionStatus = 'open' | 'partial' | 'closed' | 'dust' | 'stopped';

export interface Position {
  token_address: string;
  token_name: string;
  token_symbol: string;
  buy_amount_sol: number;
  buy_time: string; // ISO 8601
  status: PositionStatus;
  cascade_level: number;
  score: number;
  tokens_remaining: number;
  total_sold_sol: number;
  sells_count: number;
  /** Combined realized + unrealized (live) when pnl_is_live, else realized only. */
  pnl_pct: number;
  pnl_is_live: boolean;
  time_held_hours: number;
  url: string;
}

/**
 * Trade-log events are heterogeneous — every event has `action` and `timestamp`,
 * but the rest of the fields vary by action type. We type the common ones as
 * optional and allow extras.
 */
export interface Trade {
  action: string;
  timestamp: string; // ISO 8601
  token?: string;
  token_address?: string;
  amount_sol?: number;
  score?: number;
  signal?: string;
  level?: number;
  pnl_pct?: number;
  price_change_pct?: number;
  sell_pct?: number;
  reasons?: string[];
  existing_status?: string;
  existing_cascade_level?: number;
  new_alert_score?: number;
  // catch-all for fields we don't model explicitly
  [extra: string]: unknown;
}

export interface SystemStatus {
  sniper_alerts_count: number;
  last_alert_time: string; // ISO 8601 or ""
  positions_open_count: number;
  positions_partial_count: number;
  positions_dust_count: number;
  total_pnl_sol: number;
  latest_scan_time: string; // ISO 8601 or ""
}

export interface MetricsSummary {
  total_positions: number;
  active_positions: number;
  closed_positions: number;
  dust_positions: number;
  win_rate_pct: number;
  avg_hold_time_hours: number;
}

/**
 * Scan-history entries are served by the EXISTING /api/history endpoint
 * (kept untouched by this dashboard rewrite). We type it here so the chart
 * component has a contract.
 */
export interface ScanHistoryEntry {
  id: number;
  timestamp: string;
  total_tokens_found: number;
  top_score: number;
}
