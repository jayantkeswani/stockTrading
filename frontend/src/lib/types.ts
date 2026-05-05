export interface PriceData {
  symbol: string;
  ltp: number;
  bid: number | null;
  ask: number | null;
  volume: number | null;
  change: number | null;
  change_pct: number | null;
  timestamp: string;
}

export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Position {
  id: string;
  trade_id: string;
  symbol: string;
  strike_price: number;
  option_type: "CE" | "PE";
  expiry_date: string;
  lots: number;
  quantity: number;
  entry_price: number;
  current_price: number | null;
  stop_loss: number;
  target_price: number | null;
  unrealized_pnl: number | null;
  fyers_option_symbol: string | null;
  strategy_name: string;
  is_paper: boolean;
  is_shadow: boolean;
  position_type: string;
  opened_at: string;
}

export interface Trade {
  id: string;
  signal_id: string | null;
  strategy_name: string;
  symbol: string;
  expiry_date: string;
  strike_price: number;
  option_type: "CE" | "PE";
  side: string;
  quantity: number;
  lots: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number;
  target_price: number | null;
  status: "OPEN" | "CLOSED" | "CANCELLED";
  exit_reason: string | null;
  is_paper: boolean;
  source: "MANUAL" | "YOLO" | "SHADOW";
  pnl: number | null;
  pnl_percent: number | null;
  entry_time: string;
  exit_time: string | null;
  notes: string | null;
  created_at: string;
  // Signal simulation fields (populated via JOIN when signal_id is linked)
  signal_confidence: number | null;
  signal_ai_action: string | null;
  signal_ai_summary: string | null;
  signal_instrument_type: string | null;
  signal_type: string | null;
}

export interface Signal {
  id: string;
  strategy_name: string;
  symbol: string;
  signal_type: "BUY_CE" | "BUY_PE" | "BUY_FUT" | "SELL_FUT";
  instrument_type: "OPTION" | "FUTURE" | "EQUITY";
  strike_price: number;
  expiry_date: string;
  entry_price: number;
  index_entry_price: number | null;
  stop_loss: number;
  target_price: number | null;
  confidence: number | null;
  status: "PENDING" | "EXECUTED" | "EXPIRED" | "REJECTED";
  reason: string;
  indicators: Record<string, unknown>;
  executable: boolean;
  blocked_reason: string | null;
  generated_at: string;
  lots: number | null;
  quantity: number | null;
  fyers_option_symbol: string | null;
  fyers_futures_symbol: string | null;
  // Phase 2 — LLM overlay fields
  ai_summary: string | null;
  ai_rationale: string | null;
  ai_adjustment: number | null;
  ai_action: string | null;
}

export interface SignalPreview {
  signal_id: string;
  lots: number;
  quantity: number;
  lot_size: number;
  entry_price: number;
  stop_loss: number;
  target_price: number | null;
  capital_at_risk: number;
  sizing_meta: Record<string, unknown> | null;
}

export interface RiskDashboard {
  capital: number;
  daily_pnl: number;
  closed_pnl: number;
  daily_drawdown_pct: number;
  max_daily_drawdown_pct: number;
  trades_today: number;
  max_trades_per_day: number;
  capital_at_risk: number;
  is_halted: boolean;
  positions_open: number;
}

export interface MarketStatus {
  is_open: boolean;
  in_trading_window: boolean;
  in_dead_zone: boolean;
  minutes_to_close: number;
  india_vix: number | null;
  cpr_type: string | null;
  day_bias: string | null;
  fyers_connected: boolean;
}

export interface AgentStatus {
  running: boolean;
  yolo_mode: boolean;
  autonomy_level: "manual" | "semi" | "yolo";
  last_action_at: string | null;
  pending_confirmations: number;
  positions_monitored: number;
  uptime_seconds: number | null;
}

export interface AgentLog {
  id: string;
  action_type: string;
  trade_id: string | null;
  details: Record<string, unknown>;
  requires_confirmation: boolean;
  confirmation_status: string | null;
  confirmed_at: string | null;
  created_at: string;
}

export interface BackgroundTask {
  name: string;
  type: "scheduler" | "startup" | "service";
  status: "pending" | "running" | "completed" | "failed" | "stopped";
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  metadata: Record<string, string>;
}

export interface WSMessage {
  event: string;
  data: Record<string, unknown>;
  timestamp: string;
}

