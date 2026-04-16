"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { STRATEGY_LABELS } from "@/lib/constants";
import { useStore } from "@/store";

interface StrategyConfig {
  strategy_name: string;
  is_active: boolean;
  auto_mode: boolean;
  symbols: string[];
}

export function ScannerHeader() {
  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [scanning, setScanning] = useState<string | null>(null);
  const { addScanLog } = useStore();

  useEffect(() => {
    async function load() {
      try {
        const data = await api.getStrategies();
        setStrategies(data.filter((s) => s.is_active));
      } catch {
        // API not ready
      }
    }
    load();
    // Refresh every 30s in case settings change
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleScan = async (strategyName: string) => {
    if (scanning) return;
    setScanning(strategyName);

    const scanId = Date.now().toString();
    const label = STRATEGY_LABELS[strategyName] || strategyName;

    addScanLog({
      id: `${scanId}-start`,
      type: "start",
      strategy: label,
      timestamp: new Date().toISOString(),
    });

    try {
      const result = await api.evaluateStrategyBatch(strategyName);
      addScanLog({
        id: `${scanId}-end`,
        type: "end",
        strategy: label,
        timestamp: new Date().toISOString(),
        symbolsScanned: result.symbols_scanned,
        signalsGenerated: result.signals_generated,
      });
    } catch (err) {
      addScanLog({
        id: `${scanId}-end`,
        type: "end",
        strategy: label,
        timestamp: new Date().toISOString(),
        symbolsScanned: 0,
        signalsGenerated: 0,
      });
    } finally {
      setScanning(null);
    }
  };

  if (strategies.length === 0) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-bg-secondary">
      <span className="text-xs text-text-muted font-medium mr-1 shrink-0">Scan</span>
      <div className="flex items-center gap-1.5 flex-wrap">
        {strategies.map((s) => {
          const label = STRATEGY_LABELS[s.strategy_name] || s.strategy_name;
          const isScanning = scanning === s.strategy_name;
          return (
            <button
              key={s.strategy_name}
              onClick={() => handleScan(s.strategy_name)}
              disabled={!!scanning}
              className={`
                inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium
                transition-all duration-150
                ${isScanning
                  ? "bg-accent/30 text-accent border border-accent/40"
                  : "bg-bg-tertiary text-text-secondary border border-border hover:text-text-primary hover:border-accent/40 hover:bg-accent/10"
                }
                disabled:opacity-50 disabled:cursor-not-allowed
              `}
            >
              {isScanning ? (
                <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              )}
              {label}
            </button>
          );
        })}
      </div>
      {scanning && (
        <span className="text-xs text-accent ml-auto animate-pulse">
          Scanning...
        </span>
      )}
    </div>
  );
}
