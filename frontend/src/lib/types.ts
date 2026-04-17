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
  strategy_name: string;
  is_paper: boolean;
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
  pnl: number | null;
  pnl_percent: number | null;
  entry_time: string;
  exit_time: string | null;
  notes: string | null;
  created_at: string;
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
}

export interface RiskDashboard {
  capital: number;
  daily_pnl: number;
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
