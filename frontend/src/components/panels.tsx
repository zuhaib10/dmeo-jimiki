import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Inbox, LoaderCircle, UserRound } from "lucide-react";
import { Link } from "react-router-dom";
import { UploadDropzone } from "./UploadDropzone";
import { api } from "../api/client";
import { cn } from "../lib/cn";
import { fmtBytes, productTitle } from "../lib/format";
import type { ProductSummary } from "../types";
import { Card, CardHeader, Img, ProgressBar, StatusChip } from "./ui";

const INTAKE_LABEL: Record<string, string> = {
  IDLE: "Idle",
  REGISTERING: "Registering files",
  ANALYZING: "Analyzing images",
  SETTLING: "Waiting for batch to settle",
  GROUPING: "Grouping Images",
  ANALYZING_PRODUCT: "Analyzing Product",
};

/** Real-time view of the raw-images inbox: files copying, registered, grouping. */
export function IntakePanel({ className }: { className?: string }) {
  const { data } = useQuery({ queryKey: ["queue"], queryFn: api.queue, refetchInterval: 4000 });
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: api.status });
  const { data: refs } = useQuery({ queryKey: ["references"], queryFn: api.references });
  const inbox = data?.inbox;
  const copying = inbox?.stabilizing ?? [];
  const waiting = inbox?.unassigned ?? [];
  const stage = inbox?.intake_stage ?? "IDLE";
  const busy = copying.length > 0 || waiting.length > 0 || stage !== "IDLE";

  let headline = "Waiting for Images";
  if (copying.length) headline = `${copying.length} File${copying.length > 1 ? "s" : ""} Detected`;
  else if (stage === "GROUPING") headline = "Grouping Images";
  else if (stage === "ANALYZING_PRODUCT") headline = "Analyzing Product";
  else if (waiting.length) headline = `${waiting.length} Photograph${waiting.length > 1 ? "s" : ""} in Inbox`;

  return (
    <Card className={className}>
      <CardHeader
        title={<span className="flex items-center gap-2"><Inbox className="size-4 text-gold" /> Product Inbox</span>}
        sub={<span className="flex items-center gap-1.5"><FolderOpen className="size-3.5" />{status?.watcher.path ?? "…"}</span>}
        right={
          <span className={cn("flex items-center gap-2 text-xs", busy ? "text-gold" : "text-muted")}>
            {busy && <LoaderCircle className="size-3.5 animate-spin" />}
            {INTAKE_LABEL[stage] ?? stage}
            {stage === "SETTLING" && inbox?.settle_remaining ? ` · ${Math.ceil(inbox.settle_remaining)}s` : ""}
          </span>
        }
      />
      <div className="px-5 pb-4">
        <UploadDropzone target="raw" className="mb-4" />
        {refs && refs.items.length === 0 && (
          <Link to="/references" className="mb-3 flex items-center gap-2 rounded-lg border border-[#4a3920] bg-warn-ink px-3 py-2 text-xs text-warn hover:text-gold-bright">
            <UserRound className="size-3.5" /> No model reference photos yet — upload them on Reference Models first for better model and close-up images →
          </Link>
        )}
        <div className="mb-3 text-[15px] font-medium text-paper">{headline}</div>
        {!busy && (
          <p className="text-[13px] text-muted">
            Upload above, or copy photographs straight into the folder shown. Files are registered once copying has finished, then grouped into products automatically.
          </p>
        )}
        {(copying.length > 0 || waiting.length > 0) && (
          <div className="flex gap-3 overflow-x-auto pb-1">
            {copying.map((f) => (
              <div key={f.name} className="w-[112px] shrink-0">
                <div className="shimmer grid aspect-square place-items-center rounded-lg border border-dashed border-line">
                  <LoaderCircle className="size-4 animate-spin text-gold" />
                </div>
                <div className="mt-1.5 truncate text-[11.5px] text-cream">{f.name}</div>
                <div className="text-[10.5px] text-muted">{fmtBytes(f.size)} · copying</div>
              </div>
            ))}
            {waiting.map((s) => (
              <div key={s.id} className="w-[112px] shrink-0">
                <Img src={s.thumb_url} alt={s.filename} className="aspect-square rounded-lg border border-line" />
                <div className="mt-1.5 truncate text-[11.5px] text-cream">{s.filename}</div>
                <div className="text-[10.5px] text-muted">{s.status === "ANALYZED" ? "awaiting grouping" : s.status.toLowerCase()}</div>
              </div>
            ))}
          </div>
        )}
        {(inbox?.duplicates.length ?? 0) > 0 && (
          <div className="mt-3 text-[11.5px] text-muted">
            {inbox!.duplicates.length} duplicate file(s) ignored and left untouched — see Logs.
          </div>
        )}
      </div>
    </Card>
  );
}

export function QueueTable({ products, empty }: { products: ProductSummary[]; empty?: React.ReactNode }) {
  if (!products.length) return <>{empty}</>;
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="border-b border-line-soft text-left text-[11px] tracking-[0.08em] text-muted uppercase">
          <th className="py-2.5 pl-5 font-medium">Product</th>
          <th className="py-2.5 font-medium">Sources</th>
          <th className="py-2.5 font-medium">State</th>
          <th className="w-[200px] py-2.5 pr-5 font-medium">Progress</th>
        </tr>
      </thead>
      <tbody>
        {products.map((p) => (
          <tr key={p.id} className="border-b border-line-soft last:border-0 hover:bg-ink-800/60">
            <td className="py-2.5 pl-5">
              <Link to={`/products/${p.id}`} className="flex items-center gap-3">
                <Img src={p.thumb_url} alt="" className="size-11 shrink-0 rounded-md border border-line" />
                <div className="min-w-0">
                  <div className="font-mono text-[11.5px] text-gold">{p.product_id}</div>
                  <div className="truncate text-cream">{productTitle(p)}</div>
                </div>
              </Link>
            </td>
            <td className="tabular text-muted">{p.source_count} source image{p.source_count === 1 ? "" : "s"}</td>
            <td><StatusChip status={p.status} label={p.status.replaceAll("_", " ")} /></td>
            <td className="pr-5">
              <div className="flex items-center gap-3">
                <ProgressBar value={p.progress} tone={p.status === "FAILED" ? "err" : p.status === "REVIEW_REQUIRED" || p.status === "PARTIAL" ? "warn" : p.status === "COMPLETED" ? "ok" : "gold"} />
                <span className="tabular w-9 text-right text-xs text-muted">{p.progress}%</span>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
