"use client";

import { useEffect, useState, Fragment } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/store";
import { pnlColor } from "@/lib/formatters";
import type { S5WatchlistItem } from "@/lib/types";

const SCORE_TOOLTIP = `Composite Score — 8 quant factors:
• RS Percentile      20%  (IBD-style relative strength)
• Range Position     15%  (close vs prev-day high/low)
• Volume Trend       15%  (5-day / 20-day ratio)
• OI Change          15%  (long buildup / short covering)
• ADR%               10%  (average daily range)
• Trend Quality      10%  (stock trend strength)
• Delivery %         10%  (NSE bhav copy)
• 52W High Proximity  5%  (nearness to 52-week high)

Adjusted ±10 pts by Stage 2 news sentiment.`;

const CONF_TOOLTIP = `Signal Confidence — 9 factors at trade time:
• RVOL strength      15%  (relative volume normalised)
• Setup quality      14%  (ORB 80%, PDH/PDL & VWAP 70%, Gap 60%)
• Nifty bias         12%  (STRONG 100%, MODERATE 70%, WEAK 40%)
• Phase timing       12%  (Morning 100%, Afternoon 70%, Caution 40%)
• Volume quality     10%  (breakout candle vs avg)
• Gap alignment      10%  (signal direction vs stock gap)
• Stock trend        10%  (multi-day trend direction)
• OI direction       10%  (futures OI building vs unwinding)
• Screener rank       7%  (composite score / 100)

Missing-data default: 0.2 (penalises low-context signals).
This score drives the confidence bar on each signal card.`;

function InfoTip({ content }: { content: string }) {
  return (
    <span className="relative group inline-block ml-0.5 align-middle">
      <span className="text-text-muted text-[9px] font-mono cursor-help select-none opacity-50 group-hover:opacity-100 transition-opacity">
        ?
      </span>
      <div className="pointer-events-none absolute left-0 top-4 z-50 hidden group-hover:block w-64 bg-bg-elevated border border-border rounded p-2 text-[10px] font-mono text-text-secondary whitespace-pre-line shadow-xl leading-relaxed">
        {content}
      </div>
    </span>
  );
}

