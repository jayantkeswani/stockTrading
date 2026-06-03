"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { pnlColor, formatINR } from "@/lib/formatters";
import { SYMBOLS } from "@/lib/constants";
import { SymbolSearchInput } from "@/components/shared/SymbolSearchInput";
import type { SymbolResult } from "@/components/shared/SymbolSearchInput";
import { addToPersonalWatchlist } from "@/lib/watchlistAdd";
import type { S5WatchlistItem } from "@/lib/types";

type FilterKey = "personal" | "screened" | "pinned";

function biasCls(bias: string) {
  return bias === "BULLISH" ? "bg-profit/15 text-profit"
    : bias === "BEARISH" ? "bg-loss/15 text-loss"
    : "bg-text-muted/15 text-text-secondary";
}
function confCls(c: string | null) {
  return c === "HIGH" ? "bg-profit/15 text-profit"
    : c === "LOW" ? "bg-loss/15 text-loss"
    : "bg-accent/10 text-accent";
}
function sentCls(score: number) {
  return score > 0.3 ? "bg-profit/15 text-profit" : score < -0.3 ? "bg-loss/15 text-loss" : "bg-text-muted/15 text-text-secondary";
}

function KV({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] font-mono text-text-muted tracking-wide">{label}</span>
      <span className={`text-[12px] font-mono font-medium mt-0.5 ${cls ?? ""}`}>{value}</span>
    </div>
  );
}

/** Personal-watchlist row (indices + custom items): tap the row to open the
 *  chart, tap × to remove a custom item. Mirrors the desktop dashboard Watchlist. */
function PersonalRow({ symbol, display, segment, price, removable, onOpenChart, onRemove }: {
  symbol: string;
  display: string;
  segment?: string;
  price?: { ltp?: number; change_pct?: number | null; change?: number | null };
  removable?: boolean;
  onOpenChart: (symbol: string, display?: string) => void;
  onRemove?: (symbol: string) => void;
}) {
  const changePct = price?.change_pct ?? null;
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2.5 border-b border-border last:border-b-0">
      <button onClick={() => onOpenChart(symbol, display)} className="flex items-center gap-1.5 text-left flex-1 min-w-0">
        <span className="text-[14px] font-mono font-bold text-text-primary truncate">{display}</span>
        {segment && segment !== "EQ" && segment !== "INDEX" && (
          <span className={`text-[10px] font-mono font-semibold ${segment === "FUT" ? "text-warning" : "text-profit"}`}>{segment}</span>
        )}
      </button>
      <div className="flex items-center gap-2 shrink-0">
        <div className="text-right">
          <div className="text-[13px] font-mono text-text-primary">{price?.ltp != null ? formatINR(price.ltp) : "—"}</div>
          {changePct != null && (
            <div className={`text-[10px] font-mono ${pnlColor(changePct)}`}>{changePct > 0 ? "+" : ""}{changePct.toFixed(2)}%</div>
          )}
        </div>
        {removable && onRemove && (
          <button onClick={() => onRemove(symbol)} aria-label={`Remove ${display}`} className="text-text-muted hover:text-loss text-[13px] leading-none px-1">×</button>
        )}
      </div>
    </div>
  );
}

