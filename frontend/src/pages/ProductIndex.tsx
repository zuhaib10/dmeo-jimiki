import { useQuery } from "@tanstack/react-query";
import { Eye, Package, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Card, Confidence, Empty, Img, PageHeader, StatusChip } from "../components/ui";
import { cn } from "../lib/cn";
import { fmtDate } from "../lib/format";
import type { ImageSlot, ProductSummary } from "../types";

const FILTERS = [
  ["all", "All"], ["processing", "Processing"], ["completed", "Completed"], ["review", "Review Required"], ["failed", "Failed"],
] as const;

function Slot({ slot }: { slot: ImageSlot | null }) {
  if (!slot) return <span className="text-dim">—</span>;
  const tone = slot.status === "COMPLETED" ? "border-[#2b4430]" : slot.status === "REVIEW_REQUIRED" ? "border-warn" : slot.status === "FAILED" || slot.status === "REJECTED" ? "border-err" : "border-line";
  return (
    <div className="flex items-center gap-2" title={slot.status}>
      {slot.thumb_url ? (
        <Img src={slot.thumb_url} alt="" className={cn("size-9 rounded border", tone)} />
      ) : (
        <span className={cn("grid size-9 place-items-center rounded border border-dashed text-[9px] text-dim", tone)}>
          {slot.status === "GENERATING" ? "…" : slot.status === "FAILED" ? "✕" : ""}
        </span>
      )}
    </div>
  );
}

function IntegrityCell({ p }: { p: ProductSummary }) {
  const i = p.integrity;
  if (i.state === "pending") return <span className="text-xs text-dim">pending</span>;
  const label = { passed: "Passed", review: "Review", failed: "Failed", partial: "Partial" }[i.state];
  const color = { passed: "text-ok", review: "text-warn", failed: "text-err", partial: "text-warn" }[i.state];
  return <div className="text-xs"><div className={color}>{label}</div><Confidence value={i.confidence} /></div>;
}

export default function ProductIndex() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const [q, setQ] = useState(sp.get("q") ?? "");
  const filter = sp.get("filter") ?? "all";
  useEffect(() => setQ(sp.get("q") ?? ""), [sp]);
  const { data } = useQuery({ queryKey: ["products", filter, sp.get("q") ?? ""], queryFn: () => api.products({ filter, q: sp.get("q") ?? "" }) });

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v && v !== "all") n.set(k, v); else n.delete(k);
    setSp(n, { replace: true });
  };

  return (
    <div className="mx-auto max-w-[1600px] p-6">
      <PageHeader title="Product Index" sub="Every identified product with its permanent ID, number and generated assets" />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-line-soft p-4">
          <form className="relative w-[380px]" onSubmit={(e) => { e.preventDefault(); set("q", q.trim()); }}>
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => set("q", q.trim())}
              placeholder="Search product ID, number or name..."
              className="h-9 w-full rounded-lg border border-line bg-ink-900 pr-3 pl-9 text-[13px] text-cream placeholder:text-dim focus:border-gold-deep focus:outline-none" />
          </form>
          <div className="flex gap-1.5">
            {FILTERS.map(([k, label]) => (
              <button key={k} onClick={() => set("filter", k)}
                className={cn("h-9 rounded-lg border px-3.5 text-[12.5px] transition-colors",
                  filter === k ? "border-gold-deep bg-gold-ink text-gold" : "border-line bg-ink-900 text-muted hover:text-cream")}>
                {label}
                <span className="tabular ml-1.5 text-[11px] opacity-70">{data?.counts[k] ?? ""}</span>
              </button>
            ))}
          </div>
        </div>
        {!data ? (
          <div className="p-6 text-sm text-muted">Loading…</div>
        ) : !data.items.length ? (
          <Empty icon={<Package className="size-5" />} title="No products match">Products are created automatically when photographs are grouped.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line-soft text-left text-[10.5px] tracking-[0.08em] text-muted uppercase">
                  {["", "Product ID", "Number", "Product Name", "Sources", "White", "Model", "Close-up", "Integrity", "Status", "Created", ""].map((h, i) => (
                    <th key={i} className={cn("py-2.5 font-medium", i === 0 && "pl-4")}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id} onClick={() => nav(`/products/${p.id}`)} className="cursor-pointer border-b border-line-soft last:border-0 hover:bg-ink-800/60">
                    <td className="py-2 pl-4"><Img src={p.source_thumb_url} alt="" className="size-11 rounded-md border border-line" /></td>
                    <td className="font-mono text-[12px] text-gold">{p.product_id}</td>
                    <td className="tabular text-paper">{p.product_number}</td>
                    <td className="max-w-[260px] truncate text-cream">{p.product_name ?? <span className="text-dim">naming…</span>}</td>
                    <td className="tabular text-muted">{p.source_count}</td>
                    <td><Slot slot={p.images.white_background} /></td>
                    <td><Slot slot={p.images.model} /></td>
                    <td><Slot slot={p.images.closeup} /></td>
                    <td><IntegrityCell p={p} /></td>
                    <td><StatusChip status={p.status} /></td>
                    <td className="tabular text-xs text-muted">{fmtDate(p.created_at)}</td>
                    <td className="pr-4 text-right">
                      <Link to={`/products/${p.id}`} onClick={(e) => e.stopPropagation()} className="inline-grid size-8 place-items-center rounded-md text-muted hover:bg-ink-750 hover:text-paper"><Eye className="size-4" /></Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
