"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, daysAgo, LOCATION_LABEL, shortDate, type JobRow, type LocationClass } from "@/lib/api";
import {
  ApplyButton, cx, ErrorBox, errorText, FlagChips, Loading, PayText, RecBadge, ScorePill, STATUS_STYLE, StatusSelect, TierBadge,
} from "@/components/ui";

const PAGE = 50;

// Which of my statuses each view shows.
const VIEWS = {
  todo: { label: "To do", status: "new,shortlisted" },
  shortlisted: { label: "Shortlisted", status: "shortlisted" },
  progress: { label: "Applied & interviewing", status: "applied,interviewing,offer" },
  closed: { label: "Closed / skipped", status: "rejected,skipped,expired" },
  all: { label: "All", status: "" },
} as const;
type View = keyof typeof VIEWS;

const RECS = [
  { value: "apply,maybe", label: "Apply + Maybe" },
  { value: "apply", label: "Apply only" },
  { value: "maybe", label: "Maybe only" },
  { value: "skip", label: "Skipped by scorer" },
  { value: "", label: "Everything" },
];

function apiParams(sp: URLSearchParams): URLSearchParams {
  const view = (sp.get("view") as View) in VIEWS ? (sp.get("view") as View) : "todo";
  const p = new URLSearchParams();
  p.set("rec", sp.get("rec") ?? "apply,maybe");
  if (VIEWS[view].status) p.set("status", VIEWS[view].status);
  for (const key of ["location", "q", "sort", "group"]) {
    const v = sp.get(key);
    if (v) p.set(key, v);
  }
  if (sp.get("rotational") === "1") p.set("rotational", "true");
  return p;
}

