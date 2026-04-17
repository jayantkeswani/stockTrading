"use client";

import { useEffect, useState, useCallback } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import type { MarketStatus } from "@/lib/types";
import { AgentPopup } from "./AgentPopup";
import { TasksPopup } from "./TasksPopup";

export function Header() {
  const { wsConnected, marketStatus, setMarketStatus, setAgentStatus, updatePrice } = useStore();
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
      </div>
    </header>
  );
}
