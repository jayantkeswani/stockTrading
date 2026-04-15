"use client";

import { useEffect, useRef, useCallback } from "react";
import { WS_URL, SYMBOLS } from "@/lib/constants";
import { useStore } from "@/store";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const {
    setWsConnected,
    updatePrice,
    updatePosition,
    removePosition,
    addSignal,
    setRisk,
    addAgentLog,
    setAgentStatus,
  } = useStore();

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
      // Subscribe to all default symbols
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
      setWsConnected(false);
      // Reconnect after 3 seconds
      reconnectTimerRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [setWsConnected]);

  const handleMessage = useCallback(
    (msg: { event: string; data: Record<string, unknown> }) => {
      switch (msg.event) {
        case "price:update":
          updatePrice(msg.data.symbol as string, msg.data as never);
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
      }
    },
    [
      updatePrice,
      updatePosition,
      removePosition,
      addSignal,
      setRisk,
      addAgentLog,
      setAgentStatus,
    ]
  );

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return wsRef;
}

/**
 * Subscribe to additional symbols on an existing WebSocket connection.
 * Call this when custom contracts are added to the watchlist.
 */
export function subscribeSymbols(
  wsRef: React.RefObject<WebSocket | null>,
  symbols: string[]
) {
  if (wsRef.current?.readyState === WebSocket.OPEN && symbols.length > 0) {
    wsRef.current.send(
      JSON.stringify({
        event: "subscribe:symbol",
        data: { symbols },
      })
    );
  }
}
