// Types and fetch helpers for the FastAPI backend (proxied at /api by next.config.ts).

export const STATUSES = [
  "new", "shortlisted", "applied", "interviewing", "offer", "rejected", "skipped", "expired",
] as const;
export type Status = (typeof STATUSES)[number];
export type Recommendation = "apply" | "maybe" | "skip";
export type Tier = "strong_fit" | "competitive" | "reach";
export type LocationClass = "preferred" | "remote" | "secondary" | "other" | "foreign";

export type Pay =
  | { kind: "posted"; min: number; max: number | null; period: "year" | "hour" | null }
  | { kind: "estimate"; min: number; max: number | null; period: "year" | "hour" | null; confidence: string };

export type Flag = { label: string; status: "ok" | "blocks" | "needs_your_check" | "check" };

export type JobRow = {
  id: number;
  other_locations: number; // same company + title posted elsewhere (collapsed in the list)
  title: string;
  company: string;
  location: string | null;
  location_class: LocationClass | null;
  work_arrangement: string;
  score: number;
  tier: Tier;
  recommendation: Recommendation;
  summary: string | null;
  pay: Pay | null;
  flags: Flag[];
  rotational: boolean;
  early_career: boolean;
  status: Status;
  applied_at: string | null;
  posted_at: string | null;
  first_seen_at: string;
  apply_url: string;
  is_active: boolean;
  closed_at: string | null;
  is_new: boolean; // first found in the last 2 days
};

export type Explanation = {
  summary: string;
  skills: { matched?: string[]; partial?: string[]; missing?: string[]; note?: string };
  best_experiences: { name: string; skills: string[] }[];
  experience_fit: string;
  responsibilities: string;
  compensation: string;
  location: string;
  rotational: boolean;
  posting_age?: string | null;
  reasons: string[];
};

export type JobDetail = JobRow & {
  is_active: boolean;
  description: string | null;
  employment_type: string;
  experience: { min_years: number | null; max_years: number | null; text: string | null };
  pay_estimate_evidence: Record<string, unknown> | null;
  score_detail: {
    breakdown: { categories: Record<string, number>; weights: Record<string, number>; rotational_bonus: number; age_penalty?: number };
    explanation: Explanation;
    hard_flags: (Flag & { type: string; evidence: string })[];
    computed_at: string;
  } | null;
  tracking: {
    notes: string | null;
    resume_version: { id: number; label: string } | null;
    events: { from: Status | null; to: Status; at: string; note: string | null; reasons: string[] }[];
  };
  sources: { kind: string; ref: string | null; url: string | null; first_seen_at: string }[];
};

export type Settings = {
  salary_min: number;
  salary_preferred_min: number | null;
  salary_preferred_max: number | null;
  work_arrangements: string[];
  preferred_locations: string[];
  excluded_locations: string[];
  excluded_companies: string[];
  excluded_title_words: string[];
  secondary_locations: string[];
  secondary_location_min_score: number | null;
  open_to_relocation: boolean | null;
  target_titles_primary: string[];
  target_titles_secondary: string[];
  experience_max_years: number;
  scoring_weights: Record<string, number>;
  search_frequency_hours: number;
  graduation_date: string | null;
  work_authorization: string | null;
  needs_sponsorship: boolean | null;
  version: number;
  scores_stale: boolean;
};

export const SKIP_REASONS = ["experience", "pay", "location", "arrangement", "role", "company", "other"] as const;
export type SkipReason = (typeof SKIP_REASONS)[number];

export type SkipSuggestion = {
  id: string;
  reason: SkipReason;
  text: string;
  evidence: string[];
  change: Partial<Settings>;
};

export type SkipFeedback = {
  skipped: number;
  with_reasons: number;
  reasons: { reason: SkipReason; label: string; count: number }[];
  notes: { job_id: number; title: string; company: string; note: string }[];
  suggestions: SkipSuggestion[];
};

export type ScoreState = {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  result: { scored: number; pay_parsed: number; estimated: number; by_recommendation: Record<string, number> } | null;
  error: string | null;
};

export type Dashboard = {
  active_jobs: number;
  by_recommendation: Record<Recommendation, number>;
  by_tier: Record<Tier, number>;
  by_status: Record<string, number>;
  new_apply_this_week: number;
  applied_this_week: number;
  recent_activity: { job_id: number; title: string; company: string; status: Status; updated_at: string }[];
  sources: { kind: string; last_success: string | null; runs: number }[];
  failed_runs_this_week: number;
  scored_at: string | null;
};

