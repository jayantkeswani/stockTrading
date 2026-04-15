"use client";

import { useEffect, useState, useCallback } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import type { MarketStatus } from "@/lib/types";

export function Header() {
  const { wsConnected, marketStatus, setMarketStatus, agentStatus, setAgentStatus, updatePrice } = useStore();
  const [time, setTime] = useState("");

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
        // Load all prices into store
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

  const handleStartFeed = useCallback(async () => {
    try {
      await api.startDataFeed();
      // Refresh status after a moment
      setTimeout(async () => {
        const status = await api.getMarketStatus() as MarketStatus;
        setMarketStatus(status);
      }, 2000);
    } catch (err) {
      console.error("Failed to start data feed:", err);
    }
  }, [setMarketStatus]);

  return (
    <header className="fixed top-0 left-16 right-0 z-30 h-12 border-b border-border bg-bg-secondary/80 backdrop-blur-sm flex items-center justify-between px-4">
      <div className="flex items-center gap-4">
        <span className="text-text-secondary font-mono text-sm">{time} IST</span>
        {marketStatus && (
          <span
            className={`text-xs px-2 py-0.5 rounded ${
              marketStatus.is_open
                ? "bg-profit/20 text-profit"
                : "bg-text-muted/20 text-text-muted"
            }`}
          >
            {marketStatus.is_open ? "MARKET OPEN" : "MARKET CLOSED"}
          </span>
        )}
        {marketStatus?.in_dead_zone && (
          <span className="text-xs px-2 py-0.5 rounded bg-warning/20 text-warning">
            DEAD ZONE
          </span>
        )}
      </div>

      <div className="flex items-center gap-4">
        {/* VIX */}
        {marketStatus?.india_vix != null && (
          <div className="text-xs">
            <span className="text-text-muted">VIX </span>
            <span className="text-text-primary font-mono">
              {marketStatus.india_vix.toFixed(2)}
            </span>
          </div>
        )}

        {/* Fyers Data Feed */}
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2 h-2 rounded-full ${
              marketStatus?.fyers_connected ? "bg-profit" : "bg-warning"
            }`}
          />
          {marketStatus?.fyers_connected ? (
            <span className="text-xs text-text-secondary">Fyers Live</span>
          ) : (
            <button
              onClick={handleStartFeed}
              className="text-xs text-warning hover:text-warning/80 transition-colors"
            >
              Connect Fyers
            </button>
          )}
        </div>

        {/* Agent Status */}
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2 h-2 rounded-full ${
              agentStatus?.running ? "bg-profit animate-pulse" : "bg-text-muted"
            }`}
          />
          <span className="text-xs text-text-secondary">
            {agentStatus?.running
              ? agentStatus.yolo_mode
                ? "YOLO"
                : "Agent On"
              : "Agent Off"}
          </span>
        </div>

        {/* WebSocket Connection */}
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2 h-2 rounded-full ${
              wsConnected ? "bg-profit" : "bg-loss"
            }`}
          />
          <span className="text-xs text-text-secondary">
            {wsConnected ? "WS" : "Disconnected"}
          </span>
        </div>
      </div>
    </header>
  );
}
