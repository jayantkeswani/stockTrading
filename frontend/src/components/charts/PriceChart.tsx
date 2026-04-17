"use client";

import { useEffect, useRef, useState, useCallback } from "react";
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

type Timeframe = "1m" | "5m" | "15m" | "1h" | "1D";

// Maps UI timeframe → Fyers resolution param + days of history
const TIMEFRAME_CONFIG: Record<Timeframe, { resolution: string; days: number }> = {
  "1m": { resolution: "1", days: 5 },
  "5m": { resolution: "5", days: 15 },
  "15m": { resolution: "15", days: 30 },
  "1h": { resolution: "60", days: 90 },
  "1D": { resolution: "D", days: 365 },
};

// Seconds per candle for each timeframe
const BUCKET_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "1D": 86400,
};

const IST_OFFSET = 19800; // 5h 30m in seconds

/** Compute the start-of-bucket UTC timestamp for the current moment. */
function currentBucketTime(tf: Timeframe): number {
  const nowUtc = Math.floor(Date.now() / 1000);
  const bucket = BUCKET_SECONDS[tf];
  // Align to IST then convert back to UTC epoch
  const nowIst = nowUtc + IST_OFFSET;
  const bucketIst = Math.floor(nowIst / bucket) * bucket;
  return bucketIst - IST_OFFSET;
}

interface PriceChartProps {
  fullHeight?: boolean;
}

export function PriceChart({ fullHeight }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastCandleRef = useRef<CandlestickData<UTCTimestamp> | null>(null);
  const { selectedSymbol, prices } = useStore();
  const [activeTimeframe, setActiveTimeframe] = useState<Timeframe>("5m");
  const [loading, setLoading] = useState(false);

  const handleTimeframeChange = useCallback((tf: Timeframe) => {
    setActiveTimeframe(tf);
  }, []);

  // Create chart and load historical data
  useEffect(() => {
    if (!chartContainerRef.current) return;
    const container = chartContainerRef.current;

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
        timeVisible: activeTimeframe !== "1D",
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

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

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
          color: c.close >= c.open ? "rgba(0, 230, 138, 0.3)" : "rgba(255, 64, 96, 0.3)",
        }));

        candleSeries.setData(candles);
        volumeSeries.setData(volumes);
        lastCandleRef.current = candles[candles.length - 1];
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
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      lastCandleRef.current = null;
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [selectedSymbol, activeTimeframe]);

  // Update chart on live price ticks
  const priceData = prices[selectedSymbol];
  useEffect(() => {
    const series = candleSeriesRef.current;
    const last = lastCandleRef.current;
    if (!series || !last || !priceData?.ltp) return;

    const ltp = priceData.ltp;
    const bucketTime = currentBucketTime(activeTimeframe) as UTCTimestamp;

    if (bucketTime === last.time) {
      // Same candle — update close/high/low
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
      // New candle
      const newCandle: CandlestickData<UTCTimestamp> = {
        time: bucketTime,
        open: ltp,
        high: ltp,
        low: ltp,
        close: ltp,
      };
      series.update(newCandle);
      lastCandleRef.current = newCandle;

      // Also add volume bar for new candle
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
          <h2 className="text-xs font-mono font-medium text-text-primary">{selectedSymbol}</h2>
          <div className="flex gap-0.5">
            {(["1m", "5m", "15m", "1h", "1D"] as Timeframe[]).map((tf) => (
              <button
                key={tf}
                onClick={() => handleTimeframeChange(tf)}
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
