"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/store";
import { pnlColor } from "@/lib/formatters";
import type { S5WatchlistItem } from "@/lib/types";

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

function WatchlistCard({ item, prices }: { item: S5WatchlistItem; prices: Record<string, { ltp?: number; change_pct?: number | null }> }) {
  const [open, setOpen] = useState(false);
  const p = prices[`NSE:${item.symbol}-EQ`] || prices[item.symbol];
  const ltp = p?.ltp ?? item.price;
  const changePct = p?.change_pct ?? null;
  const f = item.factors;
  const news = item.news;

  return (
    <div className="rounded-lg border border-border bg-bg-secondary mb-1.5 overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="w-full px-3 py-2.5 text-left">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[15px] font-mono font-bold text-text-primary">{item.symbol}</span>
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
      </button>

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
        </div>
      )}
    </div>
  );
}

/**
 * Mobile Watchlist tab: the Strategy 5 intraday screener as expandable cards.
 * Collapsed: symbol, bias, LLM confidence, price/change, score + RS/ADR/ORB/
 * Gap/News strip. Expand: LLM reason, full factor grid, news headlines.
 * Fetches on mount + polls every 30s (with batch prices). Used by: MobileShell.
 */
export function MobileWatchlist({ refreshKey }: { refreshKey?: number }) {
  const [items, setItems] = useState<S5WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterKey, setFilterKey] = useState<"all" | "screened" | "pinned">("all");
  const [sortKey, setSortKey] = useState<"composite_score" | "rs_percentile">("composite_score");
  const prices = useStore((s) => s.prices);
  const updatePrice = useStore((s) => s.updatePrice);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await api.getIntradayFuturesWatchlist();
        if (cancelled) return;
        setItems(data);
        if (data.length > 0) {
          const fyers = data.map((d) => `NSE:${d.symbol}-EQ`);
          const priceData = await api.fetchBatchPrices(fyers);
          for (const [sym, pd] of Object.entries(priceData)) updatePrice(sym, pd as never);
        }
      } catch { /* silent */ }
      finally { if (!cancelled) setLoading(false); }
    }
    load();
    const id = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [updatePrice, refreshKey]);

  const filtered = filterKey === "pinned" ? items.filter((i) => i.manual)
    : filterKey === "screened" ? items.filter((i) => !i.manual)
    : items;
  const sorted = [...filtered].sort((a, b) =>
    sortKey === "rs_percentile" ? b.factors.rs_percentile - a.factors.rs_percentile : b.composite_score - a.composite_score
  );

  return (
    <div className="p-3">
      <div className="sticky top-0 z-10 -mx-3 px-3 pb-2 mb-2 bg-bg-primary border-b border-border flex items-center gap-1.5">
        {(["all", "screened", "pinned"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setFilterKey(k)}
            className={`text-[12px] font-mono px-2.5 py-1 rounded border capitalize ${
              filterKey === k ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"
            }`}
          >{k}</button>
        ))}
        <button
          onClick={() => setSortKey((s) => (s === "composite_score" ? "rs_percentile" : "composite_score"))}
          className="ml-auto text-[12px] font-mono px-2.5 py-1 rounded border bg-bg-tertiary text-text-secondary border-border"
        >Sort: {sortKey === "composite_score" ? "Score" : "RS"}</button>
      </div>

      {loading ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">loading…</div>
      ) : sorted.length === 0 ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">{items.length === 0 ? "no watchlist — run screener" : "no results"}</div>
      ) : (
        sorted.map((item) => <WatchlistCard key={item.symbol} item={item} prices={prices} />)
      )}
    </div>
  );
}
