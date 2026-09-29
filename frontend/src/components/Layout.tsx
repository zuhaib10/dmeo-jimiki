import { useQuery } from "@tanstack/react-query";
import {
  CircleCheck, Images, LayoutDashboard, ListOrdered, Package, ScrollText, Search, Settings, ShieldAlert, UserRound,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useLive } from "../hooks/useLive";
import { cn } from "../lib/cn";
import { fmtBytes } from "../lib/format";
import { Dot, ProgressBar } from "./ui";

export function LotusMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 44" className={className} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round">
      <path d="M32 4c6 7 6 20 0 30-6-10-6-23 0-30z" />
      <path d="M32 34c-2-10-9-18-19-20 1 11 8 19 19 20z" />
      <path d="M32 34c2-10 9-18 19-20-1 11-8 19-19 20z" />
      <path d="M32 34C27 27 18 23 6 24c4 7 13 11 26 10z" />
      <path d="M32 34c5-7 14-11 26-10-4 7-13 11-26 10z" />
      <path d="M10 40h44" />
    </svg>
  );
}

interface NavItem { to: string; label: string; icon: ReactNode; badge?: number; tone?: "gold" | "warn"; end?: boolean }

function Sidebar() {
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 30000 });
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 10000 });
  const online = !!status && status.api.ok && status.database.ok;

  const items: NavItem[] = [
    { to: "/", label: "Overview", icon: <LayoutDashboard />, end: true },
    { to: "/products", label: "Product Index", icon: <Package /> },
    { to: "/queue", label: "Processing Queue", icon: <ListOrdered />, badge: stats?.processing },
    { to: "/studio", label: "Image Studio", icon: <Images /> },
    { to: "/review", label: "Review Required", icon: <ShieldAlert />, badge: stats?.review_required, tone: "warn" },
    { to: "/completed", label: "Completed Products", icon: <CircleCheck />, badge: stats?.products_processed },
    { to: "/references", label: "Reference Models", icon: <UserRound /> },
    { to: "/logs", label: "Logs", icon: <ScrollText /> },
    { to: "/settings", label: "Settings", icon: <Settings /> },
  ];

  return (
    <aside className="flex w-[264px] shrink-0 flex-col border-r border-line-soft bg-ink-925">
      <div className="flex items-center gap-3 px-6 pt-6 pb-5">
        <LotusMark className="h-8 w-11 text-gold" />
        <div className="leading-none">
          <div className="text-[22px] font-light tracking-[0.2em] text-gold">JIMIKI</div>
          <div className="mt-1 text-[9.5px] font-medium tracking-[0.34em] text-gold-deep">IMAGE STUDIO</div>
        </div>
      </div>

      <div className="mx-5 mb-3 flex items-center gap-2 rounded-lg border border-line-soft bg-ink-900 px-3 py-2 text-[11px] font-medium tracking-[0.14em]">
        <Dot ok={status ? online : null} pulse />
        <span className={online ? "text-ok" : "text-err"}>{status ? (online ? "SYSTEM ONLINE" : "SYSTEM DEGRADED") : "CONNECTING…"}</span>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
        {items.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.end}
            className={({ isActive }) =>
              cn(
                "group flex h-11 items-center gap-3 rounded-lg px-3 text-[14px] transition-colors [&_svg]:size-[18px] [&_svg]:shrink-0",
                isActive ? "bg-gold-ink text-paper [&_svg]:text-gold" : "text-cream/80 hover:bg-ink-850 hover:text-paper [&_svg]:text-muted",
              )
            }
          >
            {it.icon}
            <span className="flex-1 whitespace-nowrap">{it.label}</span>
            {!!it.badge && (
              <span className={cn("tabular min-w-6 rounded-full px-1.5 py-0.5 text-center text-[11px] font-semibold",
                it.tone === "warn" ? "bg-warn-ink text-warn" : "bg-gold-ink text-gold")}>
                {it.badge}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="space-y-3 p-4">
        <div className="rounded-xl border border-line-soft bg-ink-900 p-3.5">
          <div className="mb-2.5 text-[11px] font-medium tracking-[0.12em] text-muted">SERVICES</div>
          {[
            ["API", status?.api.ok ?? null],
            ["Database", status?.database.ok ?? null],
            ["Watcher", status?.watcher.ok ?? null],
          ].map(([label, ok]) => (
            <div key={String(label)} className="flex items-center justify-between py-1 text-[12.5px]">
              <span className="text-cream/80">{label}</span>
              <span className="flex items-center gap-2 text-muted">
                <Dot ok={ok as boolean | null} />
                {ok === null ? "…" : ok ? "Online" : "Offline"}
              </span>
            </div>
          ))}
        </div>
        {status?.storage && (
          <div className="rounded-xl border border-line-soft bg-ink-900 p-3.5">
            <div className="mb-2 text-[12.5px] font-medium text-cream">Storage Usage</div>
            <ProgressBar value={status.storage.percent} />
            <div className="tabular mt-2 flex justify-between text-[11.5px] text-muted">
              <span>{fmtBytes(status.storage.used)} of {fmtBytes(status.storage.total)}</span>
              <span>{status.storage.percent}%</span>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function TopBar() {
  const nav = useNavigate();
  const ref = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 10000 });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        ref.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const running = status && !status.paused;
  const live = status?.generation_enabled;
  return (
    <header className="flex h-[72px] shrink-0 items-center gap-5 border-b border-line-soft px-6">
      <form
        className="relative max-w-[860px] flex-1"
        onSubmit={(e) => {
          e.preventDefault();
          nav(`/products?q=${encodeURIComponent(q.trim())}`);
        }}
      >
        <Search className="pointer-events-none absolute top-1/2 left-4 size-[18px] -translate-y-1/2 text-muted" />
        <input
          ref={ref}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search product ID, number or name..."
          className="h-11 w-full rounded-xl border border-line bg-ink-850 pr-20 pl-11 text-sm text-cream placeholder:text-dim focus:border-gold-deep focus:outline-none"
        />
        <kbd className="absolute top-1/2 right-3 -translate-y-1/2 rounded-md border border-line bg-ink-800 px-2 py-0.5 text-[11px] text-muted">Ctrl K</kbd>
      </form>
      <div className="ml-auto flex items-center gap-5">
        {status && (
          <span className={cn("rounded-md border px-2 py-1 text-[10.5px] font-semibold tracking-[0.12em]",
            live ? "border-[#2b4430] bg-ok-ink text-ok" : "border-[#4a3920] bg-warn-ink text-warn")}>
            {live ? "LIVE" : "DRY RUN"}
          </span>
        )}
        <span className="flex items-center gap-2 text-[13px]">
          <Dot ok={status ? !!running : null} pulse />
          <span className={running ? "text-ok" : "text-warn"}>{status ? (running ? "Running" : "Paused") : "…"}</span>
        </span>
        <NavLink to="/settings" className="text-muted hover:text-paper"><Settings className="size-[18px]" /></NavLink>
        <div className="flex items-center gap-3 border-l border-line-soft pl-5">
          <div className="grid size-9 place-items-center rounded-full border border-line bg-ink-800 text-sm font-medium text-cream">
            {(status?.operator ?? "S").charAt(0).toUpperCase()}
          </div>
          <div className="leading-tight">
            <div className="text-[13px] font-medium text-paper">{status?.operator ?? "—"}</div>
            <div className="text-[11.5px] text-muted">Local workstation</div>
          </div>
        </div>
      </div>
    </header>
  );
}

export function Layout() {
  const { connected } = useLive();
  return (
    <div className="flex h-full min-w-[1180px]">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        {!connected && (
          <div className="border-b border-[#4a3920] bg-warn-ink px-6 py-1.5 text-xs text-warn">Live updates reconnecting…</div>
        )}
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
