"use client";

import { useStore } from "@/store";

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
  const { scanLogs } = useStore();

  if (scanLogs.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-bg-secondary">
      <div className="px-4 py-2.5 border-b border-border">
        <h2 className="text-xs font-semibold text-text-primary">Scan Log</h2>
      </div>
      <div className="max-h-[200px] overflow-y-auto divide-y divide-border/30">
        {scanLogs.map((entry) => (
          <div key={entry.id} className="px-4 py-2 flex items-center gap-2">
            {entry.type === "start" ? (
              <>
                <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" />
                <span className="text-xs text-text-secondary">
                  <span className="text-accent font-medium">{entry.strategy}</span>
                  {" "}scan started
                </span>
              </>
            ) : (
              <>
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  (entry.signalsGenerated ?? 0) > 0 ? "bg-profit" : "bg-text-muted"
                }`} />
                <span className="text-xs text-text-secondary">
                  <span className="font-medium text-text-primary">{entry.strategy}</span>
                  {" "}complete &mdash;{" "}
                  <span className="font-mono">
                    {entry.symbolsScanned} scanned, {" "}
                    <span className={(entry.signalsGenerated ?? 0) > 0 ? "text-profit" : "text-text-muted"}>
                      {entry.signalsGenerated} signal{entry.signalsGenerated !== 1 ? "s" : ""}
                    </span>
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
