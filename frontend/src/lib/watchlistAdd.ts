import { api } from "@/lib/api";
import { useStore } from "@/store";

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
  strikePrice: number;
  expiryDate: string | null;
}): Promise<boolean> {
  const { fyersSymbol, symbol, optionType, strikePrice, expiryDate } = params;
  const isFut = !optionType;
  let fyers = fyersSymbol;
  if (!fyers) {
    const query = isFut ? symbol : `${symbol} ${strikePrice}${optionType}`;
    const res = await api.searchSymbols(query);
    const match = res.results?.find((r: { symbol: string }) =>
      isFut ? r.symbol.includes("FUT") : r.symbol.includes(`${strikePrice}${optionType}`)
    );
    fyers = match?.symbol ?? null;
  }
  if (!fyers) return false;

  const segment = isFut ? "FUT" : "OPT";
  const display = isFut ? `${symbol} FUT` : `${symbol} ${strikePrice} ${optionType}`;
  await api.addToWatchlist({
    symbol: fyers,
    display,
    segment,
    strike: strikePrice > 0 ? strikePrice : null,
    option_type: isFut ? null : optionType,
    expiry: expiryDate ?? undefined,
  });
  useStore.getState().addWatchlistItem({ symbol: fyers, display, segment });
  return true;
}
