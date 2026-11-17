"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api, shortDate, STATUS_LABEL, STATUSES, type JobDetail as Detail, type SkipReason, type Status,
} from "@/lib/api";
import { SkipReasonDialog } from "@/components/SkipReasonDialog";
import {
  ApplyButton, Card, cx, ErrorBox, errorText, FlagChips, Loading, PayText, RecBadge, ScorePill, TierBadge, useResource, STATUS_STYLE, StatusBadge,
} from "@/components/ui";

const CATEGORY_LABEL: Record<string, string> = {
  skills: "Skills", experience: "Experience level", responsibilities: "Role / responsibilities",
  compensation: "Pay", location: "Location",
};

export function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const fetchJob = useCallback(() => api.job(id), [id]);
  const { data: job, error, reload: load } = useResource<Detail>(fetchJob);

  if (error) return <ErrorBox error={error} onRetry={load} />;
  if (!job) return <Loading />;
  const sd = job.score_detail;
  const ex = sd?.explanation;

  return (
    <div className="space-y-4">
      <Link href="/" className="text-sm text-zinc-500 hover:underline">← Back to list</Link>

      <Card>
        <div className="flex flex-wrap items-start gap-4">
          {sd && <ScorePill score={job.score} />}
          <div className="min-w-0 flex-1 space-y-1">
            <h1 className="text-xl font-semibold">{job.title}</h1>
            <p className="text-zinc-600 dark:text-zinc-400">
              <span className="font-medium text-zinc-800 dark:text-zinc-200">{job.company}</span>
              {job.location && <> · {job.location}</>}
              {job.work_arrangement !== "unknown" && <> · {job.work_arrangement}</>}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {sd && <RecBadge rec={job.recommendation} />}
              {sd && <TierBadge tier={job.tier} />}
              {job.rotational && <span className="rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-800 dark:bg-violet-950 dark:text-violet-300">Rotational program</span>}
              <span className="text-sm"><PayText pay={job.pay} /></span>
            </div>
            {!job.is_active && <p className="text-sm font-medium text-red-600">This listing is no longer active.</p>}
          </div>
          <ApplyButton url={job.apply_url} className="px-4 py-2" />
        </div>
        {ex && <p className="mt-3 text-sm">{ex.summary}</p>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {sd && ex && <WhyCard detail={job} />}
          <Card>
            <h2 className="mb-2 font-semibold">Job description</h2>
            {job.description
              ? <Description text={job.description} />
              : <p className="text-sm text-zinc-500">The source didn&apos;t include a description. Open the application page to read it.</p>}
          </Card>
        </div>
        <div className="space-y-4">
          <Tracker job={job} onSaved={load} />
          <Card>
            <h2 className="mb-2 font-semibold">Where it was found</h2>
            <ul className="space-y-1 text-sm">
              {job.sources.map((s, i) => (
                <li key={i} className="break-words [overflow-wrap:anywhere]">
                  <span className="font-medium">{s.kind.replace("_", " ")}</span>
                  {s.ref && <span className="text-zinc-500"> · {s.ref}</span>}
                  <span className="text-zinc-500"> · {shortDate(s.first_seen_at)}</span>
                </li>
              ))}
            </ul>
            {job.posted_at && <p className="mt-2 text-sm text-zinc-500">Posted {shortDate(job.posted_at)}</p>}
          </Card>
        </div>
      </div>
    </div>
  );
}

