"use client";

import { useEffect, useRef, useCallback } from "react";
import { getWsUrl, SYMBOLS } from "@/lib/constants";
import { useStore } from "@/store";

// Module-level ref so subscribeSymbols() can be called from any component
let sharedWs: WebSocket | null = null;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const {
    setWsConnected,
    updatePrice,
    addPosition,
    updatePosition,
    removePosition,
    addSignal,
    setRisk,
    addAgentLog,
    setAgentStatus,
    startResearchSession,
    updateResearchAgent,
    completeResearch,
    setIntradayBias,
  } = useStore();

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    // Close any lingering connection before creating a new one
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      wsRef.current.onopen = null;
      try { wsRef.current.close(); } catch { /* already closed */ }
    }

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;
    sharedWs = ws;

    ws.onopen = () => {
      if (ws !== wsRef.current) return;
      setWsConnected(true);
      ws.send(
        JSON.stringify({
          event: "subscribe:symbol",
          data: { symbols: [...SYMBOLS] },
        })
      );
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      if (ws !== wsRef.current) return;
      setWsConnected(false);
      sharedWs = null;
      reconnectTimerRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      if (ws !== wsRef.current) return;
      ws.close();
    };
  }, [setWsConnected]);

  const handleMessage = useCallback(
    (msg: { event: string; data: Record<string, unknown> }) => {
      switch (msg.event) {
        case "price:update":
          updatePrice(msg.data.symbol as string, msg.data as never);
          break;
        case "trade:open":
          // Shadow trades don't go to the main positions slice
          if (!msg.data.is_shadow) addPosition(msg.data as never);
          break;
        case "position:update":
          updatePosition(msg.data.position_id as string, {
            current_price: msg.data.current_price as number,
            unrealized_pnl: msg.data.unrealized_pnl as number,
          });
          break;
        case "position:closed":
          removePosition(msg.data.position_id as string);
          break;
        case "signal:new":
          addSignal(msg.data as never);
          break;
        case "signal:updated":
          addSignal(msg.data as never); // addSignal deduplicates by id
          break;
        case "risk:update":
          setRisk(msg.data as never);
          break;
        case "agent:action":
        case "agent:confirmation_request":
          addAgentLog(msg.data as never);
          break;
        case "agent:status":
          setAgentStatus(msg.data as never);
          break;

        // Research events
        case "research:started":
          startResearchSession(
            msg.data.report_id as string,
            msg.data.symbol as string,
            msg.data.agents_total as number
          );
          break;
        case "research:agent_started":
          updateResearchAgent(
            msg.data.report_id as string,
            msg.data.agent_name as string,
            { status: "running", description: msg.data.description as string }
          );
          break;
        case "research:agent_completed":
          updateResearchAgent(
            msg.data.report_id as string,
            msg.data.agent_name as string,
            {
              status: "completed",
              summary: msg.data.summary as string,
              duration: msg.data.duration_seconds as number,
            }
          );
          break;
        case "research:agent_failed":
          updateResearchAgent(
            msg.data.report_id as string,
            msg.data.agent_name as string,
            { status: "failed", error: msg.data.error as string }
          );
          break;
        case "research:synthesis_started":
          // Could show a synthesis indicator
          break;
        case "research:completed":
          completeResearch(msg.data.report_id as string, "completed");
          break;
        case "research:failed":
          completeResearch(msg.data.report_id as string, "failed");
          break;
        case "market:bias_update":
          if (msg.data.symbol === "NIFTY") {
            setIntradayBias(msg.data as never);
          }
          break;
      }
    },
    [
      updatePrice,
      addPosition,
      updatePosition,
      removePosition,
      addSignal,
      setRisk,
      addAgentLog,
      setAgentStatus,
      startResearchSession,
      updateResearchAgent,
      completeResearch,
      setIntradayBias,
    ]
  );

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      sharedWs = null;
    };
  }, [connect]);

  return wsRef;
}

/**
 * Subscribe to additional symbols on the shared WebSocket connection.
 * Call this when custom contracts are added to the watchlist.
 */
export function subscribeSymbols(symbols: string[]) {
  if (sharedWs?.readyState === WebSocket.OPEN && symbols.length > 0) {
    sharedWs.send(
      JSON.stringify({
        event: "subscribe:symbol",
        data: { symbols },
      })
    );
  }
}
