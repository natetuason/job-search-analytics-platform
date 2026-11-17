"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api, ApiError, REC_LABEL, STATUS_LABEL, STATUSES, TIER_LABEL,
  type Flag, type Pay, type Recommendation, type SkipReason, type Status, type Tier, payText,
} from "@/lib/api";
import { SkipReasonDialog } from "@/components/SkipReasonDialog";

export function cx(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

const REC_STYLE: Record<Recommendation, string> = {
  apply: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  maybe: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  skip: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

const TIER_STYLE: Record<Tier, string> = {
  strong_fit: "border-indigo-300 text-indigo-700 dark:border-indigo-700 dark:text-indigo-300",
  competitive: "border-sky-300 text-sky-700 dark:border-sky-800 dark:text-sky-300",
  reach: "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400",
};

export function RecBadge({ rec }: { rec: Recommendation }) {
  return <span className={cx("rounded px-2 py-0.5 text-xs font-semibold", REC_STYLE[rec])}>{REC_LABEL[rec]}</span>;
}

export function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span className={cx("rounded border px-2 py-0.5 text-xs font-medium", TIER_STYLE[tier])}
      title="A qualitative label for how well you match, not a chance of an interview or offer">
      {TIER_LABEL[tier]}
    </span>
  );
}

export function ScorePill({ score }: { score: number }) {
  return (
    <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 border-zinc-300 text-sm font-bold tabular-nums dark:border-zinc-600"
      title="Match score 0–100 (how well the posting matches your profile and preferences)">
      {Math.round(score)}
    </span>
  );
}

export function PayText({ pay }: { pay: Pay | null }) {
  if (!pay) return <span className="text-zinc-500">Pay not posted</span>;
  if (pay.kind === "posted") return <span className="font-medium">{payText(pay)}</span>;
  return (
    <span className="text-zinc-600 dark:text-zinc-400" title="Not posted. Estimated from similar postings.">
      <span className="mr-1 rounded bg-zinc-100 px-1 text-[10px] font-semibold uppercase tracking-wide dark:bg-zinc-800">
        Estimate
      </span>
      {payText(pay)} <span className="text-xs">({pay.confidence})</span>
    </span>
  );
}

const FLAG_STYLE: Record<Flag["status"], string> = {
  blocks: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  needs_your_check: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  check: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  ok: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
};
const FLAG_SUFFIX: Record<Flag["status"], string> = {
  blocks: " — conflicts with your Settings", needs_your_check: " — needs your check", check: " — check this",
  ok: " — fine for you",
};

export function FlagChips({ flags, hideOk = false }: { flags: Flag[]; hideOk?: boolean }) {
  const shown = hideOk ? flags.filter((f) => f.status !== "ok") : flags;
  if (!shown.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((f, i) => (
        <span key={i} className={cx("rounded px-1.5 py-0.5 text-xs", FLAG_STYLE[f.status])}>
          {f.label}{FLAG_SUFFIX[f.status]}
        </span>
      ))}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
      {error}
      {onRetry && <button onClick={onRetry} className="ml-3 underline">Retry</button>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="py-8 text-center text-sm text-zinc-500">{label}</p>;
}

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

// One color per status, used on the dropdown, badges and the stripe on each job card.
export const STATUS_STYLE: Record<Status, { chip: string; stripe: string }> = {
  new: { chip: "border-zinc-300 bg-white text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300",
    stripe: "border-l-zinc-300 dark:border-l-zinc-700" },
  shortlisted: { chip: "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300",
    stripe: "border-l-sky-400" },
  applied: { chip: "border-pine-300 bg-pine-50 text-pine-800 dark:border-pine-700 dark:bg-pine-950 dark:text-pine-200",
    stripe: "border-l-pine-500" },
  interviewing: { chip: "border-violet-300 bg-violet-50 text-violet-800 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-300",
    stripe: "border-l-violet-500" },
  offer: { chip: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300",
    stripe: "border-l-amber-400" },
  rejected: { chip: "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-300",
    stripe: "border-l-rose-400" },
  skipped: { chip: "border-zinc-200 bg-zinc-100 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-800 dark:text-zinc-400",
    stripe: "border-l-zinc-200 dark:border-l-zinc-800" },
  expired: { chip: "border-zinc-200 bg-zinc-100 text-zinc-400 line-through dark:border-zinc-800 dark:bg-zinc-800",
    stripe: "border-l-zinc-200 dark:border-l-zinc-800" },
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={cx("rounded border px-2 py-0.5 text-xs font-medium", STATUS_STYLE[status].chip)}>{STATUS_LABEL[status]}</span>;
}

/** Status dropdown that saves on change. Marking "applied" records today's date unless one is set later. */
export function StatusSelect({ jobId, status, onChange, className, jobLabel }: {
  jobId: number; status: Status; onChange?: (s: Status, appliedAt: string | null) => void; className?: string;
  jobLabel?: string;
}) {
  const [value, setValue] = useState(status);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function change(next: Status, extra?: { reasons?: SkipReason[]; note?: string | null; applied_at?: string }) {
    const prev = value;
    setValue(next);
    setSaving(true);
    setError(null);
    try {
      const r = await api.setStatus(jobId, { status: next, ...extra });
      onChange?.(r.status, r.applied_at);
    } catch (e) {
      setValue(prev);
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <span className="inline-flex flex-col">
      <select aria-label="Status" value={value} disabled={saving}
        onChange={(e) => (e.target.value === "skipped" ? setAsking(true) : change(e.target.value as Status))}
        className={cx("rounded-md border px-2 py-1 text-sm font-medium", STATUS_STYLE[value].chip, className)}>
        {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
      </select>
      {error && <span className="mt-1 text-xs text-red-600">{error}</span>}
      {asking && (
        <SkipReasonDialog job={jobLabel} onCancel={() => setAsking(false)}
          onDone={(reasons, note) => { setAsking(false); change("skipped", { reasons, note }); }}
          onApplied={(applied_at) => { setAsking(false); change("applied", { applied_at }); }} />
      )}
    </span>
  );
}

export function ApplyButton({ url, className }: { url: string; className?: string }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
      className={cx("inline-flex items-center justify-center rounded-md bg-pine-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-pine-600 dark:bg-pine-300 dark:text-pine-950 dark:hover:bg-pine-200", className)}>
      Open application ↗
    </a>
  );
}

export function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900", className)}>
      {children}
    </section>
  );
}

/** Fetch on mount (and when `fetcher` changes). Stale responses are dropped, so fast filter changes can't race. */
export function useResource<T>(fetcher: () => Promise<T>) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>(
    { data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let current = true;
    fetcher().then(
      (data) => current && setState({ data, error: null, loading: false }),
      (e) => current && setState((s) => ({ ...s, error: errorText(e), loading: false })),
    );
    return () => {
      current = false;
    };
  }, [fetcher, nonce]);
  const reload = useCallback(() => {
    setState((s) => ({ ...s, error: null, loading: true }));
    setNonce((n) => n + 1);
  }, []);
  return { ...state, reload };
}
