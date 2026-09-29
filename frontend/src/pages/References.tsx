import { useQuery } from "@tanstack/react-query";
import { FolderOpen, UserRound } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Card, Empty, Img, Lightbox, PageHeader } from "../components/ui";
import { UploadDropzone } from "../components/UploadDropzone";
import { fmtBytes } from "../lib/format";

export default function References() {
  const { data } = useQuery({ queryKey: ["references"], queryFn: api.references });
  const [lb, setLb] = useState<{ src: string; title: string } | null>(null);
  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <PageHeader
        title="Reference Models"
        sub="Used only for model appearance, pose, framing, styling and lighting — never as a source for jewellery"
        right={data && <span className="flex items-center gap-1.5 text-xs text-muted"><FolderOpen className="size-3.5 text-gold-deep" />{data.folder}</span>}
      />
      <UploadDropzone target="reference" className="mb-5" />
      {!data ? <div className="text-sm text-muted">Loading…</div> : !data.items.length ? (
        <Card><Empty icon={<UserRound className="size-5" />} title="No reference model images">Upload model photographs above or add them to the reference model images folder. Filenames containing a category (e.g. <i>earrings-01.jpg</i>) are preferred for that category.</Empty></Card>
      ) : (
        <div className="grid grid-cols-6 gap-4">
          {data.items.map((r) => (
            <Card key={r.name} className="overflow-hidden">
              <button className="block w-full" onClick={() => setLb({ src: r.full_url, title: r.name })}>
                <Img src={r.thumb_url} alt={r.name} className="aspect-[4/5] w-full" />
              </button>
              <div className="p-3">
                <div className="truncate text-[12.5px] text-cream">{r.name}</div>
                <div className="tabular text-[11px] text-muted">{r.width} × {r.height} · {r.format} · {fmtBytes(r.size)}</div>
              </div>
            </Card>
          ))}
        </div>
      )}
      {lb && <Lightbox src={lb.src} title={lb.title} onClose={() => setLb(null)} />}
    </div>
  );
}