function WhyCard({ detail }: { detail: Detail }) {
  const sd = detail.score_detail!;
  const ex = sd.explanation;
  const cats = sd.breakdown.categories;
  return (
    <Card className="space-y-4">
      <div>
        <h2 className="font-semibold">Why this ranking</h2>
        <ul className="mt-1 list-disc pl-5 text-sm">
          {ex.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      </div>

      <div className="space-y-1.5">
        {Object.entries(cats).map(([k, v]) => (
          <div key={k} className="flex items-center gap-2 text-sm">
            <span className="w-44 shrink-0">{CATEGORY_LABEL[k] ?? k}
              <span className="text-xs text-zinc-500"> ({sd.breakdown.weights[k]}%)</span></span>
            <div className="h-2 flex-1 rounded bg-zinc-100 dark:bg-zinc-800">
              <div className="h-2 rounded bg-zinc-700 dark:bg-zinc-300" style={{ width: `${Math.max(0, Math.min(100, v))}%` }} />
            </div>
            <span className="w-8 text-right tabular-nums">{Math.round(v)}</span>
          </div>
        ))}
        {sd.breakdown.rotational_bonus > 0 && (
          <p className="text-sm text-violet-700 dark:text-violet-300">+{sd.breakdown.rotational_bonus} rotational program bonus</p>
        )}
        {(sd.breakdown.age_penalty ?? 0) > 0 && sd.explanation.posting_age && (
          <p className="text-sm text-amber-700 dark:text-amber-300">−{sd.breakdown.age_penalty} {sd.explanation.posting_age.replace(/; ranked .*$/, "").toLowerCase()} (older postings rank lower)</p>
        )}
      </div>

      {sd.hard_flags.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold">Hard requirements</h3>
          <FlagChips flags={sd.hard_flags} />
          <ul className="mt-1 space-y-1">
            {sd.hard_flags.map((f, i) => <li key={i} className="text-xs text-zinc-500">“…{f.evidence}…”</li>)}
          </ul>
        </div>
      )}

      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <Field label="Skills you have">
          {[...(ex.skills.matched ?? []), ...(ex.skills.partial ?? []).map((s) => `${s} (some)`)].join(", ") || "None found in the text"}
        </Field>
        <Field label="Skills it asks for that you don't list">
          {(ex.skills.missing ?? []).join(", ") || "None found"}
        </Field>
        <Field label="Best supporting experience">
          {ex.best_experiences.length
            ? ex.best_experiences.map((e) => `${e.name} (${e.skills.join(", ")})`).join("; ")
            : "No direct overlap found"}
        </Field>
        <Field label="Experience level">{ex.experience_fit}</Field>
        <Field label="Role fit">{ex.responsibilities}</Field>
        <Field label="Pay vs. your target">{ex.compensation}</Field>
        <Field label="Location">{ex.location}</Field>
      </dl>
      <p className="text-xs text-zinc-500">
        Scored {shortDate(sd.computed_at)} by the rule-based scorer. The tier describes fit; it is not a probability of
        an interview or offer.
      </p>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Description({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 1500;
  return (
    <div>
      <p className={cx("whitespace-pre-wrap text-sm leading-relaxed", !open && long && "max-h-96 overflow-hidden")}>{text}</p>
      {long && (
        <button onClick={() => setOpen(!open)} className="mt-2 text-sm underline">
          {open ? "Show less" : "Show full description"}
        </button>
      )}
    </div>
  );
}

function Tracker({ job, onSaved }: { job: Detail; onSaved: () => void }) {
  const [status, setStatus] = useState<Status>(job.status);
  const [appliedAt, setAppliedAt] = useState(job.applied_at?.slice(0, 10) ?? "");
  const [notes, setNotes] = useState(job.tracking.notes ?? "");
  const [resumeId, setResumeId] = useState<number | null>(job.tracking.resume_version?.id ?? null);
  const [versions, setVersions] = useState<{ id: number; label: string }[]>([]);
  const [newVersion, setNewVersion] = useState("");
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.resumeVersions().then(setVersions).catch(() => setVersions([]));
  }, []);

  async function addVersion() {
    if (!newVersion.trim()) return;
    try {
      const v = await api.addResumeVersion(newVersion.trim());
      setVersions((vs) => [...vs, v]);
      setResumeId(v.id);
      setNewVersion("");
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    }
  }

  async function save(skip?: { reasons: SkipReason[]; note: string | null }, appliedOn?: string) {
    if (!skip && !appliedOn && status === "skipped" && job.status !== "skipped") {
      setAsking(true);  // ask why first; the dialog calls save again with the answers
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      await api.setStatus(job.id, {
        status: appliedOn ? "applied" : status, applied_at: appliedOn ?? (appliedAt || null),
        resume_version_id: resumeId, notes: notes || null, ...skip,
      });
      setMessage({ ok: true, text: "Saved" });
      onSaved();
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setSaving(false);
    }
  }

  const input = "w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">My tracking</h2>
        <StatusBadge status={job.status} />
      </div>
      <label className="block text-sm">Status
        <select className={cx(input, "font-medium", STATUS_STYLE[status].chip)} value={status}
          onChange={(e) => setStatus(e.target.value as Status)}>
          {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
      </label>
      {["applied", "interviewing", "offer", "rejected"].includes(status) && (
        <label className="block text-sm">Date applied
          <input type="date" className={input} value={appliedAt} onChange={(e) => setAppliedAt(e.target.value)} />
          <span className="text-xs text-zinc-500">Leave blank to use today.</span>
        </label>
      )}
      <label className="block text-sm">Resume version (optional)
        <select className={input} value={resumeId ?? ""} onChange={(e) => setResumeId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">—</option>
          {versions.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
        </select>
      </label>
      <div className="flex gap-1">
        <input className={input} placeholder="New version label, e.g. analytics v2" value={newVersion}
          onChange={(e) => setNewVersion(e.target.value)} />
        <button onClick={addVersion} className="rounded-md border border-zinc-300 px-2 text-sm dark:border-zinc-700">Add</button>
      </div>
      <label className="block text-sm">Notes
        <textarea className={input} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      {asking && (
        <SkipReasonDialog job={`${job.title} · ${job.company}`} onCancel={() => setAsking(false)}
          onDone={(reasons, note) => { setAsking(false); save({ reasons, note }); }}
          onApplied={(on) => { setAsking(false); setStatus("applied"); setAppliedAt(on); save(undefined, on); }} />
      )}
      <button onClick={() => save()} disabled={saving}
        className="w-full rounded-md bg-pine-700 py-1.5 text-sm font-medium text-white hover:bg-pine-600 disabled:opacity-50 dark:bg-pine-300 dark:text-pine-950">
        {saving ? "Saving…" : "Save"}
      </button>
      {message && <p className={cx("text-sm", message.ok ? "text-emerald-700" : "text-red-600")}>{message.text}</p>}
      {job.tracking.events.length > 0 && (
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">History</h3>
          <ul className="mt-1 space-y-0.5 text-sm">
            {job.tracking.events.map((e, i) => (
              <li key={i}>
                {shortDate(e.at)}: {e.from ? `${STATUS_LABEL[e.from]} → ` : ""}{STATUS_LABEL[e.to]}
                {e.reasons.length > 0 && <span className="text-zinc-500"> ({e.reasons.join("; ")})</span>}
                {e.note && <span className="text-zinc-500"> — {e.note}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
