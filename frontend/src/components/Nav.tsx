"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cx } from "@/components/ui";

const LINKS = [
  { href: "/", label: "Jobs to Apply To" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/hub", label: "Apply hub" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav className="flex flex-wrap gap-1">
      {LINKS.map((l) => {
        const active = l.href === "/" ? path === "/" || path.startsWith("/jobs") : path.startsWith(l.href);
        return (
          <Link key={l.href} href={l.href}
            className={cx("rounded-md px-3 py-1.5 text-sm",
              active ? "bg-zinc-50 font-medium text-pine-900 dark:bg-pine-200 dark:text-pine-950"
                : "text-pine-100 hover:bg-pine-700 dark:text-pine-200 dark:hover:bg-pine-800")}>
            {l.label}
          </Link>
        );
      })}
    </nav>
  );
}
