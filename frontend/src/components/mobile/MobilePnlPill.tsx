"use client";

import { useMemo, useState } from "react";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { formatINR, pnlColor } from "@/lib/formatters";
import { livePositionPnl } from "@/lib/positionPnl";

/**
 * Header day-P&L pill, scoped to the currently selected book (Manual / YOLO
 * profile / Shadow via persisted `dashboardViewMode`). Tapping opens a bottom
 * drawer to switch the book — the selection persists across reloads and is
 * shared with the Positions tab. Computes realized (closed-today) + live
 * unrealized P&L exactly like the desktop PnLCard. Isolated component so
 * price-tick re-renders stay local to the pill. Used by: MobileShell.
 */
export function MobilePnlPill() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const prices = useStore((s) => s.prices);
  const setDashboardViewMode = useStore((s) => s.setDashboardViewMode);
  const {
    risk, positions, closedToday, dashboardViewMode,
    shadowPositions, shadowClosedToday, positionsMinConfidence, yoloProfiles,
  } = useStore(useShallow((s) => ({
    risk: s.risk,
    positions: s.positions,
    closedToday: s.closedToday,
    dashboardViewMode: s.dashboardViewMode,
    shadowPositions: s.shadowPositions,
    shadowClosedToday: s.shadowClosedToday,
    positionsMinConfidence: s.positionsMinConfidence,
    yoloProfiles: s.yoloProfiles,
  })));

  const activeProfiles = (Array.isArray(yoloProfiles) ? yoloProfiles : [])
    .filter((p) => p.is_active).sort((a, b) => a.sort_order - b.sort_order);

  const effectiveMode = useMemo(() => {
    if (dashboardViewMode === "SHADOW") return "SHADOW";
    if (dashboardViewMode === "MANUAL") return "MANUAL";
    if ((Array.isArray(yoloProfiles) ? yoloProfiles : []).some((p) => p.id === dashboardViewMode)) return dashboardViewMode;
    const first = (Array.isArray(yoloProfiles) ? yoloProfiles : []).filter((p) => p.is_active).sort((a, b) => a.sort_order - b.sort_order)[0];
    return first ? first.id : "MANUAL";
  }, [dashboardViewMode, yoloProfiles]);

  const isShadow = effectiveMode === "SHADOW";
  const isManual = effectiveMode === "MANUAL";
  const isKnownProfile = (Array.isArray(yoloProfiles) ? yoloProfiles : []).some((p) => p.id === effectiveMode);

  const rawPositions = isShadow ? shadowPositions
    : isManual ? positions.filter((p) => !p.yolo_profile_id)
    : positions.filter((p) => p.yolo_profile_id === effectiveMode);
  const activePositions = positionsMinConfidence > 0
    ? rawPositions.filter((p) => p.signal_confidence != null && Number(p.signal_confidence) >= positionsMinConfidence)
    : rawPositions;

  const liveUnrealized = useMemo(
    () => activePositions.reduce((total, pos) => total + livePositionPnl(pos, prices).pnl, 0),
    [activePositions, prices],
  );

  const filteredClosed = isShadow ? shadowClosedToday
    : isManual ? closedToday.filter((t) => t.source === "MANUAL")
    : closedToday.filter((t) => t.yolo_profile_id === effectiveMode);
  const closedFiltered = positionsMinConfidence > 0
    ? filteredClosed.filter((t) => t.signal_confidence != null && Number(t.signal_confidence) >= positionsMinConfidence)
    : filteredClosed;

  const closedPnl = (!isShadow && !isManual && !isKnownProfile)
    ? Number(risk?.closed_pnl ?? 0)
    : closedFiltered.reduce((sum, t) => sum + Number(t.pnl ?? 0), 0);

  const pnl = closedPnl + liveUnrealized;
  const label = isShadow ? "Shadow" : isManual ? "Manual"
    : (Array.isArray(yoloProfiles) ? yoloProfiles : []).find((p) => p.id === effectiveMode)?.name ?? "";

  const options: { key: string; label: string }[] = [
    { key: "MANUAL", label: "Manual" },
    ...activeProfiles.map((p) => ({ key: p.id, label: p.name })),
    { key: "SHADOW", label: "Shadow" },
  ];

  const select = (key: string) => { setDashboardViewMode(key); setDrawerOpen(false); };

  return (
    <>
      <button
        onClick={() => setDrawerOpen(true)}
        className={`text-[13px] font-mono px-2 py-1 rounded border border-border bg-bg-tertiary flex items-center gap-1 ${pnlColor(pnl)}`}
      >
        {label && <span className="text-text-muted">{label}</span>}
        <span>{pnl >= 0 ? "+" : ""}{formatINR(pnl)}</span>
        <span className="text-text-muted text-[10px]">▾</span>
      </button>

      {drawerOpen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end" onClick={() => setDrawerOpen(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="relative bg-bg-secondary border-t border-border rounded-t-2xl px-4 pt-3 pb-[calc(env(safe-area-inset-bottom)+12px)] animate-fade-in"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-border" />
            <p className="text-[11px] font-mono text-text-muted uppercase tracking-wider mb-2">Day P&L — select book</p>
            <div className="flex flex-col gap-1">
              {options.map((o) => {
                const selected = o.key === (isShadow ? "SHADOW" : isManual ? "MANUAL" : effectiveMode);
                return (
                  <button
                    key={o.key}
                    onClick={() => select(o.key)}
                    className={`flex items-center justify-between px-3 py-2.5 rounded-lg border text-[15px] font-mono ${
                      selected ? "border-accent/40 bg-accent/10 text-accent" : "border-border bg-bg-tertiary text-text-secondary"
                    }`}
                  >
                    <span>{o.label}</span>
                    {selected && <span className="text-[11px]">●</span>}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
