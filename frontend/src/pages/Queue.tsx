import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, LoaderCircle, Pause, Play, RefreshCw, Workflow } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { IntakePanel, QueueTable } from "../components/panels";
import { Button, Card, CardHeader, Empty, PageHeader, StatusChip } from "../components/ui";
import { DryRunBanner } from "../components/workflow";
import { fmtTime } from "../lib/format";

export default function Queue() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["queue"], queryFn: api.queue, refetchInterval: 5000 });
  const w = data?.worker;
  const toggle = useMutation({ mutationFn: () => (w?.paused ? api.resume() : api.pause()), onSuccess: () => qc.invalidateQueries() });
  const group = useMutation({ mutationFn: api.groupNow });
  const current = data?.products.find((p) => p.id === w?.current_product);
  const active = data?.products ?? [];

  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <PageHeader
        title="Processing Queue"
        sub="Products are processed sequentially; every state change is persisted and streamed live"
        right={
          <>
            <Button variant="ghost" onClick={() => group.mutate()} loading={group.isPending} disabled={!data?.inbox.unassigned.length}>
              <RefreshCw className="size-4" /> Group Inbox Now
            </Button>
            <Button onClick={() => toggle.mutate()} loading={toggle.isPending}>
              {w?.paused ? <><Play className="size-4" /> Resume Processing</> : <><Pause className="size-4" /> Pause Processing</>}
            </Button>
          </>
        }
      />
      <DryRunBanner className="mb-5" />
      <div className="mb-5 grid grid-cols-[minmax(0,1fr)_420px] gap-5">
        <IntakePanel />
        <Card className="p-5">
          <div className="mb-2 flex items-center gap-2 text-[13px] text-muted"><Workflow className="size-4 text-gold" /> Generation worker</div>
          {w?.paused ? (
            <div className="text-[15px] font-medium text-warn">Paused — the current step will finish, no new steps start</div>
          ) : current ? (
            <Link to={`/products/${current.id}`} className="block">
              <div className="flex items-center gap-2 text-[15px] font-medium text-gold"><LoaderCircle className="size-4 animate-spin" />{current.state_label}</div>
              <div className="mt-1 text-sm text-cream">{current.product_number} — {current.product_name} <span className="font-mono text-xs text-gold-deep">{current.product_id}</span></div>
            </Link>
          ) : (
            <div className="text-[15px] font-medium text-cream">{w?.generation_enabled ? "Idle — waiting for queued products" : "Generation disabled (dry run)"}</div>
          )}
          <div className="mt-3 text-xs text-muted">{w?.queued.length ?? 0} product(s) waiting in the generation queue</div>
        </Card>
      </div>

      <Card className="mb-5">
        <CardHeader title="Products in Workflow" sub="Everything not yet completed" />
        <QueueTable products={active} empty={<Empty icon={<Layers className="size-5" />} title="Queue is empty">Drop photographs into the raw images folder to start.</Empty>} />
      </Card>

      <Card>
        <CardHeader title="Recent Jobs" />
        <table className="w-full text-[12.5px]">
          <thead><tr className="border-b border-line-soft text-left text-[10.5px] tracking-[0.08em] text-muted uppercase">
            <th className="py-2 pl-5 font-medium">Job</th><th className="font-medium">Product</th><th className="font-medium">Status</th><th className="font-medium">Stage</th><th className="font-medium">Retries</th><th className="font-medium">Started</th><th className="font-medium">Finished</th><th className="pr-5 font-medium">Error</th>
          </tr></thead>
          <tbody>
            {data?.jobs.map((j) => (
              <tr key={j.id} className="border-b border-line-soft last:border-0">
                <td className="py-2 pl-5 font-mono text-muted">#{j.id}</td>
                <td>{j.product_id ? <Link className="text-gold hover:text-gold-bright" to={`/products/${j.product_id}`}>product {j.product_id}</Link> : "—"}</td>
                <td><StatusChip status={j.status} /></td>
                <td className="text-cream">{j.stage?.replaceAll("_", " ") ?? "—"}</td>
                <td className="tabular text-muted">{j.retry_count}</td>
                <td className="tabular text-muted">{fmtTime(j.started_at)}</td>
                <td className="tabular text-muted">{fmtTime(j.completed_at)}</td>
                <td className="max-w-[320px] truncate pr-5 text-err" title={j.error ?? ""}>{j.error ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data?.jobs.length && <Empty icon={<Layers className="size-5" />} title="No jobs yet" />}
      </Card>
    </div>
  );
}
