"use client";

import Link from "next/link";
import { api, daysAgo, shortDate, TIER_LABEL, type Dashboard as Data, type Status, type Tier } from "@/lib/api";
import { Card, ErrorBox, Loading, StatusBadge, useResource } from "@/components/ui";
import { SkipFeedbackCard } from "@/components/SkipFeedbackCard";
import { UpdatesCard } from "@/components/UpdatesCard";

const PIPELINE: Status[] = ["shortlisted", "applied", "interviewing", "offer", "rejected", "skipped"];

function Stat({ label, value, href }: { label: string; value: number; href?: string }) {
  const body = (
    <>
      <div className="text-2xl font-semibold tabular-nums">{value.toLocaleString()}</div>
      <div className="text-sm text-zinc-500">{label}</div>
    </>
  );
  return (
    <Card className="p-3">
      {href ? <Link href={href} className="block hover:underline">{body}</Link> : body}
    </Card>
  );
}

export function Dashboard() {
  const { data: d, error, reload } = useResource<Data>(api.dashboard);

  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!d) return <Loading />;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      <UpdatesCard />

      <SkipFeedbackCard />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Apply" value={d.by_recommendation.apply} href="/?rec=apply" />
        <Stat label="Maybe" value={d.by_recommendation.maybe} href="/?rec=maybe" />
        <Stat label="New Apply jobs this week" value={d.new_apply_this_week} href="/?rec=apply&sort=found" />
        <Stat label="Applied this week" value={d.applied_this_week} href="/?view=progress" />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <h2 className="mb-2 font-semibold">My pipeline</h2>
          <ul className="space-y-1 text-sm">
            {PIPELINE.map((s) => (
              <li key={s} className="flex items-center justify-between">
                <StatusBadge status={s} />
                <span className="tabular-nums">{(d.by_status[s] ?? 0).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="mb-2 font-semibold">Apply + Maybe by tier</h2>
          <ul className="space-y-1 text-sm">
            {(Object.keys(TIER_LABEL) as Tier[]).map((t) => (
              <li key={t} className="flex justify-between">
                <span>{TIER_LABEL[t]}</span>
                <span className="tabular-nums">{(d.by_tier[t] ?? 0).toLocaleString()}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-zinc-500">Tiers describe fit, not chances of an interview.</p>
        </Card>
        <Card>
          <h2 className="mb-2 font-semibold">Recent activity</h2>
          {d.recent_activity.length === 0
            ? <p className="text-sm text-zinc-500">Nothing yet. Change a job&apos;s status and it shows up here.</p>
            : (
              <ul className="space-y-1 text-sm">
                {d.recent_activity.map((a) => (
                  <li key={a.job_id}>
                    <Link href={`/jobs/${a.job_id}`} className="hover:underline">{a.title}</Link>
                    <span className="text-zinc-500"> · {a.company} · </span><StatusBadge status={a.status} />
                    <span className="text-zinc-500"> {daysAgo(a.updated_at)}</span>
                  </li>
                ))}
              </ul>
            )}
        </Card>
        <Card>
          <h2 className="mb-2 font-semibold">Sources</h2>
          <p className="mb-2 text-sm text-zinc-500">
            {d.active_jobs.toLocaleString()} active jobs. Scored {d.scored_at ? shortDate(d.scored_at) : "never"}.
            {d.failed_runs_this_week > 0 && <span className="text-red-600"> {d.failed_runs_this_week} failed fetches this week.</span>}
          </p>
          <ul className="space-y-1 text-sm">
            {d.sources.map((s) => (
              <li key={s.kind} className="flex justify-between gap-2">
                <span>{s.kind.replace("_", " ")}</span>
                <span className="text-zinc-500">last fetched {s.last_success ? daysAgo(s.last_success) : "never"}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
