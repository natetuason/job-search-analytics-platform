"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, shortDate, type ScoreState, type Settings } from "@/lib/api";
import { Card, cx, ErrorBox, errorText, Loading } from "@/components/ui";

const WEIGHTS = [
  ["skills", "Skills"], ["experience", "Experience level"], ["responsibilities", "Role / responsibilities"],
  ["compensation", "Pay"], ["location", "Location"],
] as const;
const ARRANGEMENTS = ["remote", "hybrid", "onsite"] as const;

const input = "w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";

function lines(v: string): string[] {
  return v.split("\n").map((s) => s.trim()).filter(Boolean);
}

type Draft = Omit<Settings, "preferred_locations" | "secondary_locations" | "excluded_locations" |
  "target_titles_primary" | "target_titles_secondary" | "excluded_companies" | "excluded_title_words"> & {
  preferred_locations: string; secondary_locations: string; excluded_locations: string;
  target_titles_primary: string; target_titles_secondary: string;
  excluded_companies: string; excluded_title_words: string;
};

function toDraft(s: Settings): Draft {
  return {
    ...s,
    preferred_locations: s.preferred_locations.join("\n"), secondary_locations: s.secondary_locations.join("\n"),
    excluded_locations: s.excluded_locations.join("\n"), target_titles_primary: s.target_titles_primary.join("\n"),
    target_titles_secondary: s.target_titles_secondary.join("\n"),
    excluded_companies: s.excluded_companies.join("\n"), excluded_title_words: s.excluded_title_words.join("\n"),
  };
}

function TriState({ value, onChange, label, yes, no }: {
  value: boolean | null; onChange: (v: boolean | null) => void; label: string; yes: string; no: string;
}) {
  return (
    <label className="block text-sm">{label}
      <select className={input} value={value === null ? "" : value ? "yes" : "no"}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "yes")}>
        <option value="">Not set</option>
        <option value="yes">{yes}</option>
        <option value="no">{no}</option>
      </select>
    </label>
  );
}

