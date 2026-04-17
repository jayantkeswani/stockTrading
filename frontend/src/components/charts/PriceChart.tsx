"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, CandlestickSeries, HistogramSeries, ColorType, type UTCTimestamp } from "lightweight-charts";
import { useStore } from "@/store";
import { api } from "@/lib/api";

type Timeframe = "1m" | "5m" | "15m" | "1h" | "1D";

// Maps UI timeframe → Fyers resolution param + days of history
const TIMEFRAME_CONFIG: Record<Timeframe, { resolution: string; days: number }> = {
  "1m": { resolution: "1", days: 5 },
  "5m": { resolution: "5", days: 15 },
  "15m": { resolution: "15", days: 30 },
  "1h": { resolution: "60", days: 90 },
  "1D": { resolution: "D", days: 365 },
};

interface PriceChartProps {
  fullHeight?: boolean;
}

export function PriceChart({ fullHeight }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { selectedSymbol } = useStore();
  const [activeTimeframe, setActiveTimeframe] = useState<Timeframe>("5m");
  const [loading, setLoading] = useState(false);

  const handleTimeframeChange = useCallback((tf: Timeframe) => {
    setActiveTimeframe(tf);
  }, []);

  useEffect(() => {
    if (!chartContainerRef.current) return;
    const container = chartContainerRef.current;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "#111118" },
        textColor: "#8888a0",
        fontFamily: "var(--font-geist-mono), monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e1e2e" },
        horzLines: { color: "#1e1e2e" },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: "#6366f1", width: 1, style: 2, labelBackgroundColor: "#6366f1" },
        horzLine: { color: "#6366f1", width: 1, style: 2, labelBackgroundColor: "#6366f1" },
      },
      rightPriceScale: {
        borderColor: "#1e1e2e",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: "#1e1e2e",
        timeVisible: activeTimeframe !== "1D",
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#00ff88",
      downColor: "#ff3366",
      borderDownColor: "#ff3366",
      borderUpColor: "#00ff88",
      wickDownColor: "#ff3366",
      wickUpColor: "#00ff88",
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: "#6366f1",
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    // Fetch pre-aggregated candles from backend (proxied from Fyers)
    let cancelled = false;
    setLoading(true);

    const config = TIMEFRAME_CONFIG[activeTimeframe];
    api
      .getOHLCV(selectedSymbol, { resolution: config.resolution, days: config.days })
      .then((raw) => {
        if (cancelled || !raw || raw.length === 0) {
          setLoading(false);
          return;
        }

        const candles = raw.map((c) => ({
          time: c.timestamp as UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }));

        const volumes = raw.map((c) => ({
          time: c.timestamp as UTCTimestamp,
          value: c.volume,
          color: c.close >= c.open ? "rgba(0, 255, 136, 0.3)" : "rgba(255, 51, 102, 0.3)",
        }));

        candleSeries.setData(candles);
        volumeSeries.setData(volumes);
        setLoading(false);
        chart.timeScale().fitContent();
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    const handleResize = () => {
      chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight,
      });
    };

    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      cancelled = true;
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [selectedSymbol, activeTimeframe]);

  return (
    <div className={`rounded-lg border border-border bg-bg-secondary overflow-hidden flex flex-col ${fullHeight ? "h-full" : ""}`}>
      <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-text-primary">{selectedSymbol}</h2>
          <div className="flex gap-1">
            {(["1m", "5m", "15m", "1h", "1D"] as Timeframe[]).map((tf) => (
              <button
                key={tf}
                onClick={() => handleTimeframeChange(tf)}
                className={`px-2 py-0.5 text-xs rounded transition-colors ${
                  activeTimeframe === tf
                    ? "bg-accent/20 text-accent border border-accent/50"
                    : "bg-bg-tertiary text-text-secondary hover:text-text-primary hover:bg-border border border-transparent"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
          {loading && (
            <div className="w-3 h-3 border border-accent/50 border-t-accent rounded-full animate-spin" />
          )}
        </div>
      </div>
      <div
        ref={chartContainerRef}
        className={fullHeight ? "flex-1 min-h-0 w-full" : "w-full h-[400px]"}
      />
    </div>
  );
}
