import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Button, Card, CardHeader, Dot, PageHeader } from "../components/ui";
import { cn } from "../lib/cn";
import type { SettingItem } from "../types";

const GROUPS: [string, string[]][] = [
  ["Workflow", ["WORKFLOW_MODE", "JIMIKI_ROOT", "MAX_RETRIES", "RETRY_BASE_DELAY", "LOG_LEVEL"]],
  ["Output", ["IMAGE_WIDTH", "IMAGE_HEIGHT", "WEBP_QUALITY"]],
  ["Grouping & validation", ["SIMILARITY_THRESHOLD", "GROUPING_CONFIDENCE_THRESHOLD", "ENABLE_PRODUCT_SIMILARITY_CHECK", "FILE_STABLE_SECONDS", "BATCH_SETTLE_SECONDS"]],
  ["AI provider", ["ANALYSIS_MODEL", "IMAGE_PROVIDER", "IMAGE_MODEL", "IMAGE_QUALITY", "REPLICATE_IMAGE_MODEL"]],
  ["Interface", ["OPERATOR_NAME"]],
];

function Field({ item, value, onChange }: { item: SettingItem; value: unknown; onChange: (v: unknown) => void }) {
  const base = "h-9 w-full rounded-lg border border-line bg-ink-900 px-3 text-[13px] text-cream focus:border-gold-deep focus:outline-none";
  if (item.kind === "bool")
    return (
      <button onClick={() => onChange(!value)} className={cn("relative h-6 w-11 rounded-full border transition-colors", value ? "border-gold-deep bg-gold" : "border-line bg-ink-750")}>
        <span className={cn("absolute top-0.5 size-4.5 rounded-full bg-paper transition-all", value ? "left-[22px]" : "left-0.5")} />
      </button>
    );
  if (item.kind === "select")
    return (
      <select value={String(value)} onChange={(e) => onChange(e.target.value)} className={base}>
        {item.choices!.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  return (
    <input className={cn(base, item.kind === "path" && "font-mono text-xs")} type={item.kind === "number" ? "number" : "text"} step="any"
      value={String(value ?? "")} onChange={(e) => onChange(item.kind === "number" ? e.target.valueAsNumber : e.target.value)} />
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  useEffect(() => { if (data) setDraft(Object.fromEntries(data.items.map((i) => [i.key, i.value]))); }, [data]);
  const save = useMutation({
    mutationFn: () => {
      const changed = Object.fromEntries(Object.entries(draft).filter(([k, v]) => data!.items.find((i) => i.key === k)?.value !== v));
      return api.saveSettings(changed);
    },
    onSuccess: () => qc.invalidateQueries(),
  });
  if (!data) return <div className="p-6 text-sm text-muted">Loading…</div>;
  const byKey = Object.fromEntries(data.items.map((i) => [i.key, i]));
  const dirty = data.items.some((i) => draft[i.key] !== i.value);

  return (
    <div className="mx-auto max-w-[1100px] p-6">
      <PageHeader title="Settings" sub="Changes apply immediately and are stored in the local database"
        right={<Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}><Save className="size-4" /> Save changes</Button>} />
      {save.error && <div className="mb-4 rounded-lg border border-[#4b2522] bg-err-ink px-4 py-2 text-sm text-err">{(save.error as Error).message}</div>}
      {save.isSuccess && !dirty && <div className="mb-4 rounded-lg border border-[#2b4430] bg-ok-ink px-4 py-2 text-sm text-ok">Settings saved.</div>}

      <Card className="mb-5">
        <CardHeader title={<span className="flex items-center gap-2"><KeyRound className="size-4 text-gold" /> OpenAI API key</span>} />
        <div className="flex items-center justify-between px-5 pb-5 text-[13px]">
          <span className="flex items-center gap-2 text-cream"><Dot ok={data.openai_api_key === "Configured"} />{data.openai_api_key}</span>
          <span className="text-xs text-muted">Read from <span className="font-mono">backend/.env</span> (OPENAI_API_KEY). The key is never displayed.</span>
        </div>
      </Card>

      <Card className="mb-5">
        <CardHeader title={<span className="flex items-center gap-2"><KeyRound className="size-4 text-gold" /> Replicate API token</span>} />
        <div className="flex items-center justify-between px-5 pb-5 text-[13px]">
          <span className="flex items-center gap-2 text-cream"><Dot ok={data.replicate_api_token === "Configured"} />{data.replicate_api_token}</span>
          <span className="text-xs text-muted">Read from <span className="font-mono">backend/.env</span> (REPLICATE_API_TOKEN). Needed when the image provider is replicate.</span>
        </div>
      </Card>

      {GROUPS.map(([title, keys]) => (
        <Card key={title} className="mb-5">
          <CardHeader title={title} />
          <div className="divide-y divide-line-soft px-5 pb-2">
            {keys.filter((k) => byKey[k]).map((k) => {
              const it = byKey[k];
              return (
                <div key={k} className="grid grid-cols-[1fr_340px] items-center gap-6 py-3">
                  <div>
                    <div className="text-[13px] text-cream">{it.label} <span className="ml-1 font-mono text-[10.5px] text-dim">{k}</span></div>
                    <div className="text-xs text-muted">{it.description} <span className="text-dim">· source: {it.source}</span></div>
                  </div>
                  <Field item={it} value={draft[k]} onChange={(v) => setDraft((d) => ({ ...d, [k]: v }))} />
                </div>
              );
            })}
          </div>
        </Card>
      ))}
      <Card>
        <CardHeader title="Resolved folders" />
        <div className="space-y-1.5 px-5 pb-5 font-mono text-xs text-muted">
          {Object.entries(data.paths).map(([k, v]) => <div key={k}><span className="inline-block w-24 text-dim">{k}</span>{v}</div>)}
        </div>
      </Card>
    </div>
  );
}
