export function getWsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8080/ws";
  const host = window.location.hostname;
  return `ws://${host}:8080/ws`;
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
};

export const STATUS_COLORS: Record<string, string> = {
  OPEN: "bg-accent/20 text-accent",
  CLOSED: "bg-text-muted/20 text-text-secondary",
  PENDING: "bg-warning/20 text-warning",
  EXECUTED: "bg-profit/20 text-profit",
  REJECTED: "bg-loss/20 text-loss",
  EXPIRED: "bg-text-muted/20 text-text-muted",
};
