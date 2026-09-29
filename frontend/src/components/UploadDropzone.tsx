import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CloudUpload, LoaderCircle, TriangleAlert } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { uploadFiles, type UploadResult, type UploadTarget } from "../api/client";
import { cn } from "../lib/cn";

const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,image/tiff,image/bmp,.jpg,.jpeg,.png,.webp,.heic,.heif,.tif,.tiff,.bmp";
const BATCH = 20; // files per request (server accepts up to 50)

const COPY: Record<UploadTarget, { title: string; hint: string; done: string }> = {
  raw: {
    title: "Upload product photographs",
    hint: "Drag & drop or click to choose · JPG, PNG, HEIC, WebP · several photos of the same piece are grouped automatically",
    done: "added to raw images — processing starts automatically",
  },
  reference: {
    title: "Upload model reference photographs",
    hint: "Drag & drop or click to choose · used only for model look, pose, framing and lighting — never for the product",
    done: "added to reference model images",
  },
};

export function UploadDropzone({ target, className, compact }: { target: UploadTarget; className?: string; compact?: boolean }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const copy = COPY[target];

  const send = async (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []);
    if (!files.length || busy) return;
    setBusy(true);
    setError(null);
    setResults(null);
    setProgress(0);
    const totalBytes = files.reduce((n, f) => n + f.size, 0) || 1;
    let doneBytes = 0;
    const all: UploadResult[] = [];
    try {
      for (let i = 0; i < files.length; i += BATCH) {
        const chunk = files.slice(i, i + BATCH);
        const chunkBytes = chunk.reduce((n, f) => n + f.size, 0);
        const res = await uploadFiles(chunk, target, (f) => setProgress((doneBytes + f * chunkBytes) / totalBytes));
        doneBytes += chunkBytes;
        all.push(...res.results);
      }
      setResults(all);
    } catch (e) {
      setError((e as Error).message);
      if (all.length) setResults(all);
    } finally {
      setBusy(false);
      setProgress(1);
      qc.invalidateQueries({ queryKey: target === "raw" ? ["queue"] : ["references"] });
      if (input.current) input.current.value = "";
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    send(e.dataTransfer.files);
  };

  const saved = results?.filter((r) => r.status === "saved") ?? [];
  const problems = results?.filter((r) => r.status !== "saved") ?? [];

  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        disabled={busy}
        className={cn(
          "flex w-full items-center gap-4 rounded-xl border-2 border-dashed text-left transition-colors",
          compact ? "px-4 py-3" : "px-5 py-5",
          over ? "border-gold bg-gold-ink" : "border-line bg-ink-900 hover:border-gold-deep hover:bg-ink-800",
          busy && "cursor-wait",
        )}
      >
        <span className={cn("grid shrink-0 place-items-center rounded-full border border-line bg-ink-850 text-gold", compact ? "size-9" : "size-11")}>
          {busy ? <LoaderCircle className="size-5 animate-spin" /> : <CloudUpload className="size-5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-medium text-paper">{busy ? `Uploading… ${Math.round(progress * 100)}%` : over ? "Drop to upload" : copy.title}</span>
          {!busy && <span className="mt-0.5 block text-xs text-muted">{copy.hint}</span>}
          {busy && (
            <span className="mt-2 block h-1.5 w-full overflow-hidden rounded-full bg-ink-750">
              <span className="block h-full rounded-full bg-gold transition-[width]" style={{ width: `${progress * 100}%` }} />
            </span>
          )}
        </span>
      </button>
      <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => send(e.target.files)} />

      {error && <div className="mt-2 flex items-center gap-2 text-xs text-err"><TriangleAlert className="size-3.5" />{error}</div>}
      {results && (
        <div className="mt-2 space-y-1 text-xs">
          {saved.length > 0 && (
            <div className="flex items-center gap-2 text-ok">
              <CircleCheck className="size-3.5" />
              {saved.length} file{saved.length > 1 ? "s" : ""} {copy.done}
              {saved.some((r) => r.saved_as !== r.filename) && <span className="text-muted">(renamed where a file with the same name existed)</span>}
            </div>
          )}
          {problems.map((r, i) => (
            <div key={i} className={cn("flex items-center gap-2", r.status === "duplicate" ? "text-warn" : "text-err")}>
              <TriangleAlert className="size-3.5 shrink-0" />
              <span className="truncate"><span className="text-cream">{r.filename}</span> — {r.status === "duplicate" ? "skipped, " : ""}{r.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
