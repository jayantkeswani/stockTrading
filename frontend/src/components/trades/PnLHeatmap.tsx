"use client";

import { useMemo } from "react";
import { formatINRCompact, isoDateIST } from "@/lib/formatters";
import type { Period } from "./PeriodFilter";

interface Props {
  period: Period;
  dailyPnL: Map<string, number>;
  selectedDay: string | null;
  onSelectDay: (dateKey: string | null) => void;
}

const WEEKDAY_LABELS = ["M", "T", "W", "T", "F"];
const SHORT_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function mondayDow(d: Date): number {
  return d.getDay() === 0 ? 6 : d.getDay() - 1;
}

function prevMonday(d: Date): Date {
  const r = new Date(d);
  r.setDate(d.getDate() - mondayDow(d));
  return r;
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(d.getDate() + n);
  return r;
}

export function PnLHeatmap({ period, dailyPnL, selectedDay, onSelectDay }: Props) {
  const todayKey = isoDateIST(new Date());
  const periodStart = isoDateIST(period.start);
  const periodEnd = isoDateIST(period.end);

  const { weeks, monthStarts } = useMemo(() => {
    const weeks: (Date | null)[][] = [];
    const monthStarts = new Map<number, string>();

    let cur = prevMonday(period.start);
    const endDate = new Date(period.end);
    let lastMonth = -1;

    while (cur <= endDate) {
      const weekDays: (Date | null)[] = [];
      for (let d = 0; d < 5; d++) {
        const day = addDays(cur, d);
        const dk = isoDateIST(day);
        weekDays.push(dk >= periodStart && dk <= periodEnd ? day : null);
      }

      const firstDay = weekDays.find((d) => d !== null);
      if (firstDay) {
        const m = firstDay.getMonth();
        if (m !== lastMonth) {
          const y = firstDay.getFullYear();
          const label = y !== new Date().getFullYear()
            ? `${SHORT_MONTHS[m]} ${y}`
            : SHORT_MONTHS[m];
          monthStarts.set(weeks.length, label);
          lastMonth = m;
        }
      }

      weeks.push(weekDays);
      cur = addDays(cur, 7);
    }

    return { weeks, monthStarts };
  }, [period, periodStart, periodEnd]);

  const maxAbs = useMemo(() => {
    const vals = Array.from(dailyPnL.values());
    return vals.length > 0 ? Math.max(...vals.map(Math.abs)) : 1;
  }, [dailyPnL]);

  function cellStyle(pnl: number): React.CSSProperties {
    const intensity = Math.min(Math.abs(pnl) / maxAbs, 1);
    const alpha = 0.30 + intensity * 0.65; // 0.30 → 0.95
    const rgb = pnl >= 0 ? "0,230,138" : "255,64,96";
    return { backgroundColor: `rgba(${rgb},${alpha.toFixed(2)})` };
  }

  return (
    <div className="rounded border border-border bg-bg-secondary p-3 overflow-x-auto">
      <div className="flex">
        {/* Day-of-week label rail */}
        <div className="flex flex-col gap-0.5 pt-5 pr-2 flex-shrink-0">
          {WEEKDAY_LABELS.map((l, i) => (
            <div key={i} className="h-9 flex items-center justify-end text-[9px] font-mono text-text-muted/40 w-3">
              {l}
            </div>
          ))}
        </div>

        {/* Week columns */}
        <div className="flex gap-0.5">
          {weeks.map((weekDays, wi) => (
            <div key={wi} className="flex flex-col gap-0.5 flex-shrink-0">
              {/* Month label row */}
              <div className="h-5 flex items-end pb-0.5 overflow-visible">
                {monthStarts.has(wi) && (
                  <span className="text-[9px] font-mono text-text-muted/70 uppercase tracking-wide whitespace-nowrap">
                    {monthStarts.get(wi)}
                  </span>
                )}
              </div>

              {/* Mon–Fri cells */}
              {weekDays.map((day, di) => {
                if (!day) return <div key={di} className="h-9 w-10" />;

                const dk = isoDateIST(day);
                const pnl = dailyPnL.get(dk);
                const hasTrade = pnl !== undefined;
                const isToday = dk === todayKey;
                const isSelected = selectedDay === dk;

                return (
                  <button
                    key={di}
                    onClick={() => hasTrade ? onSelectDay(isSelected ? null : dk) : undefined}
                    style={hasTrade ? cellStyle(pnl!) : undefined}
                    className={[
                      "h-9 w-10 flex flex-col items-center justify-center rounded-sm",
                      "text-[9px] font-mono transition-all duration-100 select-none",
                      hasTrade ? "cursor-pointer hover:brightness-125" : "cursor-default",
                      !hasTrade ? "bg-white/[0.06] border border-white/10" : "",
                      isToday ? "ring-1 ring-inset ring-accent/70" : "",
                      isSelected ? "ring-2 ring-inset ring-white/40 brightness-110" : "",
                    ].filter(Boolean).join(" ")}
                  >
                    <span className={[
                      "text-[9px] leading-none",
                      hasTrade ? "text-white font-semibold" : "text-white/40",
                    ].join(" ")}>
                      {day.getDate()}
                    </span>
                    {hasTrade && (
                      <span className="text-[7.5px] leading-none mt-0.5 text-white/90 font-medium">
                        {formatINRCompact(pnl!)}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {selectedDay && (
        <div className="mt-2 pt-2 border-t border-border/40 flex items-center gap-2">
          <span className="text-[10px] font-mono text-text-muted">Filtering:</span>
          <span className="text-[10px] font-mono text-accent">{selectedDay}</span>
          <button
            onClick={() => onSelectDay(null)}
            className="ml-1 text-[10px] font-mono text-text-muted hover:text-text-secondary underline"
          >
            clear
          </button>
        </div>
      )}
    </div>
  );
}
