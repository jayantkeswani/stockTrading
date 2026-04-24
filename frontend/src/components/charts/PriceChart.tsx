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
  // Align bucket boundary in IST, then return as UTC so it matches stored timestamps
  return Math.floor(nowIst / bucket) * bucket - IST_OFFSET;
}

function fmtPrice(v: number) {
  return v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtVol(v: number) {
  if (v >= 10_000_000) return `${(v / 10_000_000).toFixed(1)}Cr`;
  if (v >= 100_000) return `${(v / 100_000).toFixed(1)}L`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return `${v}`;
}

function fmtTimeIST(ts: number, tf: Timeframe): string {
  const d = new Date((ts + IST_OFFSET) * 1000);
  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const day = d.getUTCDate().toString().padStart(2, "0");
  const mon = MONTHS[d.getUTCMonth()];
  if (tf === "1D") return `${day} ${mon} ${d.getUTCFullYear()}`;
  const h = d.getUTCHours().toString().padStart(2, "0");
  const m = d.getUTCMinutes().toString().padStart(2, "0");
  return `${day} ${mon} ${h}:${m}`;
}

interface OHLCInfo {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  changePct: number;
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
  const lastVolRef = useRef<number>(0);

  const { selectedSymbol, prices, activeTimeframe, setActiveTimeframe } = useStore();
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [ohlcInfo, setOhlcInfo] = useState<OHLCInfo | null>(null);

  // Effect 1: create/destroy chart on symbol or timeframe change
  useEffect(() => {
    if (!chartContainerRef.current) return;
    const container = chartContainerRef.current;
    const tf = activeTimeframe;

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
      localization: {
        timeFormatter: (time: number) => {
          const d = new Date((time + IST_OFFSET) * 1000);
          const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
          if (tf === "1D") {
            return `${d.getUTCDate().toString().padStart(2,"0")} ${MONTHS[d.getUTCMonth()]} '${String(d.getUTCFullYear()).slice(2)}`;
          }
          return `${d.getUTCHours().toString().padStart(2,"0")}:${d.getUTCMinutes().toString().padStart(2,"0")}`;
        },
      },
      timeScale: {
        borderColor: "#1a1a2a",
        timeVisible: tf !== "1D",
        secondsVisible: false,
        rightOffset: 5, // always show last candle with space to the right
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

    // OHLC legend: update on crosshair move, restore last candle on leave
    chart.subscribeCrosshairMove((param) => {
      const candle = param.seriesData?.get(candleSeries) as CandlestickData<UTCTimestamp> | undefined;
      if (!param.time || !candle) {
        const last = lastCandleRef.current;
        if (last) {
          setOhlcInfo({
            time: fmtTimeIST(last.time, tf),
            open: last.open,
            high: last.high,
            low: last.low,
            close: last.close,
            volume: lastVolRef.current,
            changePct: ((last.close - last.open) / last.open) * 100,
          });
        }
        return;
      }
      const volData = param.seriesData?.get(volumeSeries) as { value: number } | undefined;
      setOhlcInfo({
        time: fmtTimeIST(param.time as number, tf),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: volData?.value ?? 0,
        changePct: ((candle.close - candle.open) / candle.open) * 100,
      });
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    const handleResize = () =>
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      lastCandleRef.current = null;
      lastVolRef.current = 0;
      setOhlcInfo(null);
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

        const lastCandle = candles[candles.length - 1];
        const lastVol = raw[raw.length - 1].volume;
        lastCandleRef.current = lastCandle;
        lastVolRef.current = lastVol;

        setOhlcInfo({
          time: fmtTimeIST(lastCandle.time, activeTimeframe),
          open: lastCandle.open,
          high: lastCandle.high,
          low: lastCandle.low,
          close: lastCandle.close,
          volume: lastVol,
          changePct: ((lastCandle.close - lastCandle.open) / lastCandle.open) * 100,
        });

        setLoading(false);
        // Always fit all data — avoids stale logical range hiding new candles
        chart.timeScale().fitContent();
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
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
      lastVolRef.current = 0;
      volumeSeriesRef.current?.update({
        time: bucketTime,
        value: 0,
        color: "rgba(0, 230, 138, 0.3)",
      });
    }
  }, [priceData?.ltp, activeTimeframe]);

  const isUp = ohlcInfo ? ohlcInfo.close >= ohlcInfo.open : true;

  return (
    <div className={`rounded border border-border bg-bg-secondary overflow-hidden flex flex-col ${fullHeight ? "h-full" : ""}`}>
      {/* Header: symbol, timeframe pills, refresh */}
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

      {/* Chart area */}
      <div className={`relative ${fullHeight ? "flex-1 min-h-0" : ""}`}>
        {/* OHLC legend overlay — top-left, non-interactive */}
        {ohlcInfo && (
          <div className="absolute top-1.5 left-2 z-10 flex items-center gap-2.5 text-[10px] font-mono pointer-events-none select-none leading-none">
            <span className="text-text-muted">{ohlcInfo.time}</span>
            <span className="text-text-muted">O <span className="text-text-secondary">{fmtPrice(ohlcInfo.open)}</span></span>
            <span className="text-profit">H <span>{fmtPrice(ohlcInfo.high)}</span></span>
            <span className="text-loss">L <span>{fmtPrice(ohlcInfo.low)}</span></span>
            <span className="text-text-muted">C{" "}
              <span className={isUp ? "text-profit" : "text-loss"}>{fmtPrice(ohlcInfo.close)}</span>
            </span>
            <span className={isUp ? "text-profit" : "text-loss"}>
              {isUp ? "+" : ""}{ohlcInfo.changePct.toFixed(2)}%
            </span>
            {ohlcInfo.volume > 0 && (
              <span className="text-text-muted">V <span className="text-text-secondary">{fmtVol(ohlcInfo.volume)}</span></span>
            )}
          </div>
        )}
        <div
          ref={chartContainerRef}
          className={fullHeight ? "w-full h-full" : "w-full h-[400px]"}
        />
      </div>
    </div>
  );
}
