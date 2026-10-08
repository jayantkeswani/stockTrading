export function getWsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8080/ws";
  const { hostname, port } = window.location;
  if (port === "3000") return `ws://${hostname}:8080/ws`;
  return `ws://${hostname}/ws`;
}

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "1D";

/** Strip exchange prefix (e.g. "NSE:NIFTY26APRFUT" → "NIFTY26APRFUT"). */
export function displaySymbol(symbol: string): string {
  return symbol.includes(":") ? symbol.split(":")[1] : symbol;
}

export const SYMBOLS = [
  "NIFTY",
  "BANKNIFTY",
  "FINNIFTY",
  "SENSEX",
  "MIDCPNIFTY",
] as const;

export const STRATEGY_LABELS: Record<string, string> = {
  orb: "ORB",
  vwap_pullback: "VWAP Pullback",
  gamma_scalping: "Gamma Scalp",
  can_slim: "CAN SLIM",
  intraday_futures: "Intraday Futures",
  breakout_retest: "Breakout Retest",
  vwap_reclaim: "VWAP Reclaim",
  intraday_hunter: "Intraday Hunter",
  intraday_hunter_v2: "Intraday Hunter v2",
};

// Strategies a YOLO profile can be scoped to, and the setup_types each emits.
// Drives the per-profile strategy/setup execution filter in Settings. Stub
// strategies (orb, gamma_scalping) are omitted; option strategies have no setups.
export const STRATEGY_SETUPS: Record<string, string[]> = {
  vwap_pullback: [],
  vwap_reclaim: [],
  can_slim: [],
  intraday_futures: ["ORB", "PDH_PDL", "GAP_CONTINUATION", "VWAP_BOUNCE"],
  breakout_retest: ["ORB_RETEST", "PDH_PDL_RETEST", "SWING_RETEST"],
  intraday_hunter_v2: [],  // parallel paper v2; same regime-only setup_type
  intraday_hunter: [],  // LLM-agent index-options basket; setup_type is a regime, no sub-filter chips
};

export const FILTERABLE_STRATEGIES: string[] = Object.keys(STRATEGY_SETUPS);

export const STATUS_COLORS: Record<string, string> = {
  OPEN: "bg-accent/20 text-accent",
  CLOSED: "bg-text-muted/20 text-text-secondary",
  PENDING: "bg-warning/20 text-warning",
  EXECUTED: "bg-profit/20 text-profit",
  REJECTED: "bg-loss/20 text-loss",
  EXPIRED: "bg-text-muted/20 text-text-muted",
};
