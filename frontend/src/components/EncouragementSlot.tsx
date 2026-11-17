"use client";

import dynamic from "next/dynamic";

// Rendered only in the browser so each visit gets a fresh random line (no server/client mismatch).
const Encouragement = dynamic(() => import("@/components/Encouragement"), {
  ssr: false,
  loading: () => <div className="h-7" />,
});

export function EncouragementSlot() {
  return <Encouragement />;
}
