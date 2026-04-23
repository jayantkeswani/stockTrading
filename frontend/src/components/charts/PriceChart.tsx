"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  ColorType,
  type UTCTimestamp,
  type ISeriesApi,
  type CandlestickData,
} from "lightweight-charts";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import { displaySymbol } from "@/lib/constants";
import type { Timeframe } from "@/lib/constants";

const TIMEFRAME_CONFIG: Record<Timeframe, { resolution: string; days: number }> = {
  "1m": { resolution: "1", days: 5 },
  "5m": { resolution: "5", days: 15 },
  "15m": { resolution: "15", days: 30 },
  "1h": { resolution: "60", days: 90 },
  "1D": { resolution: "D", days: 365 },
};

const BUCKET_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "1D": 86400,
};

const IST_OFFSET = 19800; // 5h 30m in seconds

function currentBucketTime(tf: Timeframe): number {
  const nowUtc = Math.floor(Date.now() / 1000);
  const bucket = BUCKET_SECONDS[tf];
  const nowIst = nowUtc + IST_OFFSET;
  return Math.floor(nowIst / bucket) * bucket - IST_OFFSET;
}

function saveRange(symbol: string, tf: Timeframe, range: { from: number; to: number }) {
  try {
    localStorage.setItem(`chart_range_${symbol}_${tf}`, JSON.stringify(range));
  } catch {}
}

function loadRange(symbol: string, tf: Timeframe): { from: number; to: number } | null {
  try {
    const raw = localStorage.getItem(`chart_range_${symbol}_${tf}`);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

interface PriceChartProps {
  fullHeight?: boolean;
}

export function PriceChart({ fullHeight }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastCandleRef = useRef<CandlestickData<UTCTimestamp> | null>(null);

  const { selectedSymbol, prices, activeTimeframe, setActiveTimeframe } = useStore();
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Effect 1: create/destroy chart on symbol or timeframe change
  useEffect(() => {
    if (!chartContainerRef.current) return;
    const container = chartContainerRef.current;
    const tf = activeTimeframe;
    const sym = selectedSymbol;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "#0b0b13" },
        textColor: "#6a6a82",
        fontFamily: "var(--font-geist-mono), monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1a1a2a" },
        horzLines: { color: "#1a1a2a" },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: "#d4a843", width: 1, style: 2, labelBackgroundColor: "#d4a843" },
        horzLine: { color: "#d4a843", width: 1, style: 2, labelBackgroundColor: "#d4a843" },
      },
      rightPriceScale: {
        borderColor: "#1a1a2a",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: "#1a1a2a",
        timeVisible: tf !== "1D",
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#00e68a",
      downColor: "#ff4060",
      borderDownColor: "#ff4060",
      borderUpColor: "#00e68a",
      wickDownColor: "#ff4060",
      wickUpColor: "#00e68a",
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: "#d4a843",
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    const handleResize = () =>
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) saveRange(sym, tf, range);
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      lastCandleRef.current = null;
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [selectedSymbol, activeTimeframe]);

  // Effect 2: load OHLCV data — also triggered by refreshKey (no chart recreation)
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !volumeSeries || !chart) return;

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
          color: c.close >= c.open ? "rgba(0, 230, 138, 0.3)" : "rgba(255, 64, 96, 0.3)",
        }));

        candleSeries.setData(candles);
        volumeSeries.setData(volumes);
        lastCandleRef.current = candles[candles.length - 1];
        setLoading(false);

        const saved = loadRange(selectedSymbol, activeTimeframe);
        if (saved) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          chart.timeScale().setVisibleLogicalRange(saved as any);
        } else {
          chart.timeScale().fitContent();
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      // Save scroll before a refresh so position is restored after reload
      const range = chartRef.current?.timeScale().getVisibleLogicalRange();
      if (range) saveRange(selectedSymbol, activeTimeframe, range);
    };
  }, [selectedSymbol, activeTimeframe, refreshKey]);

  // Effect 3: live price tick → update current candle
  const priceData = prices[selectedSymbol];
  useEffect(() => {
    const series = candleSeriesRef.current;
    const last = lastCandleRef.current;
    if (!series || !last || !priceData?.ltp) return;

    const ltp = priceData.ltp;
    const bucketTime = currentBucketTime(activeTimeframe) as UTCTimestamp;

    if (bucketTime === last.time) {
      const updated: CandlestickData<UTCTimestamp> = {
        time: last.time,
        open: last.open,
        high: Math.max(last.high, ltp),
        low: Math.min(last.low, ltp),
        close: ltp,
      };
      series.update(updated);
      lastCandleRef.current = updated;
    } else if (bucketTime > last.time) {
      const newCandle: CandlestickData<UTCTimestamp> = {
        time: bucketTime,
        open: ltp,
        high: ltp,
        low: ltp,
        close: ltp,
      };
      series.update(newCandle);
      lastCandleRef.current = newCandle;
      volumeSeriesRef.current?.update({
        time: bucketTime,
        value: 0,
        color: "rgba(0, 230, 138, 0.3)",
      });
    }
  }, [priceData?.ltp, activeTimeframe]);

  return (
    <div className={`rounded border border-border bg-bg-secondary overflow-hidden flex flex-col ${fullHeight ? "h-full" : ""}`}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <h2 className="text-xs font-mono font-medium text-text-primary">{displaySymbol(selectedSymbol)}</h2>
          <div className="flex gap-0.5">
            {(["1m", "5m", "15m", "1h", "1D"] as Timeframe[]).map((tf) => (
              <button
                key={tf}
                onClick={() => setActiveTimeframe(tf)}
                className={`px-1.5 py-0.5 text-xs font-mono rounded transition-colors ${
                  activeTimeframe === tf
                    ? "bg-accent/15 text-accent border border-accent/30"
                    : "text-text-muted hover:text-text-secondary hover:bg-bg-tertiary border border-transparent"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>
        <button
          onClick={() => setRefreshKey((k) => k + 1)}
          disabled={loading}
          title="Refresh chart data"
          className="w-6 h-6 flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-tertiary rounded transition-colors disabled:opacity-40"
        >
          <svg
            className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
        </button>
      </div>
      <div
        ref={chartContainerRef}
        className={fullHeight ? "flex-1 min-h-0 w-full" : "w-full h-[400px]"}
      />
    </div>
  );
}
