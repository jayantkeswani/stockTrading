"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import {
  startOfDayIST,
  endOfDayIST,
  startOfWeekIST,
  subDaysIST,
  subMonthsIST,
  isoDateIST,
  formatDateShort,
  toISTDate,
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
      return { start: startOfDayIST(subDaysIST(now, 30)), end: endOfDayIST(now), label: "Last 30D" };
    case "3m":
      return {
        start: startOfDayIST(subMonthsIST(now, 3)),
        end: endOfDayIST(now),
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

const NON_CUSTOM_PRESETS: { key: Preset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "week", label: "Week" },
  { key: "month", label: "30D" },
  { key: "3m", label: "3M" },
];

function labelToPreset(label: string): Preset {
  if (label === "Today") return "today";
  if (label === "This Week") return "week";
  if (label === "Last 30D") return "month";
  if (label === "Last 3M") return "3m";
  if (label === "Custom") return "custom";
  return "week";
}

export function PeriodFilter({ value, onChange }: Props) {
  const [active, setActive] = useState<Preset>(() => labelToPreset(value.label));
  const [customFrom, setCustomFrom] = useState(isoDateIST(value.start));
  const [customTo, setCustomTo] = useState(isoDateIST(value.end));
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const closeDropdown = useCallback(() => setDropdownOpen(false), []);

  useEffect(() => {
    if (!dropdownOpen) return;
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        closeDropdown();
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [dropdownOpen, closeDropdown]);

  function select(preset: Preset) {
    setActive(preset);
    setDropdownOpen(false);
    if (preset !== "custom") {
      onChange(buildPreset(preset));
    }
  }

  function toggleCustom() {
    if (active === "custom") {
      setDropdownOpen((prev) => !prev);
    } else {
      setActive("custom");
      setDropdownOpen(true);
    }
  }

  function applyCustom() {
    if (!customFrom || !customTo) return;
    const start = startOfDayIST(new Date(customFrom + "T00:00:00"));
    const end = endOfDayIST(new Date(customTo + "T00:00:00"));
    if (start > end) return;
    onChange({ start, end, label: "Custom" });
    setDropdownOpen(false);
  }

  const customRangeLabel = active === "custom"
    ? `${formatDateShort(toISTDate(value.start))} — ${formatDateShort(toISTDate(value.end))}`
    : null;

  return (
    <div className="flex items-center gap-1.5">
      {NON_CUSTOM_PRESETS.map(({ key, label }) => (
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

      <div className="relative" ref={dropdownRef}>
        <button
          onClick={toggleCustom}
          className={`
            inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium
            transition-all duration-150 border
            ${active === "custom"
              ? "bg-accent/20 text-accent border-accent/30"
              : "text-text-muted border-transparent hover:text-text-secondary hover:bg-bg-tertiary"
            }
          `}
        >
          Custom
        </button>

        {active === "custom" && customRangeLabel && (
          <span className="ml-1.5 text-[10px] font-mono text-text-muted">{customRangeLabel}</span>
        )}

        {dropdownOpen && (
          <div className="absolute top-full left-0 mt-1.5 z-50 bg-bg-elevated border border-border rounded shadow-lg shadow-black/40 p-2.5 flex items-center gap-1.5">
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
    </div>
  );
}
