"use client";

import { useCallback, useEffect, useState } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import type { YoloProfile, RiskDashboard, Position } from "@/lib/types";
import { MobilePnlPill } from "./MobilePnlPill";
import { MobileSignals } from "./MobileSignals";
import { MobilePositions } from "./MobilePositions";
import { MobileWatchlist } from "./MobileWatchlist";
import { MobileTrades } from "./MobileTrades";

type Tab = "signals" | "positions" | "watchlist" | "trades";

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "signals", label: "Signals", icon: "⚡" },
  { key: "positions", label: "Positions", icon: "◈" },
  { key: "watchlist", label: "Watchlist", icon: "👁" },
  { key: "trades", label: "Trades", icon: "▤" },
];

/**
 * Mobile dashboard shell: app header with a profile-scoped day-P&L pill +
 * refresh button, the active tab's content, and a fixed bottom tab bar. Tab
 * selection is internal state (one URL). On mount (and on every refresh)
 * hydrates the store via getYoloProfiles + getRiskDashboard + getPositions;
 * `refreshKey` is threaded to each tab so the refresh button re-fetches its
 * data too. Rendered by AppShell on small viewports. Used by: AppShell.
 */
export function MobileShell() {
  const [tab, setTab] = useState<Tab>("signals");
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const setYoloProfiles = useStore((s) => s.setYoloProfiles);
  const setRisk = useStore((s) => s.setRisk);
  const setPositions = useStore((s) => s.setPositions);
  const mobileTheme = useStore((s) => s.mobileTheme);
  const setMobileTheme = useStore((s) => s.setMobileTheme);

  useEffect(() => {
    // Initial REST hydration + re-hydration on refresh. The store's live slices
    // are otherwise only fed by WebSocket events (which already-open positions
    // don't emit). WS keeps them fresh thereafter. Mirrors the desktop
    // dashboard page's mount fetch.
    api.getYoloProfiles().then((p) => setYoloProfiles(p as YoloProfile[])).catch(() => {});
    api.getRiskDashboard().then((r) => setRisk(r as RiskDashboard)).catch(() => {});
    api.getPositions({}).then((p) => setPositions(p as Position[])).catch(() => {});
  }, [setYoloProfiles, setRisk, setPositions, refreshKey]);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    setRefreshKey((k) => k + 1);
    setTimeout(() => setRefreshing(false), 700);
  }, []);

  return (
    <div data-theme={mobileTheme === "light" ? "light" : "dark"} className="fixed inset-0 flex flex-col bg-bg-primary text-text-primary noise-bg">
      {/* Header */}
      <header className="shrink-0 flex items-center justify-between gap-2 px-4 pt-[env(safe-area-inset-top)] border-b border-border bg-bg-secondary">
        <div className="flex items-center py-2.5">
          <span className="text-[15px] font-mono font-bold tracking-wide text-text-primary">
            stock<span className="text-accent">·</span>trading
          </span>
        </div>
        <div className="flex items-center gap-2">
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

      {/* Content */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden">
        {tab === "signals" && <MobileSignals refreshKey={refreshKey} />}
        {tab === "positions" && <MobilePositions refreshKey={refreshKey} />}
        {tab === "watchlist" && <MobileWatchlist refreshKey={refreshKey} />}
        {tab === "trades" && <MobileTrades refreshKey={refreshKey} />}
      </main>

      {/* Bottom tab bar */}
      <nav className="shrink-0 flex border-t border-border bg-bg-secondary pb-[env(safe-area-inset-bottom)]">
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 flex flex-col items-center gap-0.5 py-2 transition-colors ${
                active ? "text-accent" : "text-text-muted"
              }`}
            >
              <span className="text-lg leading-none">{t.icon}</span>
              <span className="text-[10px] font-mono tracking-wide">{t.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
