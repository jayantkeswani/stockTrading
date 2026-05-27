"use client";

import { useEffect, useRef, useCallback } from "react";
import { getWsUrl } from "@/lib/constants";
import { useStore } from "@/store";

// All store interactions use getState() so AppShell never re-renders from store changes.
export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const heartbeatRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const handleMessage = useCallback(
    (msg: { event: string; data: Record<string, unknown> }) => {
      const s = useStore.getState();
      switch (msg.event) {
        case "price:update":
          s.updatePrice(msg.data.symbol as string, msg.data as never);
          break;
        case "trade:open":
          if (!msg.data.is_shadow) s.addPosition(msg.data as never);
          break;
        case "position:update":
          s.updatePosition(msg.data.position_id as string, {
            current_price: msg.data.current_price as number,
            unrealized_pnl: msg.data.unrealized_pnl as number,
          });
          break;
        case "position:closed":
          s.removePosition(msg.data.position_id as string);
          break;
        case "signal:new":
          s.addSignal(msg.data as never);
          break;
        case "signal:updated":
          s.addSignal(msg.data as never);
          break;
        case "risk:update":
          s.setRisk(msg.data as never);
          break;
        case "agent:action":
        case "agent:confirmation_request":
          s.addAgentLog(msg.data as never);
          break;
        case "agent:status":
          s.setAgentStatus(msg.data as never);
          break;
        case "research:started":
          s.startResearchSession(
            msg.data.report_id as string,
            msg.data.symbol as string,
            msg.data.agents_total as number
          );
          break;
        case "research:agent_started":
          s.updateResearchAgent(
            msg.data.report_id as string,
            msg.data.agent_name as string,
            { status: "running", description: msg.data.description as string }
          );
          break;
        case "research:agent_completed":
          s.updateResearchAgent(
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
          s.updateResearchAgent(
            msg.data.report_id as string,
            msg.data.agent_name as string,
            { status: "failed", error: msg.data.error as string }
          );
          break;
        case "research:synthesis_started":
          break;
        case "research:completed":
          s.completeResearch(msg.data.report_id as string, "completed");
          break;
        case "research:failed":
          s.completeResearch(msg.data.report_id as string, "failed");
          break;
        case "market:bias_update":
          if (msg.data.symbol === "NIFTY") {
            s.setIntradayBias(msg.data as never);
          }
          break;
      }
    },
    []
  );

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      wsRef.current.onopen = null;
      try { wsRef.current.close(); } catch { /* already closed */ }
    }

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      if (ws !== wsRef.current) return;
      useStore.getState().setWsConnected(true);
      clearInterval(heartbeatRef.current);
      heartbeatRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ event: "ping", data: {} }));
        }
      }, 25000);
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
      clearInterval(heartbeatRef.current);
      useStore.getState().setWsConnected(false);
      reconnectTimerRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      if (ws !== wsRef.current) return;
      ws.close();
    };
  }, [handleMessage]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      clearInterval(heartbeatRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return wsRef;
}

