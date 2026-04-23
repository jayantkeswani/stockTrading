"use client";

import { useMemo } from "react";
import { formatINRCompact, isoDateIST, monthLabel } from "@/lib/formatters";
import type { Period } from "./PeriodFilter";

interface Props {
  period: Period;
  dailyPnL: Map<string, number>;
  selectedDay: string | null;
  onSelectDay: (dateKey: string | null) => void;
}

const DOW_LABELS = ["M", "T", "W", "T", "F", "S", "S"];

function getMonthsInRange(start: Date, end: Date): Date[] {
  const months: Date[] = [];
  let cur = new Date(start.getFullYear(), start.getMonth(), 1);
  const last = new Date(end.getFullYear(), end.getMonth(), 1);
  while (cur <= last) {
    months.push(new Date(cur));
    cur = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
  }
  return months;
}

function mondayDow(d: Date): number {
  const raw = d.getDay();
  return raw === 0 ? 6 : raw - 1;
}

interface MonthGridProps {
  month: Date;
  period: Period;
  dailyPnL: Map<string, number>;
  maxAbs: number;
  todayKey: string;
  selectedDay: string | null;
  onSelectDay: (k: string | null) => void;
}

function MonthGrid({ month, period, dailyPnL, maxAbs, todayKey, selectedDay, onSelectDay }: MonthGridProps) {
  const year = month.getFullYear();
  const mo = month.getMonth();
  const daysInMonth = new Date(year, mo + 1, 0).getDate();

  const firstDow = mondayDow(new Date(year, mo, 1));
  const cells: (number | null)[] = Array(firstDow).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  const weeks: (number | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

  const periodStart = isoDateIST(period.start);
  const periodEnd = isoDateIST(period.end);

  const key = (day: number) =>
    `${year}-${String(mo + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const inPeriod = (d: number | null) =>
    d !== null && key(d) >= periodStart && key(d) <= periodEnd;

  // Trim leading and trailing weeks with no days in the period
  let start = 0;
  let end = weeks.length - 1;
  while (start <= end && !weeks[start].some(inPeriod)) start++;
  while (end >= start && !weeks[end].some(inPeriod)) end--;
  const visibleWeeks = weeks.slice(start, end + 1);

  if (visibleWeeks.length === 0) return null;

  function cellBg(pnl: number): { backgroundColor: string } {
    const intensity = Math.min(Math.abs(pnl) / maxAbs, 1);
    const alpha = 0.15 + intensity * 0.55;
    const rgb = pnl >= 0 ? "0,230,138" : "255,64,96";
    return { backgroundColor: `rgba(${rgb},${alpha.toFixed(2)})` };
  }

  return (
    <div className="flex-1 min-w-[180px]">
      <div className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">
        {monthLabel(month)}
      </div>
      <div className="grid grid-cols-7 gap-px mb-0.5">
        {DOW_LABELS.map((l, i) => (
          <div key={i} className="text-center text-[9px] font-mono text-text-muted/50 pb-1">
            {l}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-px">
        {visibleWeeks.flat().map((day, i) => {
          if (day === null) return <div key={i} className="h-9" />;

          const dk = key(day);
          const ip = dk >= periodStart && dk <= periodEnd;
          const pnl = dailyPnL.get(dk);
          const hasTrade = pnl !== undefined;
          const isToday = dk === todayKey;
          const isSelected = selectedDay === dk;

          return (
            <button
              key={i}
              onClick={() => hasTrade && ip ? onSelectDay(isSelected ? null : dk) : undefined}
              style={hasTrade && ip ? cellBg(pnl!) : undefined}
              className={[
                "relative h-9 flex flex-col items-center justify-center rounded-sm",
                "text-[10px] font-mono transition-all duration-100",
                !ip ? "opacity-20" : "",
                isToday ? "ring-1 ring-inset ring-accent/60" : "",
                isSelected ? "ring-1 ring-inset ring-white/25 brightness-110" : "",
                hasTrade && ip ? "cursor-pointer hover:brightness-[1.15]" : "cursor-default",
                !hasTrade && ip ? "bg-bg-tertiary/20" : "",
              ].filter(Boolean).join(" ")}
            >
              <span className={hasTrade && ip ? "text-white/60 text-[9px]" : "text-text-muted/30 text-[9px]"}>
                {day}
              </span>
              {hasTrade && ip && (
                <span className={`text-[8px] leading-none mt-0.5 ${(pnl ?? 0) >= 0 ? "text-profit/85" : "text-loss/85"}`}>
                  {formatINRCompact(pnl!)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function PnLHeatmap({ period, dailyPnL, selectedDay, onSelectDay }: Props) {
  const months = useMemo(() => getMonthsInRange(period.start, period.end), [period]);
  const maxAbs = useMemo(() => {
    const vals = Array.from(dailyPnL.values());
    return vals.length > 0 ? Math.max(...vals.map(Math.abs)) : 1;
  }, [dailyPnL]);
  const todayKey = isoDateIST(new Date());

  return (
    <div className="rounded border border-border bg-bg-secondary p-3">
      <div className="flex gap-4 flex-wrap">
        {months.map((m) => (
          <MonthGrid
            key={m.toISOString()}
            month={m}
            period={period}
            dailyPnL={dailyPnL}
            maxAbs={maxAbs}
            todayKey={todayKey}
            selectedDay={selectedDay}
            onSelectDay={onSelectDay}
          />
        ))}
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
