"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { getToken, signOut } from "@/lib/auth";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Button, cx, Spinner } from "@/lib/ui";

const NAV = [
  ["/", "Panel"], ["/importy", "Importy"], ["/produkty", "Moje produkty"], ["/marketplace", "Marketplace"],
  ["/szablony", "Szablony"], ["/mapowania", "Mapowania"], ["/bledy", "Centrum błędów"], ["/eksporty", "Eksporty"],
  ["/ustawienia", "Ustawienia"],
] as const;

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    void getToken().then((t) => (t ? setReady(true) : router.replace("/login")));
  }, [router]);
  useEffect(() => setOpen(false), [pathname]);

  const me = useApi<Rec>(ready ? "/me" : null);
  if (!ready) return <div className="p-8"><Spinner /></div>;

  const active = (href: string) => (href === "/" ? pathname === "/" || pathname.startsWith("/kreator") : pathname.startsWith(href));

  return (
    <div className="min-h-screen lg:flex">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
        <span className="font-semibold">Marketplace Data Bridge</span>
        <Button variant="secondary" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>Menu</Button>
      </header>
      <aside className={cx("w-full shrink-0 border-b border-slate-200 bg-white lg:block lg:min-h-screen lg:w-60 lg:border-b-0 lg:border-r",
        open ? "block" : "hidden")}>
        <div className="hidden px-5 py-5 lg:block">
          <div className="text-sm font-semibold text-slate-900">Marketplace Data Bridge</div>
          <div className="text-xs text-slate-500">Bez AI · reguły i słowniki</div>
        </div>
        <nav className="space-y-0.5 px-3 py-2" aria-label="Menu główne">
          {NAV.map(([href, label]) => (
            <Link key={href} href={href} aria-current={active(href) ? "page" : undefined}
              className={cx("block rounded-md px-3 py-2 text-sm", active(href)
                ? "bg-indigo-50 font-medium text-indigo-800" : "text-slate-700 hover:bg-slate-100")}>
              {label}
            </Link>
          ))}
        </nav>
        <div className="mt-4 border-t border-slate-100 px-5 py-4 text-xs text-slate-600">
          {me.data && (
            <>
              <div className="font-medium text-slate-800">{me.data.organization.name}</div>
              <div className="truncate">{me.data.email}</div>
            </>
          )}
          <button className="mt-2 text-indigo-700 hover:underline"
            onClick={() => void signOut().then(() => router.replace("/login"))}>Wyloguj</button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 lg:px-8">{children}</main>
    </div>
  );
}
