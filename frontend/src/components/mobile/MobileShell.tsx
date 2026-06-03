"use client";

import { useCallback, useEffect, useState } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import type { YoloProfile, RiskDashboard, Position, Trade } from "@/lib/types";
import { MobilePnlPill } from "./MobilePnlPill";
import { MobileSignals } from "./MobileSignals";
import { MobilePositions } from "./MobilePositions";
import { MobileWatchlist } from "./MobileWatchlist";
import { MobileTrades } from "./MobileTrades";
import { MobileChartModal } from "./MobileChartModal";
import { MobileStatusBar } from "./MobileStatusBar";

type Tab = "signals" | "positions" | "watchlist" | "trades";

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "watchlist", label: "Watchlist", icon: "👁" },
  { key: "signals", label: "Signals", icon: "⚡" },
  { key: "positions", label: "Positions", icon: "◈" },
  { key: "trades", label: "Trades", icon: "▤" },
];

/**
 * Mobile dashboard shell: app header with a profile-scoped day-P&L pill +
 * refresh button, the active tab's content, and a fixed bottom tab bar. The
 * active tab is persisted (store `mobileTab`) so it survives a refresh. On
 * mount (and on every refresh) hydrates the store via getYoloProfiles +
 * getRiskDashboard + getPositions + getClosedTradesToday; `refreshKey` is
 * threaded to each tab so the refresh button re-fetches its data too.
 * Rendered by AppShell on small viewports. Used by: AppShell.
 */
export function MobileShell() {
  const tab = useStore((s) => s.mobileTab);
  const setTab = useStore((s) => s.setMobileTab);
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const setYoloProfiles = useStore((s) => s.setYoloProfiles);
  const setRisk = useStore((s) => s.setRisk);
  const setPositions = useStore((s) => s.setPositions);
  const setClosedToday = useStore((s) => s.setClosedToday);
  const dashboardViewMode = useStore((s) => s.dashboardViewMode);
  const setShadowPositions = useStore((s) => s.setShadowPositions);
  const setShadowClosedToday = useStore((s) => s.setShadowClosedToday);
  const mobileTheme = useStore((s) => s.mobileTheme);
  const setMobileTheme = useStore((s) => s.setMobileTheme);

  useEffect(() => {
    // Initial REST hydration + re-hydration on refresh. The store's live slices
    // are otherwise only fed by WebSocket events (which already-open positions
    // don't emit). WS keeps them fresh thereafter. Mirrors the desktop
    // dashboard page's mount fetch. `closedToday` MUST be fetched here (not just
    // when the Positions tab mounts) — the header day-P&L pill reads it for the
    // realized leg, so without this the strip shows open-only until Positions is
    // visited, then jumps when closed trades load.
    api.getYoloProfiles().then((p) => setYoloProfiles(p as YoloProfile[])).catch(() => {});
    api.getRiskDashboard().then((r) => setRisk(r as RiskDashboard)).catch(() => {});
    api.getPositions({}).then((p) => setPositions(p as Position[])).catch(() => {});
    api.getClosedTradesToday().then((t) => setClosedToday(t as Trade[])).catch(() => {});
  }, [setYoloProfiles, setRisk, setPositions, setClosedToday, refreshKey]);

  useEffect(() => {
    // Shadow book is isolated: its open + closed legs are NOT in the regular
    // positions/closedToday fetch above. Hydrate them when Shadow is the selected
    // book so the header pill is complete on any tab (same open-then-close fix as
    // the regular books). MobilePositions keeps them fresh via 30s polling.
    if (dashboardViewMode !== "SHADOW") return;
    Promise.all([
      api.getPositions({ includeShadow: true }) as Promise<Position[]>,
      api.getClosedTradesToday("SHADOW"),
    ])
      .then(([all, closed]) => {
        setShadowPositions((all as Position[]).filter((p) => p.is_shadow));
        setShadowClosedToday(closed as Trade[]);
      })
      .catch(() => {});
  }, [dashboardViewMode, setShadowPositions, setShadowClosedToday, refreshKey]);

  const [chart, setChart] = useState<{ symbol: string; display?: string } | null>(null);
  const openChart = useCallback((symbol: string, display?: string) => setChart({ symbol, display }), []);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    setRefreshKey((k) => k + 1);
    setTimeout(() => setRefreshing(false), 700);
  }, []);

  return (
    <div data-theme={mobileTheme === "light" ? "light" : "dark"} className="fixed inset-0 flex flex-col bg-bg-primary text-text-primary noise-bg">
      {/* Header */}
      <header className="shrink-0 flex items-center justify-between gap-2 px-4 pt-[env(safe-area-inset-top)] border-b border-border bg-bg-secondary">
        <div className="flex items-center py-2.5 min-w-0">
          <span className="text-[15px] font-mono font-bold tracking-wide text-text-primary truncate">
            stock<span className="text-accent">·</span>trading
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <MobilePnlPill />
          <button
            onClick={() => setMobileTheme(mobileTheme === "light" ? "dark" : "light")}
            aria-label="Toggle theme"
            className="text-text-secondary hover:text-accent p-1.5 rounded border border-border bg-bg-tertiary"
          >
            <span className="block text-[15px] leading-none">{mobileTheme === "light" ? "☾" : "☀"}</span>
          </button>
          <button
            onClick={handleRefresh}
            aria-label="Refresh"
            className="text-text-secondary hover:text-accent p-1.5 rounded border border-border bg-bg-tertiary"
          >
            <span className={`block text-[15px] leading-none ${refreshing ? "animate-spin" : ""}`}>↻</span>
          </button>
        </div>
      </header>

      {/* Market status + intraday bias strip (phone equivalent of the desktop Header bar) */}
      <MobileStatusBar />

      {/* Content */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden">
        {tab === "signals" && <MobileSignals refreshKey={refreshKey} />}
        {tab === "positions" && <MobilePositions refreshKey={refreshKey} />}
        {tab === "watchlist" && <MobileWatchlist refreshKey={refreshKey} onOpenChart={openChart} />}
        {tab === "trades" && <MobileTrades refreshKey={refreshKey} />}
      </main>

      {/* Bottom tab bar */}
      <nav className="shrink-0 flex border-t border-border bg-bg-secondary px-1.5 pt-1 pb-[calc(env(safe-area-inset-bottom)+0.25rem)]">
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              aria-current={active ? "page" : undefined}
              className="flex-1 flex flex-col items-center py-1"
            >
              <span
                className={`flex flex-col items-center gap-0.5 px-3 py-1 rounded-lg transition-colors ${
                  active ? "bg-accent/10 text-accent" : "text-text-muted"
                }`}
              >
                <span className="text-lg leading-none">{t.icon}</span>
                <span className={`text-[10px] font-mono tracking-wide ${active ? "font-semibold" : ""}`}>{t.label}</span>
              </span>
            </button>
          );
        })}
      </nav>

      {chart && (
        <MobileChartModal symbol={chart.symbol} display={chart.display} onClose={() => setChart(null)} />
      )}
    </div>
  );
}
