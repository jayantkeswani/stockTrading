/**
 * Format a number as Indian Rupees (₹).
 * Uses Indian number system (lakhs, crores).
 */
export function formatINR(value: number): string {
  const num = Number(value) || 0;
  const absValue = Math.abs(num);
  const sign = num < 0 ? "-" : "";
  return `${sign}₹${absValue.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * Format a compact currency value (e.g., ₹1.5L, ₹2.3Cr).
 */
export function formatINRCompact(value: number): string {
  const absValue = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (absValue >= 10_000_000) {
    return `${sign}₹${(absValue / 10_000_000).toFixed(1)}Cr`;
  }
  if (absValue >= 100_000) {
    return `${sign}₹${(absValue / 100_000).toFixed(1)}L`;
  }
  if (absValue >= 1_000) {
    return `${sign}₹${(absValue / 1_000).toFixed(1)}K`;
  }
  return formatINR(value);
}

/**
 * Format a percentage with sign.
 */
export function formatPercent(value: number): string {
  const num = Number(value) || 0;
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(2)}%`;
}

/**
 * Format a timestamp to IST time string.
 */
export function formatTime(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/**
 * Format a date.
 */
export function formatDate(timestamp: string): string {
  return new Date(timestamp).toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

// ── IST date helpers ──────────────────────────────────────────────────────────

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;

export function toISTDate(d: Date): Date {
  const utcMs = d.getTime() + d.getTimezoneOffset() * 60000;
  return new Date(utcMs + IST_OFFSET_MS);
}

function toIST(d: Date): Date {
  return toISTDate(d);
}

function fromIST(y: number, mo: number, day: number, h = 0, m = 0, s = 0, ms = 0): Date {
  const istMs = Date.UTC(y, mo, day, h, m, s, ms) - IST_OFFSET_MS;
  return new Date(istMs);
}

export function startOfDayIST(d: Date): Date {
  const ist = toIST(d);
  return fromIST(ist.getFullYear(), ist.getMonth(), ist.getDate(), 0, 0, 0, 0);
}

export function endOfDayIST(d: Date): Date {
  const ist = toIST(d);
  return fromIST(ist.getFullYear(), ist.getMonth(), ist.getDate(), 23, 59, 59, 999);
}

export function startOfMonthIST(d: Date): Date {
  const ist = toIST(d);
  return fromIST(ist.getFullYear(), ist.getMonth(), 1, 0, 0, 0, 0);
}

export function endOfMonthIST(d: Date): Date {
  const ist = toIST(d);
  return fromIST(ist.getFullYear(), ist.getMonth() + 1, 0, 23, 59, 59, 999);
}

export function startOfWeekIST(d: Date): Date {
  const ist = toIST(d);
  const dow = ist.getDay(); // 0=Sun
  const mon = dow === 0 ? -6 : 1 - dow;
  return fromIST(ist.getFullYear(), ist.getMonth(), ist.getDate() + mon, 0, 0, 0, 0);
}

export function subDaysIST(d: Date, n: number): Date {
  return new Date(d.getTime() - n * 86400000);
}

export function subMonthsIST(d: Date, n: number): Date {
  const ist = toIST(d);
  return fromIST(ist.getFullYear(), ist.getMonth() - n, ist.getDate(), 0, 0, 0, 0);
}

export function eachDayInRange(start: Date, end: Date): Date[] {
  const days: Date[] = [];
  let cur = startOfDayIST(start);
  const last = startOfDayIST(end);
  while (cur <= last) {
    days.push(cur);
    cur = new Date(cur.getTime() + 86400000);
  }
  return days;
}

export function formatDateShort(d: Date): string {
  return d.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "short",
  });
}

export function monthLabel(d: Date): string {
  return d.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    month: "long",
    year: "numeric",
  });
}

export function isoDateIST(d: Date): string {
  const ist = toIST(d);
  const y = ist.getFullYear();
  const mo = String(ist.getMonth() + 1).padStart(2, "0");
  const da = String(ist.getDate()).padStart(2, "0");
  return `${y}-${mo}-${da}`;
}

// ── P&L color class ───────────────────────────────────────────────────────────

/**
 * Get the P&L color class.
 */
export function pnlColor(value: number): string {
  const num = Number(value) || 0;
  if (num > 0) return "text-profit";
  if (num < 0) return "text-loss";
  return "text-text-secondary";
}