export function Watchlist({ date = null, onOpenChart }: { date?: string | null; onOpenChart?: (symbol: string) => void }) {
  const [items, setItems] = useState<S5WatchlistItem[]>([]);
  const [sortKey, setSortKey] = useState<"composite_score" | "rs_percentile">("composite_score");
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const prices = useStore((s) => s.prices);
  const updatePrice = useStore((s) => s.updatePrice);

  const isHistorical = date != null;

  const fetchWatchlist = async () => {
    try {
      const data = await api.getIntradayFuturesWatchlist(date ?? undefined);
      setItems(data);
      if (!isHistorical && data.length > 0) {
        const fyersSymbols = data.map((d: S5WatchlistItem) => `NSE:${d.symbol}-EQ`);
        const priceData = await api.fetchBatchPrices(fyersSymbols);
        for (const [sym, pd] of Object.entries(priceData)) {
          updatePrice(sym, pd as never);
        }
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

  const sentimentLabel = (s: string) => {
    if (s === "positive") return "positive";
    if (s === "negative") return "negative";
    if (s === "neutral") return "neutral";
    return s;
  };

  const sentimentColor = (score: number) =>
    score > 0.3 ? "text-profit" : score < -0.3 ? "text-loss" : "text-text-muted";

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
              <th className="text-right px-2 py-1">
                Score<InfoTip content={SCORE_TOOLTIP} />
              </th>
              <th className="text-right px-2 py-1">RS</th>
              <th className="text-right px-2 py-1">ADR%</th>
              <th className="text-right px-2 py-1">ORB</th>
              <th className="text-right px-2 py-1">Gap%</th>
              <th className="text-center px-2 py-1">Bias</th>
              <th className="text-center px-2 py-1">
                Conf<InfoTip content={CONF_TOOLTIP} />
              </th>
              <th className="text-center px-2 py-1">News</th>
              <th className="text-right px-2 py-1">Price</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => {
              const isExpanded = expandedSymbol === item.symbol;
              const headlines = item.news?.headlines?.length ? item.news.headlines : [];

              return (
                <Fragment key={item.symbol}>
                  <tr
                    className="border-t border-border/50 hover:bg-bg-tertiary"
                  >
                    <td className="px-2 py-1">
                      {onOpenChart ? (
                        <button
                          onClick={() => onOpenChart(item.symbol)}
                          className="text-text-primary hover:text-accent transition-colors cursor-pointer"
                        >
                          {item.symbol}
                        </button>
                      ) : (
                        <span className="text-text-primary">{item.symbol}</span>
                      )}
                    </td>
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
                      {item.bias_source && item.bias_source !== "screener" ? (
                        <span className="relative group inline-block">
                          <span className={`text-[10px] px-1 py-px rounded cursor-default ${
                            item.bias === "BULLISH" ? "bg-profit/20 text-profit" :
                            item.bias === "BEARISH" ? "bg-loss/20 text-loss" :
                            "bg-text-muted/20 text-text-muted"
                          }`}>
                            {item.bias.slice(0, 4)}•
                          </span>
                          <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-max opacity-0 group-hover:opacity-100 transition-opacity z-50">
                            <span className="block bg-bg-elevated border border-border rounded px-2 py-1 shadow-lg text-left">
                              <span className="block text-[9px] font-mono text-accent whitespace-nowrap">
                                {item.bias_source.replace(/_/g, " ")}
                              </span>
                              <span className="block text-[9px] font-mono text-text-muted whitespace-nowrap">
                                was {item.original_bias ?? "unknown"}
                              </span>
                            </span>
                          </span>
                        </span>
                      ) : (
                        <span className={`text-[10px] px-1 py-px rounded ${
                          item.bias === "BULLISH" ? "bg-profit/20 text-profit" :
                          item.bias === "BEARISH" ? "bg-loss/20 text-loss" :
                          "bg-text-muted/20 text-text-muted"
                        }`}>
                          {item.bias.slice(0, 4)}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1 text-center">
                      <button
                        onClick={() => setExpandedSymbol(isExpanded ? null : item.symbol)}
                        className={`text-[10px] px-1 py-px rounded cursor-pointer ${
                          item.llm_confidence === "HIGH" ? "bg-profit/20 text-profit" :
                          item.llm_confidence === "LOW" ? "bg-loss/20 text-loss" :
                          "bg-accent/10 text-accent"
                        } ${isExpanded ? "ring-1 ring-current" : ""}`}
                        title={item.llm_reason || undefined}
                      >
                        {item.llm_confidence || "—"}
                      </button>
                    </td>
                    <td className="px-2 py-1 text-center">
                      {item.news ? (
                        <span className="relative group inline-block">
                          <span className={`text-[10px] cursor-default ${sentimentColor(item.news.score)}`}>
                            {sentimentLabel(item.news.sentiment)}{" "}
                            <span className="opacity-70">
                              ({item.news.score > 0 ? "+" : ""}{item.news.score.toFixed(1)})
                            </span>
                          </span>
                          {headlines.length > 0 && (
                            <span className="pointer-events-none absolute top-full left-1/2 -translate-x-1/2 mt-1.5 z-50 opacity-0 group-hover:opacity-100 transition-none">
                              <span className="block bg-bg-elevated border border-border rounded px-2.5 py-2 shadow-xl w-72 text-left">
                                {headlines.map((h, i) => (
                                  <span key={i} className="flex gap-1.5 text-[10px] font-mono text-text-secondary leading-snug mb-1.5 last:mb-0">
                                    <span className="text-text-muted shrink-0">•</span>
                                    <span>{h}</span>
                                  </span>
                                ))}
                              </span>
                            </span>
                          )}
                        </span>
                      ) : <span className="text-text-muted">—</span>}
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
                  {isExpanded && item.llm_reason && (
                    <tr key={`${item.symbol}-expanded`} className="bg-bg-tertiary border-t border-border/30">
                      <td colSpan={10} className="px-3 py-2">
                        <div className="flex items-start gap-2">
                          <span className="text-[9px] font-mono text-text-muted uppercase tracking-wider shrink-0 mt-px">
                            AI Reason
                          </span>
                          <span className="text-[10px] font-mono text-text-secondary leading-relaxed">
                            {item.llm_reason}
                          </span>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
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
