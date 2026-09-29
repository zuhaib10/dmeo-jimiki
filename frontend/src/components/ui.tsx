import { ImageOff, LoaderCircle, X } from "lucide-react";
import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "../lib/cn";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-xl border border-line bg-ink-850", className)}>{children}</section>;
}

export function CardHeader({ title, right, sub, className }: { title: ReactNode; right?: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-5 pt-4 pb-3", className)}>
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold text-paper">{title}</h2>
        {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
export function Button({
  variant = "secondary", size = "md", loading, className, children, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  const styles: Record<Variant, string> = {
    primary: "bg-gold text-ink-950 hover:bg-gold-bright border-gold",
    secondary: "bg-ink-800 text-cream hover:bg-ink-750 border-line",
    ghost: "bg-transparent text-muted hover:text-cream hover:bg-ink-800 border-transparent",
    danger: "bg-err-deep text-paper hover:bg-[#a33833] border-err-deep",
    success: "bg-ok-ink text-ok hover:bg-[#1d2e21] border-[#2c4631]",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-8 px-3 text-xs" : "h-10 px-4 text-[13px]",
        styles[variant],
        className,
      )}
    >
      {loading && <LoaderCircle className="size-3.5 animate-spin" />}
      {children}
    </button>
  );
}

const CHIP: Record<string, string> = {
  COMPLETED: "text-ok bg-ok-ink border-[#2b4430]",
  PASSED: "text-ok bg-ok-ink border-[#2b4430]",
  REVIEW_REQUIRED: "text-warn bg-warn-ink border-[#4a3920]",
  PARTIAL: "text-warn bg-warn-ink border-[#4a3920]",
  FAILED: "text-err bg-err-ink border-[#4b2522]",
  REJECTED: "text-err bg-err-ink border-[#4b2522]",
  QUEUED: "text-muted bg-ink-800 border-line",
  PENDING: "text-muted bg-ink-800 border-line",
  DISCOVERED: "text-muted bg-ink-800 border-line",
  DUPLICATE: "text-dim bg-ink-800 border-line",
  ARCHIVED: "text-ok bg-ok-ink border-[#2b4430]",
  GROUPED: "text-cream bg-ink-800 border-line",
  GENERATED: "text-gold bg-gold-ink border-[#4a3f28]",
};
const ACTIVE = new Set(["ANALYZING", "GROUPING", "NAMING", "GENERATING_WHITE", "GENERATING_MODEL", "GENERATING_CLOSEUP", "VALIDATING", "GENERATING", "RUNNING", "REGISTERED", "ANALYZED"]);

export function StatusChip({ status, label, className }: { status: string; label?: string; className?: string }) {
  const active = ACTIVE.has(status);
  const style = CHIP[status] ?? (active ? "text-gold bg-gold-ink border-[#4a3f28]" : "text-muted bg-ink-800 border-line");
  return (
    <span className={cn("inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 text-[11px] font-medium tracking-wide", style, className)}>
      {active && <LoaderCircle className="size-3 animate-spin" />}
      {label ?? status.replaceAll("_", " ")}
    </span>
  );
}

export function ProgressBar({ value, className, tone = "gold" }: { value: number; className?: string; tone?: "gold" | "ok" | "warn" | "err" }) {
  const color = { gold: "bg-gold", ok: "bg-ok", warn: "bg-warn", err: "bg-err" }[tone];
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-ink-750", className)}>
      <div className={cn("h-full rounded-full transition-[width] duration-700", color)} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function Dot({ ok, pulse }: { ok: boolean | null; pulse?: boolean }) {
  return (
    <span className={cn("inline-block size-2 rounded-full", ok === null ? "bg-dim" : ok ? "bg-ok" : "bg-err", pulse && ok && "animate-pulse")} />
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="mb-1 grid size-11 place-items-center rounded-full border border-line bg-ink-800 text-gold">{icon}</div>
      <div className="text-sm font-medium text-cream">{title}</div>
      {children && <div className="max-w-md text-xs leading-relaxed text-muted">{children}</div>}
    </div>
  );
}

export function Img({ src, alt, className, fit = "cover" }: { src: string | null | undefined; alt: string; className?: string; fit?: "cover" | "contain" }) {
  const [state, setState] = useState<"loading" | "ok" | "err">(src ? "loading" : "err");
  useEffect(() => setState(src ? "loading" : "err"), [src]);
  return (
    <div className={cn("relative overflow-hidden bg-ink-800", className)}>
      {state === "loading" && <div className="shimmer absolute inset-0" />}
      {state === "err" ? (
        <div className="absolute inset-0 grid place-items-center text-dim"><ImageOff className="size-5" /></div>
      ) : (
        <img
          src={src ?? undefined}
          alt={alt}
          loading="lazy"
          onLoad={() => setState("ok")}
          onError={() => setState("err")}
          className={cn("size-full transition-opacity duration-300", fit === "cover" ? "object-cover" : "object-contain", state === "ok" ? "opacity-100" : "opacity-0")}
        />
      )}
    </div>
  );
}

export function Lightbox({ src, title, sub, onClose }: { src: string | null; title?: string; sub?: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!src) return null;
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/85 backdrop-blur-sm" onClick={onClose}>
      <div className="flex items-center justify-between px-6 py-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <div className="text-sm font-medium text-paper">{title}</div>
          {sub && <div className="text-xs text-muted">{sub}</div>}
        </div>
        <button onClick={onClose} className="grid size-9 place-items-center rounded-lg border border-line bg-ink-850 text-muted hover:text-paper">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center p-6 pt-0">
        <img src={src} alt={title} className="max-h-full max-w-full rounded-lg object-contain shadow-2xl" onClick={(e) => e.stopPropagation()} />
      </div>
    </div>
  );
}

export function KeyValue({ k, v, mono }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[42%_58%] items-start gap-2 py-[7px] text-[13px]">
      <div className="text-muted">{k}</div>
      <div className={cn("min-w-0 break-words text-cream", mono && "font-mono text-xs")}>{v ?? "—"}</div>
    </div>
  );
}

export function PageHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-paper">{title}</h1>
        {sub && <p className="mt-1 text-[13px] text-muted">{sub}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}

export function Confidence({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-dim">—</span>;
  const tone = value >= 0.8 ? "text-ok" : value >= 0.6 ? "text-warn" : "text-err";
  return <span className={cn("tabular font-medium", tone)}>{Math.round(value * 100)}%</span>;
}
