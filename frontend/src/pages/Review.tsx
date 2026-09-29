import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw, ShieldCheck, ThumbsDown, ThumbsUp, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Button, Card, Confidence, Empty, Img, Lightbox, PageHeader } from "../components/ui";
import { cn } from "../lib/cn";
import { fmtDateTime } from "../lib/format";
import type { ReviewItem } from "../types";

function ReviewCard({ item, onOpen }: { item: ReviewItem; onOpen: (src: string, title: string) => void }) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries();
  const g = item.generated;
  const approve = useMutation({ mutationFn: () => api.approve(g.id), onSuccess: done });
  const reject = useMutation({ mutationFn: () => api.reject(g.id), onSuccess: done });
  const retry = useMutation({ mutationFn: () => api.retry(g.id), onSuccess: done });
  const flags = g.validation?.flags.filter((f) => f.severity !== "info") ?? [];
  const src = item.sources[0];

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-line-soft px-5 py-3">
        <div>
          <Link to={`/products/${item.product.id}`} className="text-[14px] font-medium text-paper hover:text-gold">
            {item.product.product_number} — {item.product.product_name}
          </Link>
          <div className="text-xs text-muted"><span className="font-mono text-gold-deep">{item.product.product_id}</span> · {g.label} · attempt {g.generation_attempts}</div>
        </div>
        <div className="text-right text-xs text-muted">Confidence<div className="text-lg"><Confidence value={g.validation_confidence} /></div></div>
      </div>
      <div className="grid grid-cols-2 gap-4 p-5">
        <div>
          <div className="mb-1.5 text-[11px] tracking-[0.1em] text-muted uppercase">Source product</div>
          <button className="block w-full" onClick={() => src && onOpen(src.full_url, src.filename)}>
            <Img src={src?.thumb_url} alt="source" className="aspect-[7/8] w-full rounded-lg border border-line" />
          </button>
          {item.sources.length > 1 && (
            <div className="mt-2 flex gap-1.5">
              {item.sources.slice(1).map((s) => (
                <button key={s.id} onClick={() => onOpen(s.full_url, s.filename)}><Img src={s.thumb_url} alt="" className="size-10 rounded border border-line" /></button>
              ))}
            </div>
          )}
        </div>
        <div>
          <div className="mb-1.5 text-[11px] tracking-[0.1em] text-muted uppercase">Generated image</div>
          <button className="block w-full" onClick={() => g.png_url && onOpen(g.png_url, g.png_name ?? g.label)}>
            <Img src={g.preview_url} alt="generated" className="aspect-[7/8] w-full rounded-lg border border-warn/50" />
          </button>
        </div>
      </div>
      <div className="px-5 pb-4">
        <div className="mb-2 text-[13px] text-warn">{g.review_reason}</div>
        <div className="flex flex-wrap gap-1.5">
          {flags.map((f) => (
            <span key={f.code} className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]", f.severity === "fail" ? "border-[#4b2522] bg-err-ink text-err" : "border-[#4a3920] bg-warn-ink text-warn")}>
              <TriangleAlert className="size-3" />{f.label}
            </span>
          ))}
        </div>
        {g.validation?.ai?.summary && <div className="mt-2 text-xs leading-relaxed text-muted">AI inspector: {g.validation.ai.summary}</div>}
        <div className="mt-1 text-[11px] text-dim">Validated {fmtDateTime(g.validation?.created_at)}</div>
      </div>
      <div className="grid grid-cols-3 gap-2 border-t border-line-soft bg-ink-900 p-4">
        <Button variant="success" onClick={() => approve.mutate()} loading={approve.isPending}><ThumbsUp className="size-4" /> Approve</Button>
        <Button onClick={() => reject.mutate()} loading={reject.isPending}><ThumbsDown className="size-4" /> Reject</Button>
        <Button onClick={() => retry.mutate()} loading={retry.isPending}><RotateCw className="size-4" /> Retry Generation</Button>
      </div>
    </Card>
  );
}

export default function Review() {
  const { data } = useQuery({ queryKey: ["review"], queryFn: api.review });
  const [lb, setLb] = useState<{ src: string; title: string } | null>(null);
  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <PageHeader title="Review Required" sub="Generated images whose product integrity could not be confirmed automatically. Source files stay in raw images until approved." />
      {!data ? <div className="text-sm text-muted">Loading…</div> : !data.items.length ? (
        <Card><Empty icon={<ShieldCheck className="size-5" />} title="Nothing to review">Images appear here when validation confidence falls below the threshold or integrity flags are raised.</Empty></Card>
      ) : (
        <div className="grid grid-cols-2 gap-5">
          {data.items.map((it) => <ReviewCard key={it.generated.id} item={it} onOpen={(src, title) => setLb({ src, title })} />)}
        </div>
      )}
      {lb && <Lightbox src={lb.src} title={lb.title} onClose={() => setLb(null)} />}
    </div>
  );
}