function WatchlistCard({ item, prices, onOpenChart }: {
  item: S5WatchlistItem;
  prices: Record<string, { ltp?: number; change_pct?: number | null }>;
  onOpenChart: (symbol: string, display?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [watchStatus, setWatchStatus] = useState<"idle" | "adding" | "done" | "error">("idle");
  const p = prices[`NSE:${item.symbol}-EQ`] || prices[item.symbol];
  const ltp = p?.ltp ?? item.price;
  const changePct = p?.change_pct ?? null;
  const f = item.factors;
  const news = item.news;

  const handleWatch = async () => {
    if (watchStatus !== "idle") return;
    setWatchStatus("adding");
    // Screener rows are stock futures (S5) — add the futures contract (resolved
    // by search since the screener item carries no Fyers symbol).
    const ok = await addToPersonalWatchlist({
      fyersSymbol: null, symbol: item.symbol, optionType: null, strikePrice: 0, expiryDate: null,
    }).catch(() => false);
    setWatchStatus(ok ? "done" : "error");
    setTimeout(() => setWatchStatus("idle"), ok ? 2000 : 2500);
  };

  return (
    <div className="rounded-lg border border-border bg-bg-secondary mb-1.5 overflow-hidden">
      <div onClick={() => setOpen((o) => !o)} className="w-full px-3 py-2.5 text-left cursor-pointer">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <button
                onClick={(e) => { e.stopPropagation(); onOpenChart(item.symbol); }}
                className="text-[15px] font-mono font-bold text-text-primary hover:text-accent transition-colors"
              >
                {item.symbol}
              </button>
              {item.manual && (
                <span className="text-[9px] font-mono px-1 py-px rounded border border-accent/40 text-accent/70">P</span>
              )}
              <span className={`text-[10px] font-mono font-bold px-1.5 py-px rounded ${biasCls(item.bias)}`}>
                {item.bias.slice(0, 4)}{item.bias_source && item.bias_source !== "screener" ? "•" : ""}
              </span>
              {item.llm_confidence && (
                <span className={`text-[9px] font-mono font-bold px-1.5 py-px rounded ${confCls(item.llm_confidence)}`}>
                  {item.llm_confidence}
                </span>
              )}
            </div>
            <div className="text-[12px] font-mono text-text-muted mt-1">
              ₹{ltp.toFixed(1)}
              {changePct != null && (
                <span className={`ml-1.5 ${pnlColor(changePct)}`}>{changePct > 0 ? "+" : ""}{changePct.toFixed(2)}%</span>
              )}
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className="text-[17px] font-mono font-bold text-accent">
              {item.manual && !item.composite_score ? "—" : item.composite_score}
            </div>
            <div className="text-[8px] font-mono text-text-muted tracking-wide">SCORE</div>
          </div>
          <span className={`text-text-muted text-[13px] mt-1 transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
        </div>

        {/* glance metrics */}
        <div className="flex gap-3 mt-2 text-[10px] font-mono text-text-muted flex-wrap">
          <span>RS <b className="text-text-primary">{f.rs_percentile}</b></span>
          <span>ADR <b className="text-text-primary">{f.adr_pct.toFixed(1)}%</b></span>
          <span>ORB <b className="text-text-primary">{item.orb_range != null ? item.orb_range.toFixed(1) : "—"}</b></span>
          {item.gap_pct != null && (
            <span>Gap <b className={item.gap_pct > 0.5 ? "text-profit" : item.gap_pct < -0.5 ? "text-loss" : "text-text-primary"}>
              {item.gap_pct > 0 ? "+" : ""}{item.gap_pct.toFixed(1)}%</b></span>
          )}
          {news && <span>News <b className={news.score > 0.3 ? "text-profit" : news.score < -0.3 ? "text-loss" : "text-text-primary"}>
            {news.score > 0 ? "+" : ""}{news.score.toFixed(1)}</b></span>}
        </div>
      </div>

      {open && (
        <div className="px-3 py-2.5 border-t border-border bg-bg-primary animate-fade-in">
          {item.llm_reason && (
            <>
              <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-0.5">
                LLM Confidence{item.llm_confidence ? ` · ${item.llm_confidence}` : ""}
              </p>
              <p className="text-[12px] font-mono text-text-secondary leading-relaxed">{item.llm_reason}</p>
            </>
          )}
          <div className="grid grid-cols-3 gap-y-2.5 gap-x-3 mt-2.5">
            <KV label="Range pos" value={f.range_position != null ? f.range_position.toFixed(2) : "—"} />
            <KV label="Vol trend" value={f.volume_trend != null ? `${f.volume_trend.toFixed(1)}×` : "—"} />
            <KV label="OI change" value={f.oi_change != null ? `${f.oi_change > 0 ? "+" : ""}${f.oi_change.toFixed(1)}%` : "—"} />
            <KV label="Delivery" value={f.delivery_pct != null ? `${f.delivery_pct.toFixed(0)}%` : "—"} />
            <KV label="Sector" value={f.sector ?? "—"} />
            <KV label="52w prox" value={f.high_52w_proximity != null ? f.high_52w_proximity.toFixed(2) : "—"} />
            <KV label="PDH" value={item.pdh != null ? item.pdh.toFixed(1) : "—"} />
            <KV label="PDL" value={item.pdl != null ? item.pdl.toFixed(1) : "—"} />
            <KV label="ORB H/L" value={item.orb_high != null && item.orb_low != null ? `${item.orb_high.toFixed(0)}/${item.orb_low.toFixed(0)}` : "—"} />
          </div>
          {news && (
            <>
              <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mt-2.5 mb-1">
                News · <span className={`px-1 py-px rounded ${sentCls(news.score)}`}>{news.sentiment} {news.score > 0 ? "+" : ""}{news.score.toFixed(1)}</span>
              </p>
              {news.headlines && news.headlines.length > 0 ? (
                news.headlines.map((h, i) => (
                  <p key={i} className="text-[11px] font-mono text-text-secondary leading-snug pl-2.5 relative before:content-['›'] before:absolute before:left-0 before:text-text-muted">{h}</p>
                ))
              ) : (
                <p className="text-[11px] font-mono text-text-muted">no material headlines</p>
              )}
            </>
          )}
          <button
            onClick={handleWatch}
            className={`w-full mt-3 text-[12px] font-mono py-1.5 rounded border ${
              watchStatus === "done" ? "border-profit/30 text-profit bg-profit/10"
                : watchStatus === "error" ? "border-loss/30 text-loss bg-loss/10"
                : "border-border bg-bg-tertiary text-text-secondary"
            }`}
          >
            {watchStatus === "adding" ? "Adding…" : watchStatus === "done" ? "✓ Added to watchlist" : watchStatus === "error" ? "Failed" : "+ Watch"}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Mobile Watchlist tab with three views (pills): **Personal · Screened · Pinned**.
 * - Personal: the dashboard `/api/v1/watchlist` (5 indices + custom items) as
 *   price rows, with a `SymbolSearchInput` to add any symbol and × to remove —
 *   shares the `watchlistItems` store slice with the desktop dashboard.
 * - Screened / Pinned: the Strategy-5 screener cards (`getIntradayFuturesWatchlist`),
 *   split by `item.manual` (Pinned = permanently-pinned, Screened = the rest).
 * Every view opens the chart overlay: tap a Personal row, or a screener card's
 * symbol name, → `onOpenChart`. Polls screener (30s) + personal prices (15s).
 * Used by: MobileShell.
 */
export function MobileWatchlist({ refreshKey, onOpenChart }: { refreshKey?: number; onOpenChart: (symbol: string, display?: string) => void }) {
  const [items, setItems] = useState<S5WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterKey, setFilterKey] = useState<FilterKey>("personal");
  const [sortKey, setSortKey] = useState<"composite_score" | "rs_percentile">("composite_score");
  const prices = useStore((s) => s.prices);
  const updatePrice = useStore((s) => s.updatePrice);
  const { watchlistItems, setWatchlistItems, addWatchlistItem, removeWatchlistItem } = useStore(useShallow((s) => ({
    watchlistItems: s.watchlistItems,
    setWatchlistItems: s.setWatchlistItems,
    addWatchlistItem: s.addWatchlistItem,
    removeWatchlistItem: s.removeWatchlistItem,
  })));

  const fetchPersonalPrices = useCallback(async (symbols: string[]) => {
    if (symbols.length === 0) return;
    try {
      const data = await api.fetchBatchPrices(symbols);
      for (const [sym, pd] of Object.entries(data)) updatePrice(sym, pd as never);
    } catch { /* silent */ }
  }, [updatePrice]);

  // Personal watchlist (shared store slice) + its prices.
  useEffect(() => {
    let cancelled = false;
    api.getWatchlist()
      .then((data) => {
        if (cancelled) return;
        const list = data.items.map((i) => ({ symbol: i.symbol, display: i.display, segment: i.segment || "EQ" }));
        setWatchlistItems(list);
        fetchPersonalPrices(list.map((i) => i.symbol));
      })
      .catch(() => { /* silent */ });
    return () => { cancelled = true; };
  }, [setWatchlistItems, fetchPersonalPrices, refreshKey]);

  useEffect(() => {
    if (watchlistItems.length === 0) return;
    const id = setInterval(() => fetchPersonalPrices(watchlistItems.map((i) => i.symbol)), 15_000);
    return () => clearInterval(id);
  }, [watchlistItems, fetchPersonalPrices]);

  // S5 screener (Screened / Pinned).
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await api.getIntradayFuturesWatchlist();
        if (cancelled) return;
        setItems(data);
        if (data.length > 0) fetchPersonalPrices(data.map((d) => `NSE:${d.symbol}-EQ`));
      } catch { /* silent */ }
      finally { if (!cancelled) setLoading(false); }
    }
    load();
    const id = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [fetchPersonalPrices, refreshKey]);

  const handleAdd = useCallback(async (result: SymbolResult) => {
    if (watchlistItems.some((c) => c.symbol === result.symbol)) return;
    const item = { symbol: result.symbol, display: result.display, segment: result.segment };
    addWatchlistItem(item);
    fetchPersonalPrices([result.symbol]);
    try {
      await api.addToWatchlist({
        symbol: result.symbol, display: result.display, segment: result.segment,
        strike: result.strike || null, option_type: result.type || null, expiry: result.expiry,
      });
    } catch { /* optimistic add stands */ }
  }, [watchlistItems, addWatchlistItem, fetchPersonalPrices]);

  const handleRemove = useCallback(async (symbol: string) => {
    removeWatchlistItem(symbol);
    try { await api.removeFromWatchlist(symbol); } catch { /* ignore */ }
  }, [removeWatchlistItem]);

  const screenerItems = items.filter((i) => filterKey === "pinned" ? i.manual : !i.manual);
  const sorted = [...screenerItems].sort((a, b) =>
    sortKey === "rs_percentile" ? b.factors.rs_percentile - a.factors.rs_percentile : b.composite_score - a.composite_score
  );

  const PILLS: { key: FilterKey; label: string }[] = [
    { key: "personal", label: "Personal" },
    { key: "screened", label: "Screened" },
    { key: "pinned", label: "Pinned" },
  ];

  return (
    <div className="p-3">
      <div className="sticky top-0 z-10 -mx-3 px-3 pb-2 mb-2 bg-bg-primary border-b border-border flex items-center gap-1.5">
        {PILLS.map((p) => (
          <button
            key={p.key}
            onClick={() => setFilterKey(p.key)}
            className={`text-[12px] font-mono px-2.5 py-1 rounded border ${
              filterKey === p.key ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"
            }`}
          >{p.label}</button>
        ))}
        {filterKey !== "personal" && (
          <button
            onClick={() => setSortKey((s) => (s === "composite_score" ? "rs_percentile" : "composite_score"))}
            className="ml-auto text-[12px] font-mono px-2.5 py-1 rounded border bg-bg-tertiary text-text-secondary border-border"
          >Sort: {sortKey === "composite_score" ? "Score" : "RS"}</button>
        )}
      </div>

      {filterKey === "personal" ? (
        <div className="rounded-lg border border-border bg-bg-secondary">
          <SymbolSearchInput onSelect={handleAdd} placeholder="search symbols to add..." direction="down" />
          {SYMBOLS.map((sym) => (
            <PersonalRow key={sym} symbol={sym} display={sym} price={prices[sym]} onOpenChart={onOpenChart} />
          ))}
          {watchlistItems.map((it) => (
            <PersonalRow key={it.symbol} symbol={it.symbol} display={it.display} segment={it.segment} price={prices[it.symbol]} removable onRemove={handleRemove} onOpenChart={onOpenChart} />
          ))}
        </div>
      ) : loading ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">loading…</div>
      ) : sorted.length === 0 ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">
          {filterKey === "pinned" ? "no pinned stocks" : items.length === 0 ? "no watchlist — run screener" : "no screened results"}
        </div>
      ) : (
        sorted.map((item) => <WatchlistCard key={item.symbol} item={item} prices={prices} onOpenChart={onOpenChart} />)
      )}
    </div>
  );
}
