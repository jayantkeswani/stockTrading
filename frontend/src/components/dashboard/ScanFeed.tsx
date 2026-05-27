"use client";

import { useStore } from "@/store";
import { startOfDayIST } from "@/lib/formatters";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "";
  }
}

export function ScanFeed() {
  const scanLogs = useStore((s) => s.scanLogs);
  const todayStart = startOfDayIST(new Date()).getTime();
  const todayLogs = scanLogs.filter((e) => new Date(e.timestamp).getTime() >= todayStart);

  if (todayLogs.length === 0) return null;

  return (
    <div className="rounded border border-border bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Scan Log
        </h2>
      </div>
      <div className="max-h-[160px] overflow-y-auto">
        {todayLogs.map((entry) => (
          <div key={entry.id} className="px-3 py-1 flex items-center gap-1.5 border-b border-border/20 last:border-0">
            {entry.type === "start" ? (
              <>
                <div className="w-1 h-1 rounded-full bg-accent animate-pulse shrink-0" />
                <span className="text-xs font-mono text-text-muted">
                  <span className="text-accent">{entry.strategy}</span> scan started
                </span>
              </>
            ) : (
              <>
                <div className={`w-1 h-1 rounded-full shrink-0 ${
                  (entry.signalsGenerated ?? 0) > 0 ? "bg-profit" : "bg-text-muted"
                }`} />
                <span className="text-xs font-mono text-text-muted">
                  <span className="text-text-secondary">{entry.strategy}</span>
                  {" "}&mdash; {entry.symbolsScanned} scanned,{" "}
                  <span className={(entry.signalsGenerated ?? 0) > 0 ? "text-profit" : ""}>
                    {entry.signalsGenerated} signal{entry.signalsGenerated !== 1 ? "s" : ""}
                  </span>
                </span>
              </>
            )}
            <span className="text-[10px] text-text-muted ml-auto font-mono shrink-0">
              {formatTime(entry.timestamp)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
