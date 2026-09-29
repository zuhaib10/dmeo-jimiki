import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, LoaderCircle, TriangleAlert } from "lucide-react";
import { api } from "../api/client";
import { Button, Card, CardHeader, Confidence, Empty, PageHeader } from "../components/ui";
import { cap, fmtDateTime } from "../lib/format";

export default function DryScan() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["dryrun"], queryFn: api.dryScanLatest, refetchInterval: (q) => (q.state.data?.run?.status === "RUNNING" ? 1500 : false) });
  const run = useMutation({ mutationFn: api.dryScan, onSuccess: () => qc.invalidateQueries({ queryKey: ["dryrun"] }) });
  const r = data?.run;
  const rep = r?.report;

  return (
    <div className="mx-auto max-w-[1300px] p-6">
      <PageHeader
        title="Dry Scan"
        sub="Scan, hash, group, analyse, propose names and numbers, select model references — without generating images or moving files"
        right={<Button variant="primary" onClick={() => run.mutate()} loading={run.isPending || r?.status === "RUNNING"}><FlaskConical className="size-4" /> Run Dry Scan</Button>}
      />
      <div className="mb-5 flex items-center gap-3 rounded-xl border border-[#4a3920] bg-warn-ink px-4 py-3 text-[13px] text-warn">
        <TriangleAlert className="size-4" /> DRY RUN — the image generation API is never called, no products are created, no numbers are consumed and no files are moved.
      </div>
      {!r ? (
        <Card><Empty icon={<FlaskConical className="size-5" />} title="No dry scan yet">Run a dry scan to preview how the files currently in raw images would be processed.</Empty></Card>
      ) : r.status === "RUNNING" ? (
        <Card className="flex items-center gap-3 p-6 text-gold"><LoaderCircle className="size-5 animate-spin" /> Scanning raw images…</Card>
      ) : r.status === "FAILED" ? (
        <Card className="p-6 text-err">Dry scan failed: {rep?.error}</Card>
      ) : rep && (
        <>
          <div className="mb-5 grid grid-cols-5 gap-4">
            {[
              ["Files scanned", rep.files_scanned], ["Proposed products", rep.groups.filter((g) => !g.attach_to).length],
              ["Attach to existing", rep.groups.filter((g) => g.attach_to).length], ["Already registered", rep.already_registered.length],
              ["Duplicates", rep.duplicates.length],
            ].map(([k, v]) => (
              <Card key={String(k)} className="p-4"><div className="text-xs text-muted">{k}</div><div className="tabular text-2xl font-semibold text-paper">{v}</div></Card>
            ))}
          </div>
          <div className="mb-3 text-xs text-muted">Scanned {fmtDateTime(r.created_at)} · {rep.raw_folder} · {rep.ai_used ? "AI vision used for grouping & analysis" : "deterministic grouping & colour analysis (no API key)"}</div>
          {rep.notes.map((n) => <div key={n} className="mb-2 text-xs text-warn">{n}</div>)}
          <div className="space-y-4">
            {rep.groups.map((g, i) => (
              <Card key={i}>
                <CardHeader
                  title={g.attach_to ? <>Attach to existing product <span className="font-mono text-gold">{g.attach_to}</span></> : <>{g.proposed_number} — {g.proposed_name} <span className="ml-2 font-mono text-sm text-gold">{g.proposed_product_id}</span></>}
                  sub={g.proposed_folder ? `Folder: ${g.proposed_folder}` : undefined}
                  right={<span className="text-xs text-muted">grouping <Confidence value={g.confidence} /></span>}
                />
                <div className="grid grid-cols-[1fr_1fr] gap-6 px-5 pb-5 text-[12.5px]">
                  <div>
                    <div className="mb-1.5 text-[11px] tracking-[0.1em] text-muted uppercase">Files ({g.files.length})</div>
                    {g.files.map((f) => (
                      <div key={f.file} className="flex justify-between border-b border-line-soft py-1">
                        <span className="text-cream">{f.file}</span>
                        <span className="tabular text-muted">{f.width}×{f.height} {f.format} · {f.colours.join(", ")}</span>
                      </div>
                    ))}
                    <div className="mt-2 text-xs text-muted">{g.reason}</div>
                  </div>
                  {g.analysis && (
                    <div>
                      <div className="mb-1.5 text-[11px] tracking-[0.1em] text-muted uppercase">Analysis ({g.analysis.source})</div>
                      <div className="space-y-1 text-cream">
                        <div><span className="text-muted">Type:</span> {cap(g.analysis.product_type)}</div>
                        <div><span className="text-muted">Colour:</span> {cap(g.analysis.dominant_colour)} · <span className="text-muted">Metal:</span> {cap(g.analysis.metal_appearance)}</div>
                        <div><span className="text-muted">Stones:</span> {g.analysis.stones}</div>
                        <div><span className="text-muted">Model references:</span> {g.model_references?.length ? g.model_references.join(", ") : "none available"}</div>
                      </div>
                    </div>
                  )}
                </div>
              </Card>
            ))}
            {!rep.groups.length && <Card><Empty icon={<FlaskConical className="size-5" />} title="No new files in raw images" /></Card>}
          </div>
        </>
      )}
    </div>
  );
}
