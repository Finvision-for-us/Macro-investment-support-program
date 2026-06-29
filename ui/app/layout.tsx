import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

const sansFont = Plus_Jakarta_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "FinVision",
  description: "오늘의 시장 스토리 — 인과 관계 및 라이프사이클 추적",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" className={sansFont.variable}>
      <body className="min-h-screen font-sans bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 antialiased selection:bg-indigo-500/20 selection:text-indigo-900 dark:selection:text-indigo-200">
        {/* Background Mesh Gradient */}
        <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none opacity-40 dark:opacity-30">
          <div className="absolute -top-[40%] -left-[20%] w-[80%] h-[80%] rounded-full bg-gradient-to-tr from-indigo-300 to-purple-300 blur-[120px] dark:from-indigo-900/40 dark:to-purple-900/40" />
          <div className="absolute -bottom-[40%] -right-[20%] w-[80%] h-[80%] rounded-full bg-gradient-to-br from-emerald-200 to-sky-300 blur-[120px] dark:from-emerald-900/30 dark:to-sky-900/30" />
        </div>
        <div className="mx-auto max-w-3xl px-5 pb-16 pt-6 sm:px-6">{children}</div>
      </body>
    </html>
  );
}

