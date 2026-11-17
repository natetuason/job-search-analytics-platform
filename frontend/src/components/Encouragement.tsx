"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

// Lines come from config/encouragement.yaml plus data/private/encouragement.yaml. Click for another one.
const FALLBACK = ["Every no brings you closer to a yes."];

function pick(count: number, exclude?: number): number {
  let i = Math.floor(Math.random() * count);
  if (count > 1 && i === exclude) i = (i + 1) % count;
  return i;
}

export default function Encouragement() {
  const [lines, setLines] = useState<string[] | null>(null);
  const [i, setI] = useState(0);

  useEffect(() => {
    let current = true;
    api.encouragement().then(
      (l) => current && (setLines(l.length ? l : FALLBACK), setI(pick(l.length || 1))),
      () => current && setLines(FALLBACK),
    );
    return () => {
      current = false;
    };
  }, []);

  if (!lines) return <div className="h-7" />;
  return (
    <button onClick={() => setI(pick(lines.length, i))} title="Another one"
      className="w-full text-left font-serif text-base italic leading-snug text-pine-800 sm:text-lg dark:text-pine-200">
      “{lines[i]}”
    </button>
  );
}
