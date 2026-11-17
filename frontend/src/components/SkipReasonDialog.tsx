"use client";

import { useEffect, useRef, useState } from "react";
import { SKIP_REASON_LABEL, SKIP_REASONS, type SkipReason } from "@/lib/api";

const today = () => new Date().toLocaleDateString("en-CA");  // YYYY-MM-DD in my time zone

/** Asks why I'm skipping a job. The reasons feed the suggestions on the Dashboard ("What your skips say").
 * "I already applied" isn't a skip: it marks the job Applied instead (onApplied). */
export function SkipReasonDialog({ job, onDone, onApplied, onCancel }: {
  job?: string;
  onDone: (reasons: SkipReason[], note: string | null) => void;
  onApplied: (appliedAt: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [picked, setPicked] = useState<SkipReason[]>([]);
  const [note, setNote] = useState("");
  const [applied, setApplied] = useState(false);
  const [appliedAt, setAppliedAt] = useState(today);

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  function toggle(r: SkipReason) {
    setPicked((p) => (p.includes(r) ? p.filter((x) => x !== r) : [...p, r]));
  }

  return (
    <dialog ref={ref} onCancel={(e) => { e.preventDefault(); onCancel(); }} aria-labelledby="skip-title"
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-zinc-200 bg-white p-0 text-zinc-900 shadow-xl backdrop:bg-black/40 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100">
      <form method="dialog" className="space-y-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (applied) onApplied(appliedAt || today());
          else onDone(picked, note.trim() || null);
        }}>
        <div>
          <h2 id="skip-title" className="font-semibold">Why are you skipping this one?</h2>
          {job && <p className="text-sm text-zinc-500">{job}</p>}
          <p className="mt-1 text-xs text-zinc-500">Pick any that apply. Your answers tune which jobs show up.</p>
        </div>
        <div className="rounded-md border border-sky-200 bg-sky-50 px-2 py-1.5 text-sm dark:border-sky-900 dark:bg-sky-950/40">
          <label className="flex cursor-pointer items-center gap-2">
            <input type="checkbox" checked={applied} onChange={(e) => setApplied(e.target.checked)}
              className="size-4 accent-pine-700" />
            I already applied
          </label>
          {applied && (
            <label className="mt-1 flex items-center gap-2 pl-6 text-xs text-zinc-600 dark:text-zinc-400">
              Applied on
              <input type="date" value={appliedAt} max={today()} onChange={(e) => setAppliedAt(e.target.value)}
                className="rounded border border-zinc-300 bg-white px-1 py-0.5 dark:border-zinc-700 dark:bg-zinc-900" />
              <span>(marks it Applied, not Skipped)</span>
            </label>
          )}
        </div>
        <fieldset disabled={applied} className="space-y-1 disabled:opacity-40">
          <legend className="sr-only">Reasons</legend>
          {SKIP_REASONS.map((r) => (
            <label key={r} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800">
              <input type="checkbox" checked={picked.includes(r)} onChange={() => toggle(r)}
                className="size-4 accent-pine-700" />
              {SKIP_REASON_LABEL[r]}
            </label>
          ))}
        </fieldset>
        <label className={applied ? "hidden" : "block text-sm"}>
          <span className={picked.includes("other") ? "" : "text-zinc-500"}>
            {picked.includes("other") ? "What else?" : "Anything to add? (optional)"}
          </span>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
            className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel}
            className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm dark:border-zinc-700">Cancel</button>
          <button type="submit" autoFocus
            className="rounded-md bg-pine-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-pine-600 dark:bg-pine-300 dark:text-pine-950">
            {applied ? "Mark as applied" : picked.length || note.trim() ? "Skip it" : "Skip without a reason"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
