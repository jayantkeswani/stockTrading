"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { IhV2Proposal, IhV2ProposalAction, IhV2Review } from "@/lib/types";
import { actionLabel, groupByWeek, isAgentPending, needsConfirm, proposalStatusClass, formatPnl } from "@/lib/ihV2";
import { Section, Empty, Val, pnlCls } from "./common";

const POLL_MS = 60000;
const FAST_POLL_MS = 5000;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="text-[11px] font-mono leading-relaxed min-w-0 break-words">
      <span className="text-text-muted uppercase text-[10px] mr-1.5">{label}</span>
      {children}
    </div>
  );
}

function Chip({ cls, children }: { cls: string; children: React.ReactNode }) {
  return <span className={`text-[10px] font-mono px-1.5 py-px rounded ${cls}`}>{children}</span>;
}

function fmtNum(v: number | null | undefined, d = 2): string {
  return v == null || !Number.isFinite(v) ? "—" : v.toFixed(d);
}

function ProposalItem({ p, onChanged }: { p: IhV2Proposal; onChanged: (u: IhV2Proposal) => void }) {
  const [note, setNote] = useState("");
  const [pending, setPending] = useState<IhV2ProposalAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHist, setShowHist] = useState(false);

  const run = async (action: IhV2ProposalAction) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.actOnIhV2Proposal(p.id, action, note.trim() || undefined);
      setPending(null);
      setNote("");
      onChanged(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(false);
    }
  };

  const click = (a: string) => {
    const action = a as IhV2ProposalAction;
    if (needsConfirm(action)) setPending(action);
    else run(action);
  };

  const plan = p.apply_plan;
  const st = p.stats;

  return (
    <div className="border border-border rounded p-2 space-y-1.5 min-w-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip cls={proposalStatusClass(p.status)}>{p.status}</Chip>
        <span className="text-[10px] font-mono text-text-muted">#{p.idx}</span>
        {p.kind && <Chip cls="bg-bg-tertiary text-text-secondary">{p.kind}</Chip>}
        {isAgentPending(p.status) && <span className="text-[10px] font-mono text-accent animate-pulse">agent working…</span>}
        {p.challenger_id != null && (
          <Chip cls="bg-accent/15 text-accent">
            challenger {String(p.challenger_id)}
            {p.challenger_mode ? ` · ${p.challenger_mode}` : ""}
            {p.started_on ? ` · since ${p.started_on}` : ""}
          </Chip>
        )}
      </div>
      <p className="text-xs font-mono text-text-primary break-words">{p.change}</p>
      {p.evidence && <Row label="evidence"><span className="text-text-secondary">{p.evidence}</span></Row>}
      {p.expected_effect && <Row label="expected"><span className="text-text-secondary">{p.expected_effect}</span></Row>}
      {p.risk && <Row label="risk"><span className="text-text-secondary">{p.risk}</span></Row>}

      {plan && (
        <div className="border-l-2 border-accent/40 pl-2 space-y-1">
          <div className="text-[10px] font-mono text-text-muted uppercase">
            apply plan{plan._mode ? ` · ${plan._mode}` : ""}
            {plan.evidence_holds === false ? " · evidence does not hold" : ""}
          </div>
          {plan.recheck && <Row label="recheck"><span className="text-text-secondary">{plan.recheck}</span></Row>}
          {plan.reasoning && <Row label="reasoning"><span className="text-text-secondary">{plan.reasoning}</span></Row>}
          {plan.params_override && Object.keys(plan.params_override).length > 0 && (
            <Row label="params"><Val v={plan.params_override} /></Row>
          )}
          {plan.prompt_addendum && <Row label="prompt+"><span className="text-text-secondary whitespace-pre-wrap">{plan.prompt_addendum}</span></Row>}
          {plan.build_brief && <Row label="build brief"><span className="text-text-secondary whitespace-pre-wrap">{plan.build_brief}</span></Row>}
          {plan._validation != null && plan._validation !== "" && (
            <div className="text-[11px] font-mono text-warning break-words">
              <Val v={plan._validation} />
            </div>
          )}
        </div>
      )}

      {st && (
        <div className="text-[11px] font-mono space-y-0.5">
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-text-secondary">
            <span>days {st.days}/{st.min_days}</span>
            <span>diff <b className={pnlCls(st.diff_total)}>{formatPnl(st.diff_total)}</b></span>
            <span>t {fmtNum(st.diff_t)}</span>
            <span>halves {formatPnl(st.first_half_diff)} / {formatPnl(st.second_half_diff)}</span>
          </div>
          {!st.eligible && st.days < st.min_days && (
            <div className="text-text-muted">promote unlocks after {st.min_days - st.days} more trading days</div>
          )}
        </div>
      )}

      {p.history.length > 0 && (
        <div>
          <button onClick={() => setShowHist((v) => !v)} className="text-[10px] font-mono text-text-muted hover:text-text-secondary">
            {showHist ? "▾" : "▸"} history ({p.history.length})
          </button>
          {showHist && (
            <ul className="mt-1 space-y-0.5">
              {p.history.map((h, i) => (
                <li key={i} className="text-[10px] font-mono text-text-secondary break-words">
                  {h.status} · {h.at ? new Date(h.at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}
                  {h.note ? ` · ${h.note}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {p.actions.length > 0 && (
        <div className="space-y-1.5">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="optional note"
            rows={1}
            className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-[11px] font-mono text-text-primary placeholder:text-text-muted"
          />
          {pending ? (
            <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
              <span className="text-warning">
                {pending === "approve" ? "approve and start the analysis agent?" : `${actionLabel(pending).toLowerCase()} this change?`}
              </span>
              <button onClick={() => run(pending)} disabled={busy} className="px-2 py-0.5 rounded bg-accent text-white disabled:opacity-50">
                {busy ? "working…" : "Confirm"}
              </button>
              <button onClick={() => setPending(null)} disabled={busy} className="px-2 py-0.5 rounded border border-border text-text-secondary disabled:opacity-50">
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {p.actions.map((a) => (
                <button
                  key={a}
                  onClick={() => click(a)}
                  disabled={busy}
                  className={`text-[10px] font-mono px-2 py-0.5 rounded disabled:opacity-50 ${
                    a === "reject" || a === "retire" ? "border border-border text-text-secondary" : "bg-accent text-white hover:opacity-90"
                  }`}
                >
                  {actionLabel(a)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {error && <p className="text-[11px] font-mono text-loss break-words">{error}</p>}
    </div>
  );
}

/**
 * Weekly review approval card: latest review summary + change proposals grouped by week, with
 * server-driven action buttons (approve/reject/analyse/promote/retire). Polls every 60s, every 5s
 * while an approved proposal's agent is running. Used by: V2Panel, MobileHunterV2
 */
export function WeeklyReviewCard() {
  const [proposals, setProposals] = useState<IhV2Proposal[] | null>(null);
  const [review, setReview] = useState<IhV2Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([api.getIhV2Proposals(50), api.getIhV2Reviews(1)]);
      setProposals(p);
      setReview(r[0] ?? null);
      setError(null);
      return p;
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let pendingNow = false;
    const tick = async () => {
      const p = await load();
      if (cancelled) return;
      if (p) pendingNow = p.some((x) => isAgentPending(x.status));
      timer.current = setTimeout(tick, pendingNow ? FAST_POLL_MS : POLL_MS);
    };
    timer.current = setTimeout(tick, 0);
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const onChanged = (u: IhV2Proposal) => {
    setProposals((prev) => (prev ? prev.map((x) => (x.id === u.id ? u : x)) : prev));
    // An approve/analyse starts a background agent: kick a fast poll without waiting for the timer.
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      load().then(() => {
        timer.current = setTimeout(() => load(), FAST_POLL_MS);
      });
    }, FAST_POLL_MS);
  };

  const groups = groupByWeek(proposals ?? []);
  const empty = proposals != null && proposals.length === 0 && !review;

  return (
    <Section title="Weekly review" right={review && <span className="text-[10px] font-mono text-text-muted">week ending {review.week_ending}</span>}>
      {error && <p className="text-[11px] font-mono text-loss">{error}</p>}
      {proposals == null && !error && <Empty>loading…</Empty>}
      {empty && <Empty>no weekly review yet — the first runs Saturday 10:00</Empty>}
      {review?.summary && (
        <p className="text-[11px] font-mono text-text-secondary leading-relaxed whitespace-pre-wrap break-words">{review.summary}</p>
      )}
      {groups.map((g) => (
        <div key={g.week_ending} className="space-y-1.5">
          <div className="text-[10px] font-mono text-text-muted uppercase tracking-wider">week ending {g.week_ending}</div>
          {g.items.map((p) => (
            <ProposalItem key={p.id} p={p} onChanged={onChanged} />
          ))}
        </div>
      ))}
    </Section>
  );
}
