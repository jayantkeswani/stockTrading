"use client";

import { useEffect, useState, useCallback } from "react";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/lib/api";
import type { MarketStatus } from "@/lib/types";
import { AgentPopup } from "./AgentPopup";
import { TasksPopup } from "./TasksPopup";

function BiasIndicator() {
  const intradayBias = useStore((s) => s.intradayBias);
  if (!intradayBias) return null;

  const colorClass =
    intradayBias.bias === "BULLISH"
      ? "text-profit"
      : intradayBias.bias === "BEARISH"
      ? "text-loss"
      : "text-text-muted";

  return (
    <span className="text-[10px] font-mono flex items-center gap-1">
      <span className={colorClass}>
        {intradayBias.bias} {intradayBias.strength}
      </span>
    </span>
  );
}

export function Header() {
  const { wsConnected, marketStatus, setMarketStatus, setAgentStatus, updatePrice, setIntradayBias } = useStore(useShallow((s) => ({
    wsConnected: s.wsConnected,
    marketStatus: s.marketStatus,
    setMarketStatus: s.setMarketStatus,
    setAgentStatus: s.setAgentStatus,
    updatePrice: s.updatePrice,
    setIntradayBias: s.setIntradayBias,
  })));
  const [time, setTime] = useState("");
  const [deployedVersion, setDeployedVersion] = useState("");
  const [dataFeedReady, setDataFeedReady] = useState(true);

  // Clock
  useEffect(() => {
    const update = () => {
      setTime(
        new Date().toLocaleTimeString("en-IN", {
          timeZone: "Asia/Kolkata",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        })
      );
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, []);

  // Fetch deployed version + readiness on mount; poll until ready
  useEffect(() => {
    let stopped = false;
    async function check() {
      try {
        const h = await api.health();
        setDeployedVersion(h?.version || "");
        setDataFeedReady(h?.data_feed_ready ?? true);
        if (!h?.data_feed_ready && !stopped) {
          setTimeout(check, 3000);
        }
      } catch { /* API not running */ }
    }
    check();
    return () => { stopped = true; };
  }, []);

  // Poll market status + prices every 10s
  useEffect(() => {
    async function fetchStatus() {
      try {
        const [market, agent, prices] = await Promise.all([
          api.getMarketStatus() as Promise<MarketStatus>,
          api.getAgentStatus(),
          api.getAllPrices(),
        ]);
        setMarketStatus(market);
        setAgentStatus(agent as never);
        if (prices) {
          for (const [symbol, data] of Object.entries(prices)) {
            updatePrice(symbol, data as never);
          }
        }
      } catch {
        // API not running
      }
    }
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, [setMarketStatus, setAgentStatus, updatePrice]);

  // Hydrate the NIFTY intraday bias from the cache once on mount (the persisted
  // value covers refreshes; this covers a cold load and keeps it shown after
  // close). The WebSocket `market:bias_update` keeps it live during the session.
  useEffect(() => {
    let stopped = false;
    api.getIntradayBias().then((b) => {
      if (!stopped && b) setIntradayBias(b);
    }).catch(() => { /* API not running */ });
    return () => { stopped = true; };
  }, [setIntradayBias]);

  const handleStartFeed = useCallback(async () => {
    try {
      await api.startDataFeed();
      setTimeout(async () => {
        const status = await api.getMarketStatus() as MarketStatus;
        setMarketStatus(status);
      }, 2000);
    } catch (err) {
      console.error("Failed to start data feed:", err);
    }
  }, [setMarketStatus]);

  return (
    <header className="fixed top-0 left-12 right-0 z-30 h-9 border-b border-border bg-bg-secondary/90 backdrop-blur-sm flex items-center justify-between px-3">
      <div className="flex items-center gap-3">
        <span className="text-text-muted font-mono text-xs">{time}</span>
        {marketStatus && (
          <span
            className={`text-xs font-mono font-medium ${
              marketStatus.is_open ? "text-profit" : "text-text-muted"
            }`}
          >
            {marketStatus.is_open ? "MKT OPEN" : "MKT CLOSED"}
          </span>
        )}
        {marketStatus?.in_dead_zone && (
          <span className="text-xs font-mono text-warning">DEAD ZONE</span>
        )}
        <BiasIndicator />
        {!dataFeedReady && (
          <span className="text-xs font-mono text-warning animate-pulse">STARTING…</span>
        )}
      </div>

      <div className="flex items-center gap-3">
        {/* VIX */}
        {marketStatus?.india_vix != null && (
          <span className="text-xs font-mono">
            <span className="text-text-muted">VIX</span>{" "}
            <span className="text-text-secondary">{marketStatus.india_vix.toFixed(2)}</span>
          </span>
        )}

        {/* Divider */}
        <div className="w-px h-3 bg-border" />

        {/* Background Tasks */}
        <TasksPopup />

        {/* Fyers Data Feed */}
        <div className="flex items-center gap-1">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              marketStatus?.fyers_connected ? "bg-profit" : "bg-warning"
            }`}
          />
          {marketStatus?.fyers_connected ? (
            <span className="text-xs font-mono text-text-muted">FYERS</span>
          ) : (
            <button
              onClick={handleStartFeed}
              className="text-xs font-mono text-warning hover:text-warning/80 transition-colors"
            >
              CONNECT
            </button>
          )}
        </div>

        {/* Agent Status */}
        <AgentPopup />

        {/* WebSocket Connection */}
        <div className="flex items-center gap-1">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              wsConnected ? "bg-profit" : "bg-loss"
            }`}
          />
          <span className="text-xs font-mono text-text-muted">
            {wsConnected ? "WS" : "WS OFF"}
          </span>
        </div>

        {/* Deployed Version */}
        {deployedVersion && deployedVersion !== "dev" && (
          <span className="text-[10px] font-mono text-text-muted/60">{deployedVersion}</span>
        )}
      </div>
    </header>
  );
}
