export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8080/ws";

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
};

export const STATUS_COLORS: Record<string, string> = {
  OPEN: "bg-accent/20 text-accent",
  CLOSED: "bg-text-muted/20 text-text-secondary",
  PENDING: "bg-warning/20 text-warning",
  EXECUTED: "bg-profit/20 text-profit",
  REJECTED: "bg-loss/20 text-loss",
  EXPIRED: "bg-text-muted/20 text-text-muted",
};
