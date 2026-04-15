"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, CandlestickSeries, HistogramSeries, ColorType, type UTCTimestamp } from "lightweight-charts";
import { useStore } from "@/store";

type Timeframe = "1m" | "5m" | "15m" | "1h";

const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
};

interface PriceChartProps {
  fullHeight?: boolean;
}

export function PriceChart({ fullHeight }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { selectedSymbol } = useStore();
  const [activeTimeframe, setActiveTimeframe] = useState<Timeframe>("5m");

  const handleTimeframeChange = useCallback((tf: Timeframe) => {
    setActiveTimeframe(tf);
  }, []);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
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
        timeVisible: true,
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

    // Generate data based on selected timeframe
    const intervalSeconds = TIMEFRAME_SECONDS[activeTimeframe];
    const demoData = generateDemoData(intervalSeconds);
    candleSeries.setData(demoData.candles);
    volumeSeries.setData(demoData.volumes);

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener("resize", handleResize);
    handleResize();
    chart.timeScale().fitContent();

    return () => {
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
            {(["1m", "5m", "15m", "1h"] as Timeframe[]).map((tf) => (
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
        </div>
      </div>
      <div
        ref={chartContainerRef}
        className={fullHeight ? "flex-1 min-h-0 w-full" : "w-full h-[400px]"}
      />
    </div>
  );
}

function generateDemoData(intervalSeconds: number) {
  const candles: { time: UTCTimestamp; open: number; high: number; low: number; close: number }[] = [];
  const volumes: { time: UTCTimestamp; value: number; color: string }[] = [];
  let price = 24800;

  const barCount = Math.min(400, Math.floor(86400 / intervalSeconds));
  const baseTime = Math.floor(Date.now() / 1000) - barCount * intervalSeconds;

  for (let i = 0; i < barCount; i++) {
    const time = (baseTime + i * intervalSeconds) as UTCTimestamp;
    const volatility = intervalSeconds >= 3600 ? 80 : intervalSeconds >= 900 ? 50 : intervalSeconds >= 300 ? 30 : 15;
    const change = (Math.random() - 0.48) * volatility;
    const open = price;
    const close = price + change;
    const high = Math.max(open, close) + Math.random() * (volatility * 0.6);
    const low = Math.min(open, close) - Math.random() * (volatility * 0.6);
    const volume = Math.floor(Math.random() * 50000) + 10000;

    candles.push({ time, open, high, low, close });
    volumes.push({
      time,
      value: volume,
      color: close >= open ? "rgba(0, 255, 136, 0.3)" : "rgba(255, 51, 102, 0.3)",
    });
    price = close;
  }

  return { candles, volumes };
}
