import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarCheck, FlaskConical, Layers, PackageCheck, RefreshCw, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { IntakePanel, QueueTable } from "../components/panels";
import { Button, Card, CardHeader, Empty, PageHeader } from "../components/ui";
import { ActivityFeed, DryRunBanner } from "../components/workflow";

function Kpi({ label, value, sub, icon, tone = "gold", to }: { label: string; value: number | undefined; sub: string; icon: ReactNode; tone?: "gold" | "warn" | "ok"; to: string }) {
  const color = { gold: "text-gold bg-gold-ink", warn: "text-warn bg-warn-ink", ok: "text-ok bg-ok-ink" }[tone];
  return (
    <Link to={to}>
      <Card className="flex items-center gap-4 p-5 transition-colors hover:border-[#35352f]">
        <div className={`grid size-11 place-items-center rounded-xl ${color} [&_svg]:size-5`}>{icon}</div>
        <div>
          <div className="text-[12.5px] text-muted">{label}</div>
          <div className="tabular text-[26px] leading-tight font-semibold text-paper">{value ?? "—"}</div>
          <div className="text-[11.5px] text-dim">{sub}</div>
        </div>
      </Card>
    </Link>
  );
}

export default function Overview() {
  const nav = useNavigate();
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const { data: queue } = useQuery({ queryKey: ["queue"], queryFn: api.queue });
  const { data: audit } = useQuery({ queryKey: ["audit", "recent"], queryFn: () => api.audit({ limit: 14 }) });
  const dry = useMutation({ mutationFn: api.dryScan, onSuccess: () => nav("/dry-scan") });
  const rescan = useMutation({ mutationFn: api.rescan });

  const active = (queue?.products ?? []).filter((p) => p.status !== "COMPLETED");

  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <PageHeader
        title="JIMIKI Image Studio"
        sub="Automated ecommerce product photography workflow"
        right={
          <>
            <Button variant="ghost" onClick={() => rescan.mutate()} loading={rescan.isPending}><RefreshCw className="size-4" /> Rescan Folder</Button>
            <Button variant="primary" onClick={() => dry.mutate()} loading={dry.isPending}><FlaskConical className="size-4" /> Run Dry Scan</Button>
          </>
        }
      />
      <DryRunBanner className="mb-5" />

      <div className="mb-5 grid grid-cols-4 gap-4">
        <Kpi label="Products Processed" value={stats?.products_processed} sub={`of ${stats?.total_products ?? 0} products identified`} icon={<PackageCheck />} tone="ok" to="/completed" />
        <Kpi label="Processing" value={stats?.processing} sub="analysing, queued or generating" icon={<Layers />} to="/queue" />
        <Kpi label="Review Required" value={stats?.review_required} sub="awaiting operator decision" icon={<ShieldAlert />} tone="warn" to="/review" />
        <Kpi label="Completed Today" value={stats?.completed_today} sub="since midnight" icon={<CalendarCheck />} tone="ok" to="/completed" />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_420px] gap-5">
        <div className="min-w-0 space-y-5">
          <IntakePanel />
          <Card>
            <CardHeader title="Processing Queue" sub="Live workflow state from the job database" right={<Link to="/queue" className="text-xs text-gold hover:text-gold-bright">Open queue →</Link>} />
            <QueueTable
              products={active.slice(0, 12)}
              empty={<Empty icon={<Layers className="size-5" />} title="No products in progress">New products appear here as soon as photographs are grouped.</Empty>}
            />
          </Card>
        </div>
        <Card className="h-fit">
          <CardHeader title="Recent Activity" sub="From the audit log" right={<Link to="/logs" className="text-xs text-gold hover:text-gold-bright">All logs →</Link>} />
          <div className="px-3 pb-3">
            {audit?.items.length ? <ActivityFeed events={audit.items} /> : <Empty icon={<Layers className="size-5" />} title="No activity yet" />}
          </div>
        </Card>
      </div>
    </div>
  );
}
