"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, type SkipFeedback, type SkipSuggestion } from "@/lib/api";
import { Card, cx, errorText, useResource } from "@/components/ui";

const HIDDEN_KEY = "skip-suggestions-not-now";

function readHidden(): string[] {
  try {
    return JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? "[]");
  } catch {
    return [];
  }
}

/** Why I've skipped jobs, and the Settings changes those reasons suggest. Nothing changes until I click Apply. */
export function SkipFeedbackCard() {
  const { data, reload } = useResource<SkipFeedback>(api.skipFeedback);
  const [hidden, setHidden] = useState<string[]>(readHidden);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  if (!data) return null;
  const counted = data.reasons.filter((r) => r.count > 0).sort((a, b) => b.count - a.count);
  const shown = data.suggestions.filter((s) => !hidden.includes(s.id));

  function notNow(id: string) {
    const next = [...hidden, id];
    setHidden(next);
    try {
      localStorage.setItem(HIDDEN_KEY, JSON.stringify(next));
    } catch {
      // private window or blocked storage: it just shows again next time
    }
  }

  async function apply(s: SkipSuggestion) {
    setBusy(s.id);
    setMessage(null);
    try {
      await api.saveSettings(s.change);
      await api.rescore().catch((e) => {
        if (!(e instanceof ApiError && e.status === 409)) throw e;  // already rescoring
      });
      setMessage({ ok: true, text: `Done: ${s.text.toLowerCase()}. Rescoring now (about a minute); undo it in Settings.` });
      reload();
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="space-y-3">
      <div>
        <h2 className="font-semibold">What your skips say</h2>
        <p className="text-sm text-zinc-500">
          {data.with_reasons === 0
            ? "When you mark a job Skipped, pick why. Patterns show up here as suggested filter changes."
            : `${data.with_reasons} of ${data.skipped} skipped jobs have a reason.`}
        </p>
      </div>

      {counted.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 text-sm">
          {counted.map((r) => (
            <li key={r.reason} className="rounded-md bg-zinc-100 px-2 py-0.5 dark:bg-zinc-800">
              {r.label} <span className="font-semibold tabular-nums">{r.count}</span>
            </li>
          ))}
        </ul>
      )}

      {shown.length > 0 && (
        <ul className="space-y-2">
          {shown.map((s) => (
            <li key={s.id} className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-pine-200 p-2 dark:border-pine-900">
              <div className="min-w-0 text-sm">
                <p className="font-medium">{s.text}</p>
                <p className="text-xs text-zinc-500">{s.evidence.join(" · ")}</p>
              </div>
              <div className="flex gap-1">
                <button onClick={() => notNow(s.id)} className="rounded-md border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
                  Not now
                </button>
                <button onClick={() => apply(s)} disabled={busy !== null}
                  className="rounded-md bg-pine-700 px-2 py-1 text-xs font-medium text-white hover:bg-pine-600 disabled:opacity-50 dark:bg-pine-300 dark:text-pine-950">
                  {busy === s.id ? "Applying…" : "Apply"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {message && <p className={cx("text-sm", message.ok ? "text-emerald-700" : "text-red-600")}>{message.text}</p>}

      {data.notes.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-zinc-500">Your skip notes ({data.notes.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {data.notes.map((n) => (
              <li key={n.job_id}>
                <Link href={`/jobs/${n.job_id}`} className="hover:underline">{n.title}</Link>
                <span className="text-zinc-500"> · {n.company}: {n.note}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
