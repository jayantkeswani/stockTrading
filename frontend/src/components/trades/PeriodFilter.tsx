"use client";

import { useState } from "react";
import {
  startOfDayIST,
  endOfDayIST,
  startOfWeekIST,
  startOfMonthIST,
  endOfMonthIST,
  subMonthsIST,
  isoDateIST,
} from "@/lib/formatters";

export type Period = {
  start: Date;
  end: Date;
  label: string;
};

type Preset = "today" | "week" | "month" | "3m" | "custom";

interface Props {
  value: Period;
  onChange: (p: Period) => void;
}

function buildPreset(preset: Preset, customStart?: Date, customEnd?: Date): Period {
  const now = new Date();
  switch (preset) {
    case "today":
      return { start: startOfDayIST(now), end: endOfDayIST(now), label: "Today" };
    case "week":
      return { start: startOfWeekIST(now), end: endOfDayIST(now), label: "This Week" };
    case "month":
      return { start: startOfMonthIST(now), end: endOfMonthIST(now), label: "This Month" };
    case "3m":
      return {
        start: startOfMonthIST(subMonthsIST(now, 2)),
        end: endOfMonthIST(now),
        label: "Last 3M",
      };
    case "custom":
      return {
        start: customStart ?? startOfDayIST(now),
        end: customEnd ?? endOfDayIST(now),
        label: "Custom",
      };
  }
}

const PRESETS: { key: Preset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "3m", label: "3M" },
  { key: "custom", label: "Custom" },
];

export function PeriodFilter({ value, onChange }: Props) {
  const [active, setActive] = useState<Preset>("month");
  const [customFrom, setCustomFrom] = useState(isoDateIST(value.start));
  const [customTo, setCustomTo] = useState(isoDateIST(value.end));

  function select(preset: Preset) {
    setActive(preset);
    if (preset !== "custom") {
      onChange(buildPreset(preset));
    }
  }

  function applyCustom() {
    if (!customFrom || !customTo) return;
    const start = startOfDayIST(new Date(customFrom + "T00:00:00"));
    const end = endOfDayIST(new Date(customTo + "T00:00:00"));
    if (start > end) return;
    onChange({ start, end, label: "Custom" });
  }

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {PRESETS.map(({ key, label }) => (
        <button
          key={key}
          onClick={() => select(key)}
          className={`
            inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium
            transition-all duration-150 border
            ${active === key
              ? "bg-accent/20 text-accent border-accent/30"
              : "text-text-muted border-transparent hover:text-text-secondary hover:bg-bg-tertiary"
            }
          `}
        >
          {label}
        </button>
      ))}

      {active === "custom" && (
        <div className="flex items-center gap-1.5 ml-1">
          <input
            type="date"
            value={customFrom}
            onChange={(e) => setCustomFrom(e.target.value)}
            className="bg-bg-secondary border border-border rounded px-1.5 py-0.5 text-xs font-mono text-text-primary focus:outline-none focus:border-accent/50"
          />
          <span className="text-text-muted text-xs font-mono">—</span>
          <input
            type="date"
            value={customTo}
            onChange={(e) => setCustomTo(e.target.value)}
            className="bg-bg-secondary border border-border rounded px-1.5 py-0.5 text-xs font-mono text-text-primary focus:outline-none focus:border-accent/50"
          />
          <button
            onClick={applyCustom}
            className="px-2 py-0.5 rounded text-xs font-mono font-medium bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 transition-colors"
          >
            Apply
          </button>
        </div>
      )}
    </div>
  );
}
