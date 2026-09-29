import { useQuery } from "@tanstack/react-query";
import { ScrollText, Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Card, Empty, PageHeader } from "../components/ui";
import { EventIcon } from "../components/workflow";
import { cn } from "../lib/cn";
import { fmtDateTime } from "../lib/format";

export default function Logs() {
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const { data } = useQuery({ queryKey: ["audit", "logs", level, query], queryFn: () => api.audit({ level, q: query, limit: 500 }) });
  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <PageHeader title="Logs" sub="Complete audit trail of every workflow action (live)" />
      <Card>
        <div className="flex items-center gap-3 border-b border-line-soft p-4">
          <form className="relative w-[360px]" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => setQuery(q)} placeholder="Search message, product ID or event…"
              className="h-9 w-full rounded-lg border border-line bg-ink-900 pr-3 pl-9 text-[13px] text-cream placeholder:text-dim focus:border-gold-deep focus:outline-none" />
          </form>
          {["", "INFO", "WARN", "ERROR"].map((l) => (
            <button key={l} onClick={() => setLevel(l)}
              className={cn("h-9 rounded-lg border px-3.5 text-[12.5px]", level === l ? "border-gold-deep bg-gold-ink text-gold" : "border-line bg-ink-900 text-muted hover:text-cream")}>
              {l || "All levels"}
            </button>
          ))}
        </div>
        {!data?.items.length ? <Empty icon={<ScrollText className="size-5" />} title="No log entries" /> : (
          <table className="w-full text-[12.5px]">
            <thead><tr className="border-b border-line-soft text-left text-[10.5px] tracking-[0.08em] text-muted uppercase">
              <th className="py-2 pl-5 font-medium">Timestamp</th><th className="font-medium">Product</th><th className="font-medium">Stage</th><th className="font-medium">Event</th><th className="pr-5 font-medium">Message</th>
            </tr></thead>
            <tbody>
              {data.items.map((e) => (
                <tr key={e.id} className="border-b border-line-soft align-top last:border-0 hover:bg-ink-800/50">
                  <td className="tabular py-2 pl-5 whitespace-nowrap text-muted">{fmtDateTime(e.timestamp)}</td>
                  <td className="font-mono text-[11.5px] whitespace-nowrap">{e.product_id ? <Link to={`/products/${e.product_id}`} className="text-gold hover:text-gold-bright">{e.product_code}</Link> : <span className="text-dim">—</span>}</td>
                  <td className="text-[11px] whitespace-nowrap text-muted">{e.stage?.replaceAll("_", " ") ?? ""}</td>
                  <td className="whitespace-nowrap"><span className="flex items-center gap-1.5 text-cream/80"><EventIcon level={e.level} type={e.event_type} />{e.event_type.replaceAll("_", " ")}</span></td>
                  <td className={cn("py-2 pr-5", e.level === "ERROR" ? "text-err" : e.level === "WARN" ? "text-warn" : "text-cream")}>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
