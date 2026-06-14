import type { Position } from "@/lib/types";

/**
 * Whether a position profits when price FALLS (short futures or a sold option).
 *
 * Direction is derived primarily from the backend's authoritative
 * `unrealized_pnl` sign relative to the backend's last price move — this is
 * immune to a stale `target_price` (WS `position:update` refreshes
 * current_price/unrealized_pnl but not target, so a long-lived page can hold an
 * old target and invert the sign). Falls back to SL/target geometry — the same
 * rule the backend uses — which correctly handles bought options (target>entry
 * → long), sold options (target<entry → short), and long/short futures.
 * Used by: livePositionPnl, ActivePositions, PnLCard, MobilePositions, MobilePnlPill.
 */
export function isShortPosition(pos: Position): boolean {
  const u = pos.unrealized_pnl != null ? Number(pos.unrealized_pnl) : null;
  const cp = pos.current_price != null ? Number(pos.current_price) : null;
  if (u != null && u !== 0 && cp != null && cp !== pos.entry_price) {
    // long: pnl and (current - entry) share a sign; short: they differ.
    return (u > 0) !== (cp > pos.entry_price);
  }
  return pos.target_price != null
    ? pos.target_price < pos.entry_price
    : pos.stop_loss > pos.entry_price;
}

/**
 * The price-map keys a set of positions reads (`fyers_option_symbol || symbol`,
 * matching `livePositionPnl`). Feed to `usePrices()` so a position view
 * subscribes only to its own symbols. Used by: PnLCard, ActivePositions,
 * MobilePositions, MobilePnlPill.
 */
export function positionPriceKeys(positions: Position[]): string[] {
  return positions.map((p) => p.fyers_option_symbol || p.symbol);
}

export interface LivePnl {
  currentPrice: number;
  pnl: number;
  pnlPct: number;
  slDistance: number;
  isShort: boolean;
}

/**
 * Live P&L for an open position using the freshest available price
 * (`prices[fyers_option_symbol || symbol]` → backend `current_price`), with the
 * direction resolved by `isShortPosition`. Returns currentPrice, pnl, pnlPct,
 * and SL distance %. Used by: ActivePositions, PnLCard, MobilePositions, MobilePnlPill.
 */
export function livePositionPnl(pos: Position, prices: Record<string, { ltp?: number }>): LivePnl {
  const isShort = isShortPosition(pos);
  const live = prices[pos.fyers_option_symbol || pos.symbol]?.ltp;
  const currentPrice = live ?? (pos.current_price != null ? Number(pos.current_price) : 0);
  const diff = isShort ? pos.entry_price - currentPrice : currentPrice - pos.entry_price;
  const pnl = currentPrice && pos.entry_price > 0 ? diff * pos.quantity : Number(pos.unrealized_pnl ?? 0);
  const pnlPct = pos.entry_price > 0 && currentPrice ? (diff / pos.entry_price) * 100 : 0;
  const slDistance = currentPrice && pos.stop_loss
    ? (isShort ? (pos.stop_loss - currentPrice) / currentPrice : (currentPrice - pos.stop_loss) / currentPrice) * 100
    : 0;
  return { currentPrice, pnl, pnlPct, slDistance, isShort };
}
