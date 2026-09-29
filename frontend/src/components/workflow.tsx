import { useQuery } from "@tanstack/react-query";
import { Ban, Check, CircleAlert, Info, LoaderCircle, TriangleAlert, X } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { cn } from "../lib/cn";
import { fmtTime } from "../lib/format";
import type { AuditEvent, Stage } from "../types";

export function PipelineStepper({ stages, compact }: { stages: Stage[]; compact?: boolean }) {
  return (
    <ol className="flex w-full items-start">
      {stages.map((s, i) => {
        const next = stages[i + 1];
        return (
          <li key={s.key} className="relative flex flex-1 flex-col items-center text-center">
            {next && (
              <span
                className={cn("absolute top-[17px] left-1/2 h-px w-full",
                  s.state === "done" && next.state !== "pending" && next.state !== "disabled" ? "bg-gold-deep" : "bg-line")}
              />
            )}
            <StepDot state={s.state} index={i + 1} />
            {!compact && (
              <>
                <div className={cn("mt-2.5 text-[13px] font-medium",
                  s.state === "active" ? "text-gold" : s.state === "pending" || s.state === "disabled" ? "text-muted" : "text-paper")}>
                  {i + 1}. {s.label}
                </div>
                <div className="mt-0.5 text-[11.5px] text-muted">
                  {s.state === "active" ? "Processing…" : s.state === "disabled" ? "Dry run" : s.state === "review" ? "Review" : s.state === "failed" ? "Failed" : s.sublabel}
                </div>
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepDot({ state, index }: { state: Stage["state"]; index: number }) {
  const base = "relative z-10 grid size-9 place-items-center rounded-full border-2";
  switch (state) {
    case "done":
      return <span className={cn(base, "border-gold-deep bg-gold-ink text-gold")}><Check className="size-4" strokeWidth={2.6} /></span>;
    case "active":
      return (
        <span className={cn(base, "pulse-ring border-gold-bright bg-gold text-ink-950")}>
          <LoaderCircle className="size-4 animate-spin" strokeWidth={2.6} />
        </span>
      );
    case "failed":
      return <span className={cn(base, "border-err bg-err-ink text-err")}><X className="size-4" strokeWidth={2.6} /></span>;
    case "review":
      return <span className={cn(base, "border-warn bg-warn-ink text-warn")}><TriangleAlert className="size-4" /></span>;
    case "disabled":
      return <span className={cn(base, "border-line bg-ink-800 text-dim")}><Ban className="size-3.5" /></span>;
    default:
      return <span className={cn(base, "tabular border-line bg-ink-850 text-xs text-muted")}>{index}</span>;
  }
}

export function EventIcon({ level, type }: { level: AuditEvent["level"]; type: string }) {
  if (level === "ERROR") return <CircleAlert className="size-4 text-err" />;
  if (level === "WARN") return <TriangleAlert className="size-4 text-warn" />;
  if (type.endsWith("_started") || type === "file_discovered") return <span className="grid size-4 place-items-center"><span className="size-2 rounded-full bg-gold" /></span>;
  if (type === "system_started" || type === "recovery") return <Info className="size-4 text-muted" />;
  return <Check className="size-4 text-ok" strokeWidth={2.5} />;
}

export function ActivityFeed({ events, showProduct = true, highlightLast }: { events: AuditEvent[]; showProduct?: boolean; highlightLast?: boolean }) {
  return (
    <ul className="divide-y divide-line-soft">
      {events.map((e, i) => (
        <li key={e.id}
          className={cn("flex items-start gap-3 px-2 py-2 text-[13px]",
            highlightLast && i === events.length - 1 && "rounded-md bg-gold-ink/70")}>
          <span className="tabular w-[62px] shrink-0 pt-px text-xs text-muted">{fmtTime(e.timestamp)}</span>
          <span className="pt-0.5"><EventIcon level={e.level} type={e.event_type} /></span>
          <span className={cn("min-w-0 flex-1 leading-snug", e.level === "ERROR" ? "text-err" : e.level === "WARN" ? "text-warn" : "text-cream")}>
            {e.message}
          </span>
          {showProduct && e.product_code && e.product_id && (
            <Link to={`/products/${e.product_id}`} className="tabular shrink-0 font-mono text-[11px] text-gold-deep hover:text-gold">
              {e.product_code}
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

export function DryRunBanner({ className }: { className?: string }) {
  const { data } = useQuery({ queryKey: ["status"], queryFn: api.status });
  if (!data || data.generation_enabled) return null;
  return (
    <div className={cn("flex items-center gap-3 rounded-xl border border-[#4a3920] bg-warn-ink px-4 py-3 text-[13px]", className)}>
      <TriangleAlert className="size-4 shrink-0 text-warn" />
      <div className="flex-1">
        <span className="font-semibold tracking-wide text-warn">DRY RUN — IMAGE GENERATION DISABLED.</span>{" "}
        <span className="text-cream/80">{data.dry_run_reason}. Products are ingested, grouped, analysed, numbered and named; no images are generated and no source files are moved.</span>
      </div>
      <Link to="/settings" className="shrink-0 text-xs font-medium text-gold hover:text-gold-bright">Settings →</Link>
    </div>
  );
}
