import type {
  AuditEvent, DryRun, ProductDetail, ProductSummary, QueueData, ReferenceImage, ReviewItem, SettingItem, Stats,
  SystemStatus,
} from "../types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = body.detail ?? msg;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface UploadResult {
  filename: string;
  saved_as?: string;
  status: "saved" | "duplicate" | "rejected";
  message?: string;
  size?: number;
  width?: number;
  height?: number;
}
export type UploadTarget = "raw" | "reference";

/** Multipart upload with progress (fetch cannot report upload progress). */
export function uploadFiles(files: File[], target: UploadTarget, onProgress?: (fraction: number) => void) {
  return new Promise<{ saved: number; results: UploadResult[]; folder: string }>((resolve, reject) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/uploads?target=${target}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let body: any = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not json */
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) resolve(body);
      else reject(new ApiError(xhr.status, body?.detail ?? xhr.statusText ?? "Upload failed"));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.send(form);
  });
}

export const api = {
  status: () => request<SystemStatus>("/api/status"),
  stats: () => request<Stats>("/api/stats"),
  queue: () => request<QueueData>("/api/queue"),
  products: (params: { filter?: string; q?: string }) => {
    const sp = new URLSearchParams();
    if (params.filter) sp.set("filter", params.filter);
    if (params.q) sp.set("q", params.q);
    return request<{ items: ProductSummary[]; counts: Record<string, number> }>(`/api/products?${sp}`);
  },
  product: (ref: string | number) => request<ProductDetail>(`/api/products/${ref}`),
  studioCurrent: () => request<{ id: number | null }>("/api/studio/current"),
  processProduct: (id: number) => post<{ queued: boolean; reason?: string }>(`/api/products/${id}/process`),
  stopProduct: (id: number) => post(`/api/products/${id}/stop`),
  approve: (gid: number, note?: string) => post(`/api/generated/${gid}/approve`, { note }),
  reject: (gid: number, note?: string) => post(`/api/generated/${gid}/reject`, { note }),
  retry: (gid: number) => post<{ ok: boolean; queued: boolean }>(`/api/generated/${gid}/retry`),
  markReview: (gid: number) => post(`/api/generated/${gid}/review`),
  review: () => request<{ items: ReviewItem[] }>("/api/review"),
  completed: (q = "") =>
    request<{ items: (ProductSummary & { generated: import("../types").GeneratedImage[]; output_folder: string; archive_folder: string })[] }>(
      `/api/completed?q=${encodeURIComponent(q)}`,
    ),
  audit: (params: { product_id?: number; level?: string; q?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && sp.set(k, String(v)));
    return request<{ items: AuditEvent[] }>(`/api/audit?${sp}`);
  },
  settings: () =>
    request<{ items: SettingItem[]; openai_api_key: string; replicate_api_token: string; paths: Record<string, string> }>("/api/settings"),
  saveSettings: (values: Record<string, unknown>) =>
    request<{ items: SettingItem[] }>("/api/settings", { method: "PUT", body: JSON.stringify({ values }) }),
  pause: () => post("/api/system/pause"),
  resume: () => post("/api/system/resume"),
  rescan: () => post<{ new_files: number }>("/api/system/rescan"),
  groupNow: () => post("/api/system/group-now"),
  dryScan: () => post<{ id: number }>("/api/dry-scan"),
  dryScanLatest: () => request<{ run: DryRun | null }>("/api/dry-scan/latest"),
  references: () => request<{ folder: string; items: ReferenceImage[] }>("/api/references"),
};
