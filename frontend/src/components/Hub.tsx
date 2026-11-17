"use client";

import { useState } from "react";
import { api, daysAgo, type HubLink } from "@/lib/api";
import { Card, ErrorBox, Loading, useResource } from "@/components/ui";

// "Last opened" dates live only in this browser; they're a reminder, not data the app relies on.
const STORAGE_KEY = "hub-last-opened";

function readOpened(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function Hub() {
  const { data: links, error, reload } = useResource<HubLink[]>(api.hub);
  // Lazy initializer: localStorage is read once, in the browser (this component only renders client-side data).
  const [opened, setOpened] = useState<Record<string, string>>(() => (typeof window === "undefined" ? {} : readOpened()));

  function markOpened(url: string) {
    const next = { ...opened, [url]: new Date().toISOString() };
    setOpened(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* private window or storage blocked */
    }
  }

  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!links) return <Loading />;
  const groups = [...new Set(links.map((l) => l.group))];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Apply hub</h1>
        <p className="text-sm text-zinc-500">
          Places the app doesn&apos;t fetch (their terms or Workday sites). Check them by hand. Searches are pre-filtered
          to entry level, full time, past week. Edit the list in <code>config/apply_hub.yaml</code>.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {groups.map((g) => (
          <Card key={g}>
            <h2 className="mb-2 font-semibold">{g}</h2>
            <ul className="space-y-1.5 text-sm">
              {links.filter((l) => l.group === g).map((l) => (
                <li key={l.url}>
                  <a href={l.url} target="_blank" rel="noopener noreferrer" onClick={() => markOpened(l.url)}
                    className="font-medium hover:underline">{l.name} ↗</a>
                  {opened[l.url] && <span className="text-xs text-zinc-500"> · opened {daysAgo(opened[l.url])}</span>}
                  {l.note && <div className="text-xs text-zinc-500">{l.note}</div>}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  );
}
