import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight, CircleCheck, Flag, FolderOpen, Hash, LoaderCircle, Maximize2, Pause, Play, RotateCw, Square,
  ThumbsDown, ThumbsUp, TriangleAlert,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Button, Card, CardHeader, Confidence, Empty, Img, KeyValue, Lightbox, ProgressBar, StatusChip } from "../components/ui";
import { ActivityFeed, DryRunBanner, PipelineStepper } from "../components/workflow";
import { cn } from "../lib/cn";
import { cap, fmtBytes, fmtDateTime, fmtTime, pct } from "../lib/format";
import type { GeneratedImage, ProductDetail as PD, SourceImage } from "../types";

type LB = { src: string; title: string; sub?: ReactNode } | null;

export default function ProductDetail() {
  const { id } = useParams();
  const { data: p, isLoading, error } = useQuery({
    queryKey: ["product", id],
    queryFn: () => api.product(id!),
    refetchInterval: 8000,
  });
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status });
  const [lightbox, setLightbox] = useState<LB>(null);

  if (isLoading) return <div className="p-6 text-sm text-muted">Loading product…</div>;
  if (error || !p) return <div className="p-6"><Empty icon={<Hash className="size-5" />} title="Product not found" /></div>;

  const stagesDone = p.stages.filter((s) => s.state === "done").length;
  const generatedDone = p.generated.filter((g) => g.status === "COMPLETED").length;
  const busy = ["ANALYZING", "NAMING", "GENERATING_WHITE", "GENERATING_MODEL", "GENERATING_CLOSEUP", "VALIDATING"].includes(p.status);

  return (
    <div className="mx-auto max-w-[1680px] p-6">
      <div className="mb-4 flex items-center gap-1.5 text-[12.5px] text-muted">
        <Link to="/products" className="hover:text-cream">Product Index</Link>
        <ChevronRight className="size-3.5" />
        <span className="font-mono text-gold">{p.product_id}</span>
      </div>
      <DryRunBanner className="mb-4" />

      <div className="grid grid-cols-[196px_minmax(0,1fr)_392px] gap-5">
        {/* ------------------------------------------------ top row */}
        <Card className="col-span-2 px-4 pt-5 pb-4">
          <PipelineStepper stages={p.stages} />
        </Card>
        <Card className="flex flex-col justify-center p-5">
          <div className={cn("flex items-center gap-2 text-[15px] font-medium", busy ? "text-gold" : p.status === "COMPLETED" ? "text-ok" : p.status === "FAILED" ? "text-err" : "text-warn")}>
            {busy && <LoaderCircle className="size-4 animate-spin" />}
            {p.state_label}
          </div>
          <div className="tabular mt-1 text-[22px] font-medium text-paper">{stagesDone} of {p.stages.length} <span className="text-sm font-normal text-muted">workflow stages</span></div>
          <div className="mt-3 flex items-center gap-3">
            <ProgressBar value={p.progress} tone={p.status === "COMPLETED" ? "ok" : p.status === "FAILED" ? "err" : "gold"} className="h-2" />
            <span className="tabular text-sm text-cream">{p.progress}%</span>
          </div>
        </Card>

        {/* ------------------------------------------------ sources */}
        <Card className="h-fit p-4">
          <div className="mb-3 text-[14px] font-semibold text-paper">Source Images <span className="font-normal text-muted">({p.sources.length})</span></div>
          <div className="space-y-4">
            {p.sources.map((s) => (
              <SourceTile key={s.id} s={s} onOpen={() => setLightbox({ src: s.full_url, title: s.filename, sub: `${s.width}×${s.height} · ${s.format} · ${s.status}` })} />
            ))}
          </div>
          {p.grouping_reason && (
            <div className="mt-4 border-t border-line-soft pt-3 text-[11.5px] leading-relaxed text-muted">
              <div className="mb-1 font-medium text-cream">Grouping <Confidence value={p.grouping_confidence} /></div>
              {p.grouping_reason}
            </div>
          )}
        </Card>

        {/* ------------------------------------------------ centre */}
        <div className="min-w-0 space-y-5">
          <Card className="p-5">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h1 className="truncate text-[21px] font-semibold text-paper">
                  {p.product_number} — {p.product_name ?? <span className="text-muted">naming pending…</span>}
                </h1>
                <div className="mt-1 flex items-center gap-3 text-[13px] text-muted">
                  <span>Product ID <span className="font-mono text-gold">{p.product_id}</span></span>
                  {p.folder_name && <span className="flex items-center gap-1 truncate"><FolderOpen className="size-3.5" />{p.folder_name}</span>}
                </div>
              </div>
              <StatusChip status={p.status} label={p.status === "COMPLETED" ? "COMPLETED" : busy ? "PROCESSING" : p.status.replaceAll("_", " ")} className="h-8 px-3.5 text-xs" />
            </div>
            <div className="grid grid-cols-3 gap-4">
              {p.generated.map((g, i) => (
                <GeneratedCard key={g.id} g={g} index={i + 1} product={p} dryRun={!status?.generation_enabled}
                  onOpen={(src) => setLightbox({ src, title: g.png_name ?? g.label, sub: `${g.width}×${g.height} · validation ${g.validation_result ?? "—"}` })} />
              ))}
              {!p.generated.length && (
                <div className="col-span-3"><Empty icon={<LoaderCircle className="size-5 animate-spin" />} title="Image slots are created once the product is named" /></div>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Workflow Progress" right={<span className="tabular text-xs text-muted">{generatedDone} of 3 images completed</span>} />
            <div className="px-5 pb-2">
              <div className="flex items-center gap-3">
                <ProgressBar value={(generatedDone / 3) * 100} className="h-2" />
                <span className="tabular w-10 text-right text-sm text-cream">{Math.round((generatedDone / 3) * 100)}%</span>
              </div>
            </div>
            <div className="max-h-[340px] overflow-y-auto px-3 pb-3">
              <ActivityFeed events={p.events} showProduct={false} highlightLast={busy} />
            </div>
          </Card>
        </div>

        {/* ------------------------------------------------ right */}
        <div className="space-y-5">
          <InfoPanel p={p} />
          <AnalysisPanel p={p} />
          <IntegrityPanel p={p} />
          <FilesPanel p={p} onOpen={(src, title) => setLightbox({ src, title })} />
          <ActionsPanel p={p} paused={!!status?.paused} />
          <JobPanel p={p} />
        </div>
      </div>
      {lightbox && <Lightbox src={lightbox.src} title={lightbox.title} sub={lightbox.sub} onClose={() => setLightbox(null)} />}
    </div>
  );
}

function SourceTile({ s, onOpen }: { s: SourceImage; onOpen: () => void }) {
  return (
    <div>
      <button onClick={onOpen} className="group relative block w-full">
        <Img src={s.thumb_url} alt={s.filename} className="aspect-[4/3] w-full rounded-lg border border-line" />
        <span className="absolute top-1.5 right-1.5 grid size-6 place-items-center rounded-md bg-black/60 text-paper opacity-0 transition-opacity group-hover:opacity-100">
          <Maximize2 className="size-3" />
        </span>
      </button>
      <div className="mt-1.5 truncate text-[12px] font-medium text-cream" title={s.filename}>{s.filename}</div>
      <div className="tabular text-[11px] text-muted">{s.width ?? "?"} × {s.height ?? "?"} · {s.format}</div>
      <div className="mt-0.5 flex items-center justify-between text-[10.5px]">
        <span className={s.exists ? "text-ok" : "text-err"} title={s.file_hash}>
          {s.exists ? "✓ sha256 verified" : "file missing"}
        </span>
        <span className="text-muted">group <Confidence value={s.grouping_confidence} /></span>
      </div>
      {s.status === "ARCHIVED" && <div className="text-[10.5px] text-muted">archived to completed images</div>}
    </div>
  );
}

function GeneratedCard({ g, index, product, dryRun, onOpen }: { g: GeneratedImage; index: number; product: PD; dryRun: boolean; onOpen: (src: string) => void }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["product"] });
  const retry = useMutation({ mutationFn: () => api.retry(g.id), onSuccess: refresh });
  const review = useMutation({ mutationFn: () => api.markReview(g.id), onSuccess: refresh });
  const approve = useMutation({ mutationFn: () => api.approve(g.id), onSuccess: refresh });
  const reject = useMutation({ mutationFn: () => api.reject(g.id), onSuccess: refresh });

  const generating = g.status === "GENERATING" || g.status === "VALIDATING";
  const chipLabel: Record<string, string> = {
    COMPLETED: g.approved ? "Approved" : "Completed", GENERATING: "Generating…", VALIDATING: "Validating…",
    PENDING: dryRun ? "Dry run" : product.on_hold ? "On hold" : "Waiting…", FAILED: "Failed", REVIEW_REQUIRED: "Review required",
    REJECTED: "Rejected", GENERATED: "Awaiting validation",
  };
  return (
    <div className="min-w-0">
      <div className="relative">
        {g.preview_url ? (
          <button className="group block w-full" onClick={() => onOpen(g.png_url!)}>
            <Img src={g.preview_url} alt={g.label} className={cn("aspect-[7/8] w-full rounded-lg border border-line", generating && "opacity-60")} />
            <span className="absolute top-2 right-2 grid size-7 place-items-center rounded-md bg-black/60 text-paper opacity-0 transition-opacity group-hover:opacity-100">
              <Maximize2 className="size-3.5" />
            </span>
          </button>
        ) : (
          <div className={cn("grid aspect-[7/8] w-full place-items-center rounded-lg border border-dashed border-line bg-ink-800", generating && "shimmer")}>
            <div className="px-4 text-center text-xs text-muted">
              {generating ? <LoaderCircle className="mx-auto mb-2 size-5 animate-spin text-gold" /> : null}
              {generating ? "Generating…" : g.status === "FAILED" ? "Generation failed" : dryRun ? "DRY RUN — generation disabled" : "Waiting in queue"}
            </div>
          </div>
        )}
        {generating && g.preview_url && (
          <div className="absolute inset-0 grid place-items-center"><LoaderCircle className="size-6 animate-spin text-gold" /></div>
        )}
      </div>
      <div className="mt-2.5 flex items-center justify-between gap-2">
        <div className="truncate text-[14px] font-medium text-paper">{index}. {g.label}</div>
      </div>
      <div className="mt-1.5"><StatusChip status={g.status} label={chipLabel[g.status] ?? g.status} /></div>
      <dl className="mt-2.5 space-y-1 text-[11.5px]">
        <Row k="Attempts" v={<span className="tabular">{g.generation_attempts}</span>} />
        <Row k="Validation" v={g.validation_result ? <span className={g.validation_result === "PASSED" ? "text-ok" : g.validation_result === "FAILED" ? "text-err" : "text-warn"}>{g.validation_result.replaceAll("_", " ")} · {pct(g.validation_confidence)}</span> : "—"} />
        <Row k="PNG" v={g.png_exists ? <span className="text-ok">✓ {fmtBytes(g.png_size)}</span> : "—"} />
        <Row k="WebP" v={g.webp_exists ? <span className="text-ok">✓ {fmtBytes(g.webp_size)}</span> : "—"} />
        <Row k="Generated" v={g.generated_at ? fmtDateTime(g.generated_at) : "—"} />
        {g.image_type !== "white_background" && <Row k="References" v={g.model_reference.length ? g.model_reference.join(", ") : "none"} />}
      </dl>
      {(g.error || g.review_reason) && (
        <div className={cn("mt-2 rounded-md border px-2.5 py-1.5 text-[11.5px] leading-snug",
          g.status === "REVIEW_REQUIRED" ? "border-[#4a3920] bg-warn-ink text-warn" : "border-[#4b2522] bg-err-ink text-err")}>
          {g.status === "REVIEW_REQUIRED" ? g.review_reason : g.error}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {g.png_url && <Button size="sm" onClick={() => onOpen(g.png_url!)}><Maximize2 className="size-3.5" /> Full size</Button>}
        {g.status === "REVIEW_REQUIRED" && (
          <>
            <Button size="sm" variant="success" onClick={() => approve.mutate()} loading={approve.isPending}><ThumbsUp className="size-3.5" /> Approve</Button>
            <Button size="sm" onClick={() => reject.mutate()} loading={reject.isPending}><ThumbsDown className="size-3.5" /> Reject</Button>
          </>
        )}
        {["FAILED", "REJECTED", "REVIEW_REQUIRED"].includes(g.status) && (
          <Button size="sm" onClick={() => retry.mutate()} loading={retry.isPending} disabled={dryRun} title={dryRun ? "Generation disabled in dry run" : undefined}>
            <RotateCw className="size-3.5" /> Retry
          </Button>
        )}
        {(g.status === "COMPLETED" || g.status === "GENERATED") && (
          <Button size="sm" variant="ghost" onClick={() => review.mutate()} loading={review.isPending}><Flag className="size-3.5" /> Mark for review</Button>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 truncate text-right text-cream">{v}</dd>
    </div>
  );
}

function InfoPanel({ p }: { p: PD }) {
  return (
    <Card>
      <CardHeader title="Product Information" />
      <div className="divide-y divide-line-soft px-5 pb-3">
        <KeyValue k="Product Number" v={<span className="tabular">{p.product_number}</span>} />
        <KeyValue k="Product ID" v={<span className="font-mono text-gold">{p.product_id}</span>} />
        <KeyValue k="Product Name" v={p.product_name ?? "—"} />
        <KeyValue k="Category" v={cap(p.analysis?.category ?? p.category)} />
        <KeyValue k="Source Images" v={p.sources.length} />
        <KeyValue k="Grouping" v={<Confidence value={p.grouping_confidence} />} />
        <KeyValue k="Status" v={<StatusChip status={p.status} label={p.state_label} />} />
        <KeyValue k="Created" v={fmtDateTime(p.created_at)} />
        {p.completed_at && <KeyValue k="Completed" v={fmtDateTime(p.completed_at)} />}
      </div>
    </Card>
  );
}

function AnalysisPanel({ p }: { p: PD }) {
  const a = p.analysis;
  return (
    <Card>
      <CardHeader
        title="Product Analysis"
        right={a && <span className={cn("rounded-md border px-2 py-0.5 text-[10.5px] font-medium", a.source === "ai" ? "border-[#4a3f28] bg-gold-ink text-gold" : "border-[#4a3920] bg-warn-ink text-warn")}>
          {a.source === "ai" ? `AI · ${a.model ?? ""}` : "Colour-based only"}
        </span>}
      />
      {!a ? (
        <div className="px-5 pb-5 text-[13px] text-muted">{p.status === "ANALYZING" ? "Analysing product…" : "Not analysed yet."}</div>
      ) : (
        <div className="divide-y divide-line-soft px-5 pb-3">
          <KeyValue k="Category" v={cap(a.product_type || a.category)} />
          <KeyValue k="Dominant Colour" v={cap(a.dominant_colour)} />
          <KeyValue k="Metal Appearance" v={cap(a.metal_appearance)} />
          <KeyValue k="Stones" v={a.stones} />
          <KeyValue k="Pearls" v={a.pearls === null ? "Not assessed" : a.pearls ? "Yes" : "No"} />
          <KeyValue k="Design Features" v={a.design_features.length ? <ul className="space-y-0.5">{a.design_features.map((f) => <li key={f}>{f}</li>)}</ul> : "—"} />
          <KeyValue k="Integrity Notes" v={<ul className="space-y-0.5">{a.integrity_notes.map((f) => <li key={f} className="text-gold/90">{f}</li>)}</ul>} />
          {a.notes.length > 0 && <div className="py-2 text-[11.5px] text-warn">{a.notes.join(" · ")}</div>}
        </div>
      )}
    </Card>
  );
}

function IntegrityPanel({ p }: { p: PD }) {
  const validated = p.generated.filter((g) => g.validation);
  return (
    <Card>
      <CardHeader title="Integrity Checks" sub="Generated images vs. source photographs" />
      <div className="space-y-3 px-5 pb-4">
        {!validated.length && <div className="text-[13px] text-muted">Validation runs after generation.</div>}
        {validated.map((g) => {
          const v = g.validation!;
          return (
            <div key={g.id} className="rounded-lg border border-line-soft bg-ink-900 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[13px] text-cream">{g.label}</span>
                <span className="flex items-center gap-2"><Confidence value={v.confidence} /><StatusChip status={v.result} /></span>
              </div>
              {v.flags.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {v.flags.map((f) => (
                    <li key={f.code} className={cn("flex items-center gap-1.5 text-[11.5px]", f.severity === "info" ? "text-muted" : f.severity === "fail" ? "text-err" : "text-warn")}>
                      <TriangleAlert className="size-3" />{f.label}
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-muted">
                {Object.entries(v.checks).filter(([, c]) => c && typeof c === "object" && "passed" in c && c.passed !== undefined).map(([k, c]) => (
                  <span key={k} className={c.passed === false ? "text-warn" : c.passed ? "text-ok/80" : ""}>
                    {c.passed === false ? "✕" : c.passed ? "✓" : "·"} {k.replaceAll("_", " ")}
                  </span>
                ))}
              </div>
              {v.ai?.summary && <div className="mt-2 text-[11.5px] leading-snug text-muted">AI: {v.ai.summary}</div>}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function FilesPanel({ p, onOpen }: { p: PD; onOpen: (src: string, title: string) => void }) {
  const [tab, setTab] = useState<"png" | "webp">("png");
  return (
    <Card>
      <CardHeader title="Generated Files" />
      <div className="flex gap-1 border-b border-line-soft px-5">
        {(["png", "webp"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("-mb-px border-b-2 px-3 pb-2 text-[12.5px]", tab === t ? "border-gold text-paper" : "border-transparent text-muted hover:text-cream")}>
            {t === "png" ? "PNG (Master)" : "WebP (Ecommerce)"}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-3 gap-3 p-4">
        {p.generated.map((g) => {
          const exists = tab === "png" ? g.png_exists : g.webp_exists;
          const url = tab === "png" ? g.png_url : g.webp_url;
          const name = tab === "png" ? g.png_name : g.webp_name;
          const size = tab === "png" ? g.png_size : g.webp_size;
          return (
            <div key={g.id} className="min-w-0">
              {exists && url ? (
                <button className="relative block w-full" onClick={() => onOpen(url, name ?? "")}>
                  <Img src={g.thumb_url} alt={name ?? ""} className="aspect-square w-full rounded-md border border-line" />
                  {g.status === "COMPLETED" && <CircleCheck className="absolute right-1 bottom-1 size-5 rounded-full bg-ink-950 text-ok" />}
                </button>
              ) : (
                <div className="grid aspect-square place-items-center rounded-md border border-dashed border-line text-dim">
                  {g.status === "GENERATING" ? <LoaderCircle className="size-4 animate-spin text-gold" /> : "—"}
                </div>
              )}
              <div className="mt-1.5 truncate text-[10.5px] text-cream" title={name ?? ""}>{name ?? `${g.image_type}.${tab}`}</div>
              <div className="tabular text-[10px] text-muted">{g.width ? `${g.width} × ${g.height}` : "—"}{size ? ` · ${fmtBytes(size)}` : ""}</div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function ActionsPanel({ p, paused }: { p: PD; paused: boolean }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries();
  const pause = useMutation({ mutationFn: () => (paused ? api.resume() : api.pause()), onSuccess: refresh });
  const process = useMutation({ mutationFn: () => api.processProduct(p.id), onSuccess: refresh });
  const stop = useMutation({ mutationFn: () => api.stopProduct(p.id), onSuccess: refresh });
  const canProcess = p.on_hold || ["PARTIAL", "FAILED"].includes(p.status) || (p.status === "QUEUED" && !p.queued);
  const canStop = p.is_current || p.queued;
  return (
    <Card>
      <CardHeader title="Actions" />
      <div className="grid grid-cols-2 gap-2.5 px-5 pb-5">
        <Button onClick={() => pause.mutate()} loading={pause.isPending}>
          {paused ? <><Play className="size-3.5" /> Resume Processing</> : <><Pause className="size-3.5" /> Pause Processing</>}
        </Button>
        <Button onClick={() => process.mutate()} loading={process.isPending} disabled={!canProcess}>
          <RotateCw className="size-3.5" /> {["PARTIAL", "FAILED"].includes(p.status) ? "Retry Failed" : "Process"}
        </Button>
        <Button variant="danger" className="col-span-2" onClick={() => stop.mutate()} loading={stop.isPending} disabled={!canStop}>
          <Square className="size-3.5" /> Stop Job
        </Button>
        {process.data && !process.data.queued && process.data.reason && (
          <div className="col-span-2 text-[11.5px] text-warn">Not queued: {process.data.reason}</div>
        )}
      </div>
    </Card>
  );
}

function JobPanel({ p }: { p: PD }) {
  if (!p.jobs.length) return null;
  return (
    <Card>
      <CardHeader title="Job Metadata" />
      <div className="px-5 pb-4">
        <table className="w-full text-[11.5px]">
          <thead><tr className="text-left text-muted"><th className="pb-1.5 font-normal">Job</th><th className="font-normal">Status</th><th className="font-normal">Stage</th><th className="font-normal">Started</th></tr></thead>
          <tbody>
            {p.jobs.map((j) => (
              <tr key={j.id} className="border-t border-line-soft" title={j.error ?? ""}>
                <td className="py-1.5 font-mono text-muted">#{j.id}</td>
                <td className={j.status === "COMPLETED" ? "text-ok" : j.status === "RUNNING" ? "text-gold" : j.status === "FAILED" ? "text-err" : "text-warn"}>{j.status}</td>
                <td className="text-cream">{j.stage?.replaceAll("_", " ")}</td>
                <td className="tabular text-muted">{fmtTime(j.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
