import type { Metadata } from "next";
import { Fraunces, Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import { EncouragementSlot } from "@/components/EncouragementSlot";
import { Nav } from "@/components/Nav";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const fraunces = Fraunces({ variable: "--font-fraunces", subsets: ["latin"], style: ["italic"] });

export const metadata: Metadata = {
  title: "Job Search",
  description: "Ranked jobs to apply to, tracking, and the apply hub",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${fraunces.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <header className="bg-pine-900 text-zinc-50 dark:bg-pine-950">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <span className="font-serif text-lg italic">Job Search</span>
            <Suspense><Nav /></Suspense>
          </div>
        </header>
        <div className="border-b border-pine-100 bg-pine-50 dark:border-pine-900 dark:bg-pine-950/40">
          <div className="mx-auto max-w-6xl px-4 py-3">
            <EncouragementSlot />
          </div>
        </div>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
