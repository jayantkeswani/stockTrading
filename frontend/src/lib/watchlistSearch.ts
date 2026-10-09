// Pure helpers for "+ Add to watchlist" when a row has no stored Fyers symbol — used by
// ActivePositions and lib/watchlistAdd (mobile)
// (unit-tested in src/__tests__/watchlistSearch.test.ts).

/** Strike as the symbol master writes it: the API serializes Decimals as strings ("71900.00"),
 *  so normalise through Number → "71900" (and "22350.5" stays "22350.5"). */
export function formatStrike(strike: number | string | null | undefined): string {
  const n = Number(strike);
  return Number.isFinite(n) ? String(n) : "";
}

/** Symbol-search query for an option contract, e.g. "SENSEX 71900CE"; the bare symbol for futures. */
export function optionSearchQuery(symbol: string, strike: number | string, optionType: string | null): string {
  return optionType ? `${symbol} ${formatStrike(strike)}${optionType}` : symbol;
}

/** Whether a search hit is the wanted contract (strike+type suffix for options, FUT for futures). */
export function matchesContract(fyersSymbol: string, strike: number | string, optionType: string | null): boolean {
  return optionType ? fyersSymbol.includes(`${formatStrike(strike)}${optionType}`) : fyersSymbol.includes("FUT");
}