// ── Research ──

export interface ResearchReport {
  id: string;
  symbol: string;
  display_name: string;
  status: "PENDING" | "IN_PROGRESS" | "COMPLETED" | "PARTIAL" | "FAILED";
  recommendation: "BUY" | "HOLD" | "SELL" | "AVOID" | null;
  confidence_score: number | null;
  executive_summary: string | null;
  report_json: Record<string, unknown> | null;
  report_markdown: string | null;
  agents_completed: number;
  agents_total: number;
  price_at_research: number | null;
  market_cap_cr: number | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  agent_runs: ResearchAgentRun[];
  created_at: string;
}

export interface ResearchAgentRun {
  agent_name: string;
  status: string;
  summary_text: string | null;
  findings_json: Record<string, unknown> | null;
  duration_seconds: number | null;
  error_message: string | null;
  data_sources_used: string[] | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ResearchReportListItem {
  id: string;
  symbol: string;
  display_name: string;
  status: string;
  recommendation: string | null;
  confidence_score: number | null;
  executive_summary: string | null;
  price_at_research: number | null;
  duration_seconds: number | null;
  created_at: string;
}

export interface ResearchAgentStatus {
  name: string;
  description: string;
  status: "pending" | "running" | "completed" | "failed";
  summary?: string;
  duration?: number;
  error?: string;
}

// ── Strategy 5: Intraday Futures ──

export interface S5WatchlistItem {
  symbol: string;
  composite_score: number;
  price: number;
  bias: string;
  llm_confidence?: string;
  llm_reason?: string;
  lot_size: number;
  factors: {
    rs_percentile: number;
    range_position: number;
    volume_trend: number;
    oi_change: number;
    adr_pct: number;
    adr_qualifies: boolean;
    sector: string | null;
    sector_score: number;
    delivery_pct: number;
    high_52w_proximity: number;
  };
  news?: {
    sentiment: string;
    score: number;
    key_themes?: string[];
    risk_events?: string[];
    catalyst_events?: string[];
    articles_count?: number;
    flagged?: boolean;
    headlines?: string[];
  };
  pdh: number | null;
  pdl: number | null;
  pdc: number | null;
  preopen_price?: number;
  gap_pct?: number;
  relative_gap_pct?: number;
  gap_direction?: "UP" | "DOWN" | null;
  nifty_gap_pct?: number;
  original_bias?: string;
  bias_source?: "screener" | "gap_override" | "gap_nudge";
  gap_alignment_bonus?: number;
  orb_high?: number;
  orb_low?: number;
  orb_range?: number;
}

export interface S5AgentLogEntry {
  timestamp: number;
  category: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface S5GlobalCues {
  dow_futures_pct?: number;
  sp500_close_pct?: number;
  nasdaq_close_pct?: number;
  nifty_pct?: number;
  crude_pct?: number;
  usdinr_pct?: number;
  dxy_pct?: number;
  us_vix?: number;
  india_vix_live?: number;
  nifty_gap_pct?: number;
  global_score?: number;
  overnight_bias?: "BULLISH" | "BEARISH" | "NEUTRAL";
  preopen_reassessed?: boolean;
  halted?: boolean;
  volatile_open?: boolean;
  flags?: string[];
  date?: string;
  // Absolute prices (from indicator:global:*_price Redis keys)
  crude_price?: number;
  usdinr_price?: number;
  sp500_price?: number;
  dow_futures_price?: number;
  nifty_price?: number;
  nasdaq_price?: number;
  dxy_price?: number;
}

export interface S5MorningBriefing {
  approach?: string;
  summary?: string;
  sector_bias?: string | string[];
  setup_priority?: string[];
  flags?: string[];
  max_lots_recommendation?: number;
  date?: string;
  generated_at?: number;
}

export interface S5DailyStats {
  total_trades: number;
  active_positions: number;
  closed_trades: number;
  wins: number;
  losses: number;
  net_pnl: number;
  win_rate: number;
}

export interface S5SetupStats {
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  avg_pnl: number;
  trades: number;
}

export interface S5SetupPerformance {
  period: { start: string; end: string; days: number };
  setups: Record<string, S5SetupStats>;
  overall: {
    wins: number;
    losses: number;
    win_rate: number;
    net_pnl: number;
    trades: number;
  };
}