export function SettingsForm() {
  const [d, setD] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [stale, setStale] = useState(false);
  const [scoring, setScoring] = useState<ScoreState | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const startPolling = useCallback(() => {
    if (poll.current) return;
    poll.current = setInterval(async () => {
      try {
        const st = await api.scoreState();
        setScoring(st);
        if (!st.running) {
          clearInterval(poll.current!);
          poll.current = null;
          if (!st.error) setStale((await api.settings()).scores_stale);
        }
      } catch {
        /* keep polling; the next tick may reach the backend */
      }
    }, 2000);
  }, []);

  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let current = true;
    Promise.all([api.settings(), api.scoreState()]).then(([s, st]) => {
      if (!current) return;
      setD(toDraft(s));
      setStale(s.scores_stale);
      setScoring(st);
      if (st.running) startPolling();
    }, (e) => current && setError(errorText(e)));
    return () => {
      current = false;
      if (poll.current) clearInterval(poll.current);
      poll.current = null;
    };
  }, [nonce, startPolling]);

  function load() {
    setError(null);
    setNonce((n) => n + 1);
  }

  async function rescore() {
    try {
      setScoring(await api.rescore());
      startPolling();
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    }
  }

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setD((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function save() {
    if (!d) return;
    setSaving(true);
    setMessage(null);
    try {
      const s = await api.saveSettings({
        salary_min: d.salary_min, salary_preferred_min: d.salary_preferred_min,
        salary_preferred_max: d.salary_preferred_max, work_arrangements: d.work_arrangements,
        preferred_locations: lines(d.preferred_locations), secondary_locations: lines(d.secondary_locations),
        excluded_locations: lines(d.excluded_locations), secondary_location_min_score: d.secondary_location_min_score,
        open_to_relocation: d.open_to_relocation, target_titles_primary: lines(d.target_titles_primary),
        target_titles_secondary: lines(d.target_titles_secondary), excluded_companies: lines(d.excluded_companies),
        excluded_title_words: lines(d.excluded_title_words), experience_max_years: d.experience_max_years,
        scoring_weights: d.scoring_weights, graduation_date: d.graduation_date || null,
        work_authorization: d.work_authorization?.trim() || null, needs_sponsorship: d.needs_sponsorship,
      });
      setD(toDraft(s));
      setStale(s.scores_stale);
      setMessage({ ok: true, text: s.scores_stale ? "Saved. Rescore to apply the changes." : "Saved." });
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setSaving(false);
    }
  }

  if (error) return <ErrorBox error={error} onRetry={load} />;
  if (!d) return <Loading />;
  const weightSum = WEIGHTS.reduce((a, [k]) => a + (Number(d.scoring_weights[k]) || 0), 0);
  const num = (v: string) => (v === "" ? null : Number(v));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Settings</h1>
        <div className="flex items-center gap-2">
          {message && <span className={cx("text-sm", message.ok ? "text-emerald-700" : "text-red-600")}>{message.text}</span>}
          <button onClick={save} disabled={saving}
            className="rounded-md bg-pine-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-pine-600 disabled:opacity-50 dark:bg-pine-300 dark:text-pine-950">
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <Card className={cx("flex flex-wrap items-center justify-between gap-2", stale && "border-amber-400 dark:border-amber-700")}>
        <div className="text-sm">
          {scoring?.running ? "Rescoring all jobs… (about a minute)"
            : scoring?.error ? <span className="text-red-600">Last rescore failed: {scoring.error}</span>
            : stale ? "Your settings changed since jobs were last scored."
            : "Scores are up to date with these settings."}
          {scoring?.result && !scoring.running && !scoring.error && (
            <span className="text-zinc-500"> Last run {shortDate(scoring.finished_at)}: {scoring.result.scored.toLocaleString()} jobs,
              {" "}{scoring.result.by_recommendation.apply ?? 0} Apply, {scoring.result.by_recommendation.maybe ?? 0} Maybe.</span>
          )}
        </div>
        <button onClick={rescore} disabled={scoring?.running}
          className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-zinc-700">
          {scoring?.running ? "Rescoring…" : "Rescore now"}
        </button>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="space-y-3">
          <h2 className="font-semibold">About me</h2>
          <p className="text-xs text-zinc-500">Only you fill these in. Blank means unknown; the app never guesses them.</p>
          <label className="block text-sm">Work authorization
            <input className={input} value={d.work_authorization ?? ""} placeholder="e.g. U.S. citizen"
              onChange={(e) => set("work_authorization", e.target.value)} />
          </label>
          <TriState label="Need visa sponsorship now or in the future?" value={d.needs_sponsorship}
            onChange={(v) => set("needs_sponsorship", v)} yes="Yes, I need sponsorship" no="No, I don't" />
          <label className="block text-sm">Graduation date
            <input type="date" className={input} value={d.graduation_date ?? ""}
              onChange={(e) => set("graduation_date", e.target.value || null)} />
            <span className="text-xs text-zinc-500">Not used in scoring yet.</span>
          </label>
          <TriState label="Open to relocation?" value={d.open_to_relocation}
            onChange={(v) => set("open_to_relocation", v)} yes="Yes" no="No" />
          <span className="text-xs text-zinc-500">Relocation isn&apos;t used in scoring yet; use the location lists below.</span>
        </Card>

        <Card className="space-y-3">
          <h2 className="font-semibold">Pay</h2>
          <label className="block text-sm">Minimum base salary ($/yr)
            <input type="number" min={0} step={1000} className={input} value={d.salary_min}
              onChange={(e) => set("salary_min", Number(e.target.value))} />
            <span className="text-xs text-zinc-500">Postings that state a base below this are skipped. Bonus and equity don&apos;t count.</span>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block text-sm">Preferred from
              <input type="number" min={0} step={1000} className={input} value={d.salary_preferred_min ?? ""}
                onChange={(e) => set("salary_preferred_min", num(e.target.value))} />
            </label>
            <label className="block text-sm">to
              <input type="number" min={0} step={1000} className={input} value={d.salary_preferred_max ?? ""}
                onChange={(e) => set("salary_preferred_max", num(e.target.value))} />
            </label>
          </div>
          <label className="block text-sm">Max years of experience a posting can ask for
            <input type="number" min={0} max={20} step={0.5} className={input} value={d.experience_max_years}
              onChange={(e) => set("experience_max_years", Number(e.target.value))} />
          </label>
        </Card>

        <Card className="space-y-3">
          <h2 className="font-semibold">Location</h2>
          <label className="block text-sm">Preferred locations (one per line; a 2-letter state means the whole state)
            <textarea rows={3} className={input} value={d.preferred_locations}
              onChange={(e) => set("preferred_locations", e.target.value)} />
          </label>
          <label className="block text-sm">Other big cities I&apos;d consider
            <textarea rows={4} className={input} value={d.secondary_locations}
              onChange={(e) => set("secondary_locations", e.target.value)} />
          </label>
          <label className="block text-sm">…only when the match score is at least
            <input type="number" min={0} max={100} className={input} value={d.secondary_location_min_score ?? ""}
              onChange={(e) => set("secondary_location_min_score", num(e.target.value))} />
          </label>
          <label className="block text-sm">Excluded locations
            <textarea rows={2} className={input} value={d.excluded_locations}
              onChange={(e) => set("excluded_locations", e.target.value)} />
            <span className="text-xs text-zinc-500">&quot;City, ST&quot; or a 2-letter state. A job is skipped when all its locations are here.</span>
          </label>
          <fieldset className="text-sm">
            <legend>Work arrangements</legend>
            <div className="mt-1 flex gap-4">
              {ARRANGEMENTS.map((a) => (
                <label key={a} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={d.work_arrangements.includes(a)}
                    onChange={(e) => set("work_arrangements", e.target.checked
                      ? [...d.work_arrangements, a] : d.work_arrangements.filter((x) => x !== a))} />
                  {a}
                </label>
              ))}
            </div>
            <span className="text-xs text-zinc-500">Postings that don&apos;t say are kept.</span>
          </fieldset>
        </Card>

        <Card className="space-y-3">
          <h2 className="font-semibold">Target roles</h2>
          <label className="block text-sm">Primary titles (one per line)
            <textarea rows={6} className={input} value={d.target_titles_primary}
              onChange={(e) => set("target_titles_primary", e.target.value)} />
          </label>
          <label className="block text-sm">Secondary titles
            <textarea rows={5} className={input} value={d.target_titles_secondary}
              onChange={(e) => set("target_titles_secondary", e.target.value)} />
          </label>
          <label className="block text-sm">Skip titles containing these words
            <textarea rows={2} className={input} value={d.excluded_title_words}
              onChange={(e) => set("excluded_title_words", e.target.value)} />
            <span className="text-xs text-zinc-500">Usually added from your skip reasons. A target title above still wins.</span>
          </label>
          <label className="block text-sm">Hidden companies
            <textarea rows={2} className={input} value={d.excluded_companies}
              onChange={(e) => set("excluded_companies", e.target.value)} />
            <span className="text-xs text-zinc-500">Their jobs are skipped. Delete a line to bring them back.</span>
          </label>
        </Card>

        <Card className="space-y-3 md:col-span-2">
          <h2 className="font-semibold">Scoring weights</h2>
          <p className="text-xs text-zinc-500">
            How much each part counts toward the match score. They don&apos;t need to add to 100; they&apos;re used as
            proportions (currently {weightSum}).
          </p>
          <div className="grid gap-3 sm:grid-cols-5">
            {WEIGHTS.map(([k, label]) => (
              <label key={k} className="block text-sm">{label}
                <input type="number" min={0} max={100} className={input} value={d.scoring_weights[k] ?? 0}
                  onChange={(e) => set("scoring_weights", { ...d.scoring_weights, [k]: Number(e.target.value) })} />
              </label>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
