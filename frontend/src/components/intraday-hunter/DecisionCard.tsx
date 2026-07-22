"use client";

import { useState } from "react";
import type { IntradayHunterRun, IHCall2 } from "@/lib/types";
import { apiUrl } from "@/lib/api";
import { DirectionBadge, confidenceColor } from "./badges";

const SECTION_HDR = "text-xs font-mono font-medium text-text-secondary uppercase tracking-wider";

const DECISION_CLS: Record<string, string> = {
  ENTER: "text-profit",
  WAIT: "text-warning",
  SKIP: "text-text-muted",
};

/** Call 2 — the at-open decision (direction, basket, levels, rationale, charts). */
export function DecisionCard({ run }: { run: IntradayHunterRun }) {
  const c2 = run.call2_json;
  const decision = (c2?.decision || run.decision || "—").toUpperCase();
  const decCls = DECISION_CLS[decision] ?? "text-text-secondary";

  return (
    <div className="bg-bg-secondary border border-border rounded p-3 space-y-3">
      <div className="flex items-center gap-2">
        <h2 className={SECTION_HDR}>Decision</h2>
        <span className="text-[10px] font-mono text-text-muted">Call 2 · 09:18–09:30</span>
        {c2?._at && (
          <span className="text-[10px] font-mono text-text-muted ml-auto">@ {c2._at}</span>
        )}
      </div>

      {!c2 ? (
        <p className="text-xs font-mono text-text-muted lowercase">no decision yet — watcher fires from 09:18</p>
      ) : (
        <>
          <div className="flex items-baseline gap-3">
            <span className={`text-2xl font-mono font-bold ${decCls}`}>{decision}</span>
            <DirectionBadge direction={c2.direction} />
            {c2.confidence != null && (
              <span className={`text-sm font-mono ${confidenceColor(c2.confidence)}`}>
                {c2.confidence}<span className="text-text-muted text-xs"> conf</span>
              </span>
            )}
            {c2.recheck_in_minutes != null && decision === "WAIT" && (
              <span className="text-[10px] font-mono text-text-muted">recheck {c2.recheck_in_minutes}m</span>
            )}
          </div>

          {/* Basket (legs) */}
          {c2.legs && c2.legs.length > 0 && (
            <div className="space-y-1">
              <div className={SECTION_HDR}>Basket</div>
              <div className="flex flex-wrap gap-2">
                {c2.legs.map((leg, i) => (
                  <span key={i} className="text-xs font-mono px-2 py-1 rounded bg-bg-tertiary border border-border">
                    <span className="text-text-primary">{leg.index}</span>
                    {leg.strike && <span className="text-text-secondary"> {leg.strike}</span>}
                    {leg.option_type && <span className="text-accent"> {leg.option_type}</span>}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Excluded indices */}
          {c2.excluded_indices && c2.excluded_indices.length > 0 && (
            <div className="space-y-1">
              {c2.excluded_indices.map((ex, i) => (
                <div key={i} className="text-[11px] font-mono text-text-muted">
                  <span className="text-loss/70">excluded {ex.index}</span>
                  {ex.reason && <span> — {ex.reason}</span>}
                </div>
              ))}
            </div>
          )}

          {/* Levels */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <LevelCell label="entry trigger" value={c2.entry_trigger} />
            <LevelCell label="invalidation" value={c2.invalidation_level ? c2.invalidation_level.toLocaleString("en-IN") : undefined} />
            <LevelCell label="target" value={c2.target} />
          </div>

          {c2.rationale && (
            <div>
              <div className={SECTION_HDR}>Rationale</div>
              <p className="text-xs font-mono text-text-primary leading-relaxed mt-1">{c2.rationale}</p>
            </div>
          )}
        </>
      )}

      <DecisionLog history={run.call2_history} />

      <ChartStrip run={run} />
    </div>
  );
}

/** Every Call 2 this session (WAIT→…→ENTER/SKIP) — preserves each re-run, not just the latest. */
function DecisionLog({ history }: { history?: IHCall2[] }) {
  const items = history ?? [];
  if (items.length === 0) return null;
  const last = items.length - 1;
  return (
    <div className="space-y-1">
      <div className={SECTION_HDR}>Decision Log · {items.length} {items.length === 1 ? "check" : "checks"}</div>
      <div className="border border-border rounded divide-y divide-border">
        {items.map((c, i) => {
          const dec = (c.decision || "—").toUpperCase();
          const cls = DECISION_CLS[dec] ?? "text-text-secondary";
          return (
            <div
              key={i}
              className={`flex items-start gap-2 px-2 py-1 text-[11px] font-mono ${
                i === last ? "bg-bg-tertiary/40" : ""
              }`}
            >
              <span className="text-text-muted w-10 shrink-0">{c._at || "—"}</span>
              <span className={`${cls} font-medium w-12 shrink-0`}>{dec}</span>
              <DirectionBadge direction={c.direction} />
              {c.confidence != null && (
                <span className={`${confidenceColor(c.confidence)} w-8 shrink-0`}>{c.confidence}</span>
              )}
              {dec === "WAIT" && c.recheck_in_minutes != null && (
                <span className="text-text-muted shrink-0">↻{c.recheck_in_minutes}m</span>
              )}
              {c.rationale && (
                <span className="text-text-secondary line-clamp-2 flex-1" title={c.rationale}>
                  {c.rationale}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LevelCell({ label, value }: { label: string; value?: string }) {
  return (
    <div className="bg-bg-tertiary/50 border border-border rounded px-2 py-1.5">
      <div className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-0.5">{label}</div>
      <div className="text-xs font-mono text-text-primary">{value || "—"}</div>
    </div>
  );
}

/** Prev-day + opening chart images per index, with a kind toggle. */
function ChartStrip({ run }: { run: IntradayHunterRun }) {
  const [kind, setKind] = useState<"opening" | "prevday">("opening");
  const urls = run.chart_urls?.[kind] || {};
  const indices = Object.keys(urls);
  const hasOpening = Object.keys(run.chart_urls?.opening || {}).length > 0;
  const hasPrevday = Object.keys(run.chart_urls?.prevday || {}).length > 0;

  if (!hasOpening && !hasPrevday) return null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className={SECTION_HDR}>Charts</span>
        <div className="flex gap-1">
          {hasOpening && <ChartTab active={kind === "opening"} onClick={() => setKind("opening")}>opening</ChartTab>}
          {hasPrevday && <ChartTab active={kind === "prevday"} onClick={() => setKind("prevday")}>prev-day</ChartTab>}
        </div>
      </div>
      {indices.length === 0 ? (
        <p className="text-[11px] font-mono text-text-muted lowercase">no {kind} charts</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
          {indices.map((idx) => (
            <figure key={idx} className="border border-border rounded overflow-hidden bg-white">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={apiUrl(urls[idx])} alt={`${idx} ${kind} chart`} className="w-full h-auto" loading="lazy" />
              <figcaption className="text-[10px] font-mono text-text-muted px-1.5 py-0.5 bg-bg-secondary">{idx}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </div>
  );
}

function ChartTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`text-[10px] font-mono px-1.5 py-px rounded border transition-colors ${
        active ? "bg-accent/20 text-accent border-accent/30" : "text-text-muted border-transparent hover:bg-bg-tertiary"
      }`}
    >
      {children}
    </button>
  );
}