export type UpdateRun = {
  id: number;
  trigger: "schedule" | "manual";
  status: "running" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  sources_ok: number;
  sources_failed: number;
  new_jobs: number;
  closed_jobs: number;
  new_recommended: number;
  error: string | null;
};

export type Updates = {
  running: boolean;
  next_due_at: string;
  history: UpdateRun[];
  digest: { run: UpdateRun; new_jobs: JobRow[]; closed_tracked: JobRow[] } | null;
};

export type HubLink = { group: string; name: string; url: string; note: string | null; checked: string | null };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Can't reach the backend. Is it running on port 8000?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail
        : Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : detail;
    } catch {
      // A non-JSON 5xx comes from the Next.js proxy when the backend is down.
      if (res.status >= 500) detail = "Can't reach the backend. Is it running on port 8000?";
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  jobs: (params: URLSearchParams) => request<{ total: number; items: JobRow[] }>(`/jobs?${params}`),
  job: (id: number | string) => request<JobDetail>(`/jobs/${id}`),
  setStatus: (id: number, body: { status: Status; applied_at?: string | null; resume_version_id?: number | null;
    notes?: string | null; note?: string | null; reasons?: SkipReason[] }) =>
    request<{ job_id: number; status: Status; applied_at: string | null }>(`/jobs/${id}/status`,
      { method: "PUT", body: JSON.stringify(body) }),
  resumeVersions: () => request<{ id: number; label: string }[]>("/resume-versions"),
  addResumeVersion: (label: string) =>
    request<{ id: number; label: string }>("/resume-versions", { method: "POST", body: JSON.stringify({ label }) }),
  settings: () => request<Settings>("/settings"),
  saveSettings: (body: Partial<Settings>) =>
    request<Settings>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  scoreState: () => request<ScoreState>("/score"),
  rescore: () => request<ScoreState>("/score", { method: "POST" }),
  dashboard: () => request<Dashboard>("/dashboard"),
  hub: () => request<HubLink[]>("/hub"),
  encouragement: () => request<string[]>("/encouragement"),
  updates: () => request<Updates>("/updates"),
  skipFeedback: () => request<SkipFeedback>("/skip-feedback"),
  startUpdate: () => request<{ started: boolean }>("/updates", { method: "POST" }),
};

// ---------- formatting ----------

const money = (n: number, period: string | null) =>
  period === "hour" ? `$${n.toFixed(n % 1 ? 2 : 0)}` : `$${Math.round(n / 1000)}k`;

export function payText(pay: Pay | null): string {
  if (!pay) return "Pay not posted";
  const unit = pay.period === "hour" ? "/hr" : "";
  const range = pay.max && pay.max !== pay.min
    ? `${money(pay.min, pay.period)}–${money(pay.max, pay.period)}` : money(pay.min, pay.period);
  return `${range}${unit}`;
}

export const TIER_LABEL: Record<Tier, string> = { strong_fit: "Strong fit", competitive: "Competitive", reach: "Reach" };
export const REC_LABEL: Record<Recommendation, string> = { apply: "Apply", maybe: "Maybe", skip: "Skip" };
export const STATUS_LABEL: Record<Status, string> = {
  new: "New", shortlisted: "Shortlisted", applied: "Applied", interviewing: "Interviewing", offer: "Offer",
  rejected: "Rejected", skipped: "Skipped", expired: "Expired",
};
// Same wording as the backend (app/feedback.py).
export const SKIP_REASON_LABEL: Record<SkipReason, string> = {
  experience: "Asks for more experience than I have",
  pay: "Pay is too low",
  location: "Location or commute",
  arrangement: "On-site / hybrid / remote setup",
  role: "Not the type of role I want",
  company: "Company or culture fit",
  other: "Something else",
};
export const LOCATION_LABEL: Record<LocationClass, string> = {
  preferred: "NYC / NJ", remote: "Remote (U.S.)", secondary: "Other big city", other: "Elsewhere in U.S.",
  foreign: "Outside U.S.",
};

export function shortDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `${hours} hr ago` : daysAgo(iso);
}

export function timeUntil(iso: string): string {
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (mins <= 5) return "within a few minutes";
  if (mins < 60) return `in ${mins} min`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `in about ${hours} hr` : `in ${Math.round(hours / 24)} days`;
}

export function daysAgo(iso: string | null): string {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days <= 0 ? "today" : days === 1 ? "1 day ago" : `${days} days ago`;
}
