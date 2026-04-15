/**
 * Format a number as Indian Rupees (₹).
 * Uses Indian number system (lakhs, crores).
 */
export function formatINR(value: number): string {
  const absValue = Math.abs(value);
  const sign = value < 0 ? "-" : "";
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
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
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

/**
 * Get the P&L color class.
 */
export function pnlColor(value: number): string {
  if (value > 0) return "text-profit";
  if (value < 0) return "text-loss";
  return "text-text-secondary";
}
