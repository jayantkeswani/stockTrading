"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/store";
import { pnlColor } from "@/lib/formatters";
import { subscribeSymbols } from "@/hooks/useWebSocket";
import type { S5WatchlistItem } from "@/lib/types";

export function Watchlist({ date = null }: { date?: string | null }) {
  const [items, setItems] = useState<S5WatchlistItem[]>([]);
  const [sortKey, setSortKey] = useState<"composite_score" | "rs_percentile">("composite_score");
  const prices = useStore((s) => s.prices);

  const isHistorical = date != null;

  const fetchWatchlist = async () => {
    try {
      const data = await api.getIntradayFuturesWatchlist(date ?? undefined);
      setItems(data);
      if (!isHistorical && data.length > 0) {
        const syms = data.map((d: S5WatchlistItem) => `NSE:${d.symbol}-EQ`);
        subscribeSymbols(syms);
      }
    } catch {
      /* silent */
    }
  };

  useEffect(() => {
    fetchWatchlist();
    if (isHistorical) return;
    const interval = setInterval(fetchWatchlist, 30_000);
    return () => clearInterval(interval);
  }, [date]);

  const sorted = [...items].sort((a, b) => {
    if (sortKey === "rs_percentile") return b.factors.rs_percentile - a.factors.rs_percentile;
    return b.composite_score - a.composite_score;
  });

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
        <span className="flex items-center gap-1.5 text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          <svg className="w-3.5 h-3.5 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
          </svg>
          Futures Watchlist ({items.length})
        </span>
        <select
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
          className="text-[10px] font-mono bg-bg-tertiary border border-border rounded px-1 py-0.5 text-text-secondary"
        >
          <option value="composite_score">Score</option>
          <option value="rs_percentile">RS</option>
        </select>
      </div>
      <div className="max-h-[400px] overflow-y-auto">
        <table className="w-full text-xs font-mono">
          <thead className="sticky top-0 bg-bg-secondary">
            <tr className="text-text-muted text-[10px] uppercase tracking-wider">
              <th className="text-left px-2 py-1">Symbol</th>
              <th className="text-right px-2 py-1">Score</th>
              <th className="text-right px-2 py-1">RS</th>
              <th className="text-right px-2 py-1">ADR%</th>
              <th className="text-right px-2 py-1">ORB</th>
              <th className="text-right px-2 py-1">Gap%</th>
              <th className="text-center px-2 py-1">Bias</th>
              <th className="text-center px-2 py-1">Conf</th>
              <th className="text-center px-2 py-1">News</th>
              <th className="text-right px-2 py-1">Price</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => (
              <tr key={item.symbol} className="border-t border-border/50 hover:bg-bg-tertiary">
                <td className="px-2 py-1 text-text-primary">{item.symbol}</td>
                <td className="px-2 py-1 text-right text-accent">{item.composite_score}</td>
                <td className="px-2 py-1 text-right">{item.factors.rs_percentile}</td>
                <td className="px-2 py-1 text-right">{item.factors.adr_pct.toFixed(1)}</td>
                <td className="px-2 py-1 text-right">
                  {item.orb_range != null ? (
                    <span
                      className="text-[10px] text-accent"
                      title={`H: ${item.orb_high?.toFixed(1)}  L: ${item.orb_low?.toFixed(1)}`}
                    >
                      {item.orb_range.toFixed(1)}
                    </span>
                  ) : (
                    <span className="text-text-muted">—</span>
                  )}
                </td>
                <td className="px-2 py-1 text-right">
                  {item.gap_pct != null ? (
                    <span className={`text-[10px] ${
                      item.gap_pct > 0.5 ? "text-profit" :
                      item.gap_pct < -0.5 ? "text-loss" :
                      "text-text-muted"
                    }`} title={item.relative_gap_pct != null ? `Rel: ${item.relative_gap_pct > 0 ? "+" : ""}${item.relative_gap_pct.toFixed(1)}%` : undefined}>
                      {item.gap_pct > 0 ? "+" : ""}{item.gap_pct.toFixed(1)}
                    </span>
                  ) : "—"}
                </td>
                <td className="px-2 py-1 text-center">
                  <span className={`text-[10px] px-1 py-px rounded ${
                    item.bias === "BULLISH" ? "bg-profit/20 text-profit" :
                    item.bias === "BEARISH" ? "bg-loss/20 text-loss" :
                    "bg-text-muted/20 text-text-muted"
                  }`} title={item.bias_source && item.bias_source !== "screener" ? `${item.bias_source} (was ${item.original_bias})` : undefined}>
                    {item.bias.slice(0, 4)}
                    {item.bias_source && item.bias_source !== "screener" ? "•" : ""}
                  </span>
                </td>
                <td className="px-2 py-1 text-center">
                  <span className={`text-[10px] px-1 py-px rounded ${
                    item.llm_confidence === "HIGH" ? "bg-profit/20 text-profit" :
                    item.llm_confidence === "LOW" ? "bg-loss/20 text-loss" :
                    "bg-accent/10 text-accent"
                  }`}>
                    {item.llm_confidence || "—"}
                  </span>
                </td>
                <td className="px-2 py-1 text-center">
                  {item.news ? (
                    <span className={`text-[10px] ${
                      item.news.score > 0.3 ? "text-profit" :
                      item.news.score < -0.3 ? "text-loss" :
                      "text-text-muted"
                    }`}>
                      {item.news.score > 0 ? "+" : ""}{item.news.score.toFixed(1)}
                    </span>
                  ) : "—"}
                </td>
                <td className="px-2 py-1 text-right">
                  {(() => {
                    const p = prices[item.symbol] || prices[`NSE:${item.symbol}-EQ`];
                    const ltp = p?.ltp ?? item.price;
                    const changePct = p?.change_pct ?? null;
                    const change = p?.change ?? null;
                    return (
                      <>
                        <div className="text-text-primary">{ltp.toFixed(1)}</div>
                        {changePct != null && (
                          <div className={`text-[10px] font-mono ${pnlColor(changePct)}`}>
                            {changePct > 0 ? "+" : ""}{changePct.toFixed(2)}%
                            {change != null && (
                              <span className="text-text-muted ml-0.5">
                                ({change > 0 ? "+" : ""}{change.toFixed(1)})
                              </span>
                            )}
                          </div>
                        )}
                      </>
                    );
                  })()}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={10} className="px-2 py-4 text-center text-text-muted text-xs font-mono">
                  no watchlist — run screener
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
