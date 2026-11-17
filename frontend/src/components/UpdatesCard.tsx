"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, payText, shortDate, timeAgo, timeUntil, type Updates } from "@/lib/api";
import { Card, cx, ErrorBox, errorText, RecBadge, StatusBadge } from "@/components/ui";

/** Latest scheduled update: what's new since then, tracked jobs whose listing closed, and "Update now". */
export function UpdatesCard() {
  const [data, setData] = useState<Updates | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let current = true;
    const load = () => api.updates().then(
      (d) => current && (setData(d), setError(null)),
      (e) => current && setError(errorText(e)),
    );
    load();
    // While an update runs (a few minutes), refresh so the result shows up without reloading.
    const timer = setInterval(() => {
      if (current) load();
    }, 10_000);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [nonce]);

  async function updateNow() {
    setStarting(true);
    try {
      await api.startUpdate();
      setNonce((n) => n + 1);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStarting(false);
    }
  }

  if (error && !data) return <ErrorBox error={error} onRetry={() => setNonce((n) => n + 1)} />;
  if (!data) return null;
  const last = data.history[0];
  const digest = data.digest;

  return (
    <Card className="space-y-3 border-pine-200 dark:border-pine-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">Latest update</h2>
          <p className="text-sm text-zinc-500">
            {data.running ? "Updating now: fetching every source, then rescoring (a few minutes)…"
              : digest ? <>Last update {timeAgo(digest.run.finished_at)}. Next one {timeUntil(data.next_due_at)}.</>
              : "No update has run yet."}
          </p>
          {last?.status === "failed" && !data.running && (
            <p className="text-sm text-red-600">The last attempt failed ({timeAgo(last.started_at)}): {last.error}</p>
          )}
        </div>
        <button onClick={updateNow} disabled={data.running || starting}
          className="rounded-md bg-pine-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-pine-600 disabled:opacity-50 dark:bg-pine-300 dark:text-pine-950">
          {data.running || starting ? "Updating…" : "Update now"}
        </button>
      </div>

      {digest && (
        <>
          <p className="text-sm">
            <span className="font-semibold">{digest.run.new_recommended}</span> new Apply/Maybe jobs
            <span className="text-zinc-500"> · {digest.run.new_jobs.toLocaleString()} new postings in all ·{" "}
              {digest.run.closed_jobs.toLocaleString()} listings closed · {digest.run.sources_ok} sources checked
              {digest.run.sources_failed > 0 && <span className="text-red-600"> ({digest.run.sources_failed} failed)</span>}
            </span>
          </p>
          {digest.new_jobs.length > 0 && (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {digest.new_jobs.slice(0, 10).map((j) => (
                <li key={j.id} className="flex flex-wrap items-center gap-2 py-1.5 text-sm">
                  <span className="w-8 tabular-nums text-zinc-500">{Math.round(j.score)}</span>
                  <RecBadge rec={j.recommendation} />
                  <Link href={`/jobs/${j.id}`} className="font-medium hover:underline">{j.title}</Link>
                  <span className="text-zinc-500">· {j.company} · {payText(j.pay)}{j.pay?.kind === "estimate" ? " (est.)" : ""}</span>
                </li>
              ))}
            </ul>
          )}
          {digest.new_jobs.length > 10 && (
            <Link href="/?sort=found" className="text-sm underline">See all {digest.run.new_recommended} new jobs</Link>
          )}
          {digest.closed_tracked.length > 0 && (
            <div className="rounded-md bg-rose-50 p-2 text-sm dark:bg-rose-950/40">
              <p className="font-medium text-rose-800 dark:text-rose-300">Listings you&apos;re tracking that closed:</p>
              <ul>
                {digest.closed_tracked.map((j) => (
                  <li key={j.id} className="flex flex-wrap items-center gap-2">
                    <Link href={`/jobs/${j.id}`} className="hover:underline">{j.title}</Link>
                    <span className="text-zinc-500">· {j.company}</span> <StatusBadge status={j.status} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {data.history.length > 1 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-zinc-500">Recent updates</summary>
          <ul className="mt-1 space-y-0.5">
            {data.history.map((r) => (
              <li key={r.id} className={cx(r.status === "failed" && "text-red-600")}>
                {shortDate(r.started_at)} {new Date(r.started_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                {" "}· {r.trigger === "manual" ? "manual" : "scheduled"} · {r.status}
                {r.status === "succeeded" && ` · ${r.new_recommended} new Apply/Maybe, ${r.closed_jobs} closed`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
