import { useQuery } from "@tanstack/react-query";
import { CircleCheck, FolderOpen } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Card, Empty, Img, Lightbox, PageHeader } from "../components/ui";
import { fmtDateTime } from "../lib/format";

export default function Completed() {
  const { data } = useQuery({ queryKey: ["completed"], queryFn: () => api.completed() });
  const [lb, setLb] = useState<{ src: string; title: string } | null>(null);
  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <PageHeader title="Completed Products" sub="Validated PNG masters and WebP ecommerce files, with source photographs archived" />
      {!data ? <div className="text-sm text-muted">Loading…</div> : !data.items.length ? (
        <Card><Empty icon={<CircleCheck className="size-5" />} title="No completed products yet" /></Card>
      ) : (
        <div className="grid grid-cols-2 gap-5 2xl:grid-cols-3">
          {data.items.map((p) => (
            <Card key={p.id} className="p-4">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <Link to={`/products/${p.id}`} className="block truncate text-[14px] font-medium text-paper hover:text-gold">{p.product_number} — {p.product_name}</Link>
                  <div className="text-xs text-muted"><span className="font-mono text-gold-deep">{p.product_id}</span> · {p.source_count} source image(s) · {fmtDateTime(p.completed_at)}</div>
                </div>
                <CircleCheck className="size-5 shrink-0 text-ok" />
              </div>
              <div className="grid grid-cols-3 gap-2">
                {p.generated.map((g) => (
                  <button key={g.id} onClick={() => g.png_url && setLb({ src: g.png_url, title: g.png_name ?? "" })} className="text-left">
                    <Img src={g.thumb_url} alt={g.label} className="aspect-[7/8] w-full rounded-md border border-line" />
                    <div className="mt-1 truncate text-[10.5px] text-muted">{g.label}</div>
                  </button>
                ))}
              </div>
              <div className="mt-3 space-y-1 text-[11px] text-muted">
                <div className="flex items-center gap-1.5 truncate" title={p.output_folder}><FolderOpen className="size-3.5 shrink-0 text-gold-deep" />{p.output_folder}</div>
                <div className="flex items-center gap-1.5 truncate" title={p.archive_folder}><FolderOpen className="size-3.5 shrink-0" />{p.archive_folder}</div>
              </div>
            </Card>
          ))}
        </div>
      )}
      {lb && <Lightbox src={lb.src} title={lb.title} onClose={() => setLb(null)} />}
    </div>
  );
}