export function JobsList() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [query, setQuery] = useState(sp.get("q") ?? "");
  const key = apiParams(new URLSearchParams(sp.toString())).toString();
  // Results and errors are tagged with the filter key they belong to, so a slow old response never shows.
  const [page, setPage] = useState<{ key: string; items: JobRow[]; total: number } | null>(null);
  const [error, setError] = useState<{ key: string; text: string } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let current = true;
    const p = new URLSearchParams(key);
    p.set("limit", String(PAGE));
    api.jobs(p).then(
      (r) => current && setPage({ key, items: r.items, total: r.total }),
      (e) => current && setError({ key, text: errorText(e) }),
    );
    return () => {
      current = false;
    };
  }, [key, nonce]);

  async function loadMore() {
    if (!page) return;
    setLoadingMore(true);
    try {
      const p = new URLSearchParams(key);
      p.set("limit", String(PAGE));
      p.set("offset", String(page.items.length));
      const r = await api.jobs(p);
      setPage((prev) => (prev && prev.key === key ? { ...prev, items: [...prev.items, ...r.items], total: r.total } : prev));
    } catch (e) {
      setError({ key, text: errorText(e) });
    } finally {
      setLoadingMore(false);
    }
  }

  function retry() {
    setError(null);
    setNonce((n) => n + 1);
  }

  const items = page?.key === key ? page.items : [];
  const total = page?.key === key ? page.total : null;
  const errorText_ = error?.key === key ? error.text : null;
  const loading = loadingMore || (page?.key !== key && !errorText_);

  function setParam(name: string, value: string | null) {
    const next = new URLSearchParams(sp.toString());
    if (value === null || value === "") next.delete(name);
    else next.set(name, value);
    // "rec" has a meaningful empty value (everything), so it's stored explicitly.
    if (name === "rec") next.set("rec", value ?? "");
    router.replace(`${pathname}?${next}`, { scroll: false });
  }

  const view = (sp.get("view") as View) in VIEWS ? (sp.get("view") as View) : "todo";
  const select = "rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Jobs to Apply To</h1>
          <p className="text-sm text-zinc-500">
            Ranked by match score. The tier is a label for fit, not a chance of an interview. You apply yourself.
          </p>
        </div>
        <a href={`/api/export.csv?${key}`}
          className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
          Export CSV
        </a>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {(Object.keys(VIEWS) as View[]).map((v) => (
          <button key={v} onClick={() => setParam("view", v === "todo" ? null : v)}
            className={cx("-mb-px border-b-2 px-3 py-2 text-sm",
              v === view ? "border-pine-600 font-medium text-pine-800 dark:border-pine-300 dark:text-pine-200" : "border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200")}>
            {VIEWS[v].label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <form onSubmit={(e) => { e.preventDefault(); setParam("q", query.trim() || null); }} className="flex gap-1">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, company, place"
            aria-label="Search" className={cx(select, "w-56")} />
          <button className={select}>Search</button>
        </form>
        <select aria-label="Recommendation" className={select} value={sp.get("rec") ?? "apply,maybe"}
          onChange={(e) => setParam("rec", e.target.value)}>
          {RECS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
        <select aria-label="Location" className={select} value={sp.get("location") ?? ""}
          onChange={(e) => setParam("location", e.target.value || null)}>
          <option value="">Any location</option>
          <option value="preferred,remote">NYC / NJ + remote</option>
          {(Object.keys(LOCATION_LABEL) as LocationClass[]).map((l) => <option key={l} value={l}>{LOCATION_LABEL[l]}</option>)}
        </select>
        <select aria-label="Sort" className={select} value={sp.get("sort") ?? "score"}
          onChange={(e) => setParam("sort", e.target.value === "score" ? null : e.target.value)}>
          <option value="score">Best match</option>
          <option value="posted">Newest posted</option>
          <option value="found">Newest found</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={sp.get("rotational") === "1"}
            onChange={(e) => setParam("rotational", e.target.checked ? "1" : null)} />
          Rotational programs only
        </label>
        {total !== null && <span className="ml-auto text-sm text-zinc-500">{total.toLocaleString()} {total === 1 ? "job" : "jobs"}</span>}
      </div>

      {errorText_ && <ErrorBox error={errorText_} onRetry={retry} />}

      <ul className="space-y-2">
        {items.map((job) => <JobItem key={job.id} job={job} />)}
      </ul>

      {loading && <Loading />}
      {!loading && !errorText_ && items.length === 0 && (
        <p className="py-8 text-center text-sm text-zinc-500">No jobs match these filters.</p>
      )}
      {!loading && total !== null && items.length < total && (
        <button onClick={loadMore}
          className="w-full rounded-md border border-zinc-300 py-2 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
          Show more ({(total - items.length).toLocaleString()} left)
        </button>
      )}
    </div>
  );
}

function JobItem({ job }: { job: JobRow }) {
  const [status, setStatus] = useState(job.status);
  return (
    <li className={cx("rounded-lg border border-l-4 border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900",
      STATUS_STYLE[status].stripe, (status === "skipped" || status === "expired") && "opacity-60")}>
      <div className="flex flex-wrap gap-3 sm:flex-nowrap">
        <ScorePill score={job.score} />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/jobs/${job.id}`} className="font-medium hover:underline">{job.title}</Link>
            <RecBadge rec={job.recommendation} />
            <TierBadge tier={job.tier} />
            {job.rotational && <span className="rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-800 dark:bg-violet-950 dark:text-violet-300">Rotational</span>}
            {!job.is_active && <span className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-800 dark:bg-rose-950 dark:text-rose-300" title={job.closed_at ? `Closed ${shortDate(job.closed_at)}` : undefined}>Listing closed</span>}
            {job.is_active && job.is_new && (
              <span className="rounded bg-pine-600 px-2 py-0.5 text-xs font-medium text-white dark:bg-pine-300 dark:text-pine-950">New</span>
            )}
            {job.early_career && <span className="rounded bg-teal-50 px-2 py-0.5 text-xs text-teal-800 dark:bg-teal-950 dark:text-teal-300">Early career</span>}
          </div>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            <span className="font-medium text-zinc-800 dark:text-zinc-200">{job.company}</span>
            {job.location && <> · {job.location.length > 80 ? job.location.slice(0, 80) + "…" : job.location}</>}
            {job.posted_at ? <> · posted {daysAgo(job.posted_at)}</> : <> · found {daysAgo(job.first_seen_at)}</>}
          </p>
          {job.other_locations > 0 && (
            <p className="text-xs text-zinc-500">
              Also posted in {job.other_locations} other location{job.other_locations > 1 ? "s" : ""} ·{" "}
              <Link className="underline" href={`/?${new URLSearchParams({ q: job.title, group: "false", view: "all" })}`}>
                show all
              </Link>
            </p>
          )}
          <p className="text-sm"><PayText pay={job.pay} /></p>
          <FlagChips flags={job.flags} hideOk />
        </div>
        <div className="flex w-full shrink-0 items-start gap-2 sm:w-auto sm:flex-col sm:items-end">
          <ApplyButton url={job.apply_url} />
          <StatusSelect jobId={job.id} status={job.status} onChange={(s) => setStatus(s)}
            jobLabel={`${job.title} · ${job.company}`} />
        </div>
      </div>
    </li>
  );
}
