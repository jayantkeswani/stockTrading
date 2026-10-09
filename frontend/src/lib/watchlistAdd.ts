import { api } from "@/lib/api";
import { useStore } from "@/store";
import { formatStrike, matchesContract, optionSearchQuery } from "@/lib/watchlistSearch";

/**
 * Resolve a Fyers symbol (searching when one isn't already known) and add it to
 * the personal watchlist (`/api/v1/watchlist`) + the Zustand `watchlistItems`
 * slice. Single source of truth for the mobile "+ Watch" buttons; mirrors the
 * desktop ScannerPanel / ActivePositions inline logic. Returns true on success.
 * Used by: MobileSignals, MobilePositions.
 */
export async function addToPersonalWatchlist(params: {
  fyersSymbol: string | null;
  symbol: string;
  optionType: string | null;
  strikePrice: number | string;
  expiryDate: string | null;
}): Promise<boolean> {
  const { fyersSymbol, symbol, optionType, strikePrice, expiryDate } = params;
  const isFut = !optionType;
  let fyers = fyersSymbol;
  if (!fyers) {
    const res = await api.searchSymbols(optionSearchQuery(symbol, strikePrice, optionType));
    const match = res.results?.find((r: { symbol: string }) =>
      matchesContract(r.symbol, strikePrice, optionType)
    );
    fyers = match?.symbol ?? null;
  }
  if (!fyers) return false;

  const segment = isFut ? "FUT" : "OPT";
  const strikeNum = Number(strikePrice);
  const display = isFut ? `${symbol} FUT` : `${symbol} ${formatStrike(strikePrice)} ${optionType}`;
  await api.addToWatchlist({
    symbol: fyers,
    display,
    segment,
    strike: strikeNum > 0 ? strikeNum : null,
    option_type: isFut ? null : optionType,
    expiry: expiryDate ?? undefined,
  });
  useStore.getState().addWatchlistItem({ symbol: fyers, display, segment });
  return true;
}
