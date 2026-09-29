export type ProductStatus =
  | "DISCOVERED" | "ANALYZING" | "GROUPING" | "NAMING" | "QUEUED"
  | "GENERATING_WHITE" | "GENERATING_MODEL" | "GENERATING_CLOSEUP" | "VALIDATING"
  | "COMPLETED" | "PARTIAL" | "FAILED" | "REVIEW_REQUIRED";

export type ImageType = "white_background" | "model" | "closeup";
export type StageState = "done" | "active" | "pending" | "failed" | "review" | "disabled";

export interface Stage { key: string; label: string; sublabel: string; state: StageState }

export interface ImageSlot { status: string; thumb_url: string | null; validation_confidence: number | null }

export interface ProductSummary {
  id: number;
  product_id: string;
  product_number: number;
  product_name: string | null;
  folder_name: string | null;
  category: string | null;
  status: ProductStatus;
  state_label: string;
  on_hold: boolean;
  grouping_confidence: number | null;
  source_count: number;
  progress: number;
  stages: Stage[];
  images: Record<ImageType, ImageSlot | null>;
  integrity: { state: "pending" | "passed" | "review" | "failed" | "partial"; confidence: number | null };
  thumb_url: string | null;
  source_thumb_url: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface SourceImage {
  id: number;
  product_id: number | null;
  filename: string;
  original_path: string;
  current_path: string;
  file_hash: string;
  perceptual_hash: string | null;
  format: string | null;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  file_size: number | null;
  status: string;
  grouping_confidence: number | null;
  grouping_reason: string | null;
  duplicate_of_id: number | null;
  exists: boolean;
  colours: string[];
  thumb_url: string;
  full_url: string;
  created_at: string;
  archived_at: string | null;
  error: string | null;
}

export interface ValidationFlag { code: string; label: string; severity: "fail" | "review" | "info" }
export interface Validation {
  id: number;
  result: "PASSED" | "FAILED" | "REVIEW_REQUIRED";
  confidence: number | null;
  flags: ValidationFlag[];
  checks: Record<string, { passed?: boolean | null; value?: unknown; expected?: unknown; method?: string; error?: string }>;
  ai: { summary?: string; confidence?: number; same_product?: boolean } | null;
  attempt: number;
  created_at: string;
}

export interface Attempt {
  attempt: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  metadata: Record<string, unknown> | null;
}

export interface GeneratedImage {
  id: number;
  image_type: ImageType;
  label: string;
  status: string;
  png_path: string | null;
  webp_path: string | null;
  png_exists: boolean;
  webp_exists: boolean;
  png_name: string | null;
  webp_name: string | null;
  png_size: number | null;
  webp_size: number | null;
  width: number | null;
  height: number | null;
  generation_attempts: number;
  prompt_version: string | null;
  provider_model: string | null;
  model_reference: string[];
  validation_result: string | null;
  validation_confidence: number | null;
  review_reason: string | null;
  approved: boolean;
  error: string | null;
  generated_at: string | null;
  updated_at: string | null;
  preview_url: string | null;
  thumb_url: string | null;
  png_url: string | null;
  webp_url: string | null;
  validation: Validation | null;
  attempts?: Attempt[];
}

export interface Analysis {
  source: "ai" | "deterministic";
  model: string | null;
  category: string | null;
  product_type: string | null;
  dominant_colour: string | null;
  secondary_colours: string[];
  metal_appearance: string | null;
  stones: string | null;
  pearls: boolean | null;
  design_features: string[];
  integrity_notes: string[];
  name_parts: Record<string, string>;
  confidence: number | null;
  notes: string[];
  created_at: string;
}

export interface Job {
  id: number;
  product_id: number | null;
  kind: string;
  status: string;
  stage: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  retry_count: number;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  timestamp: string;
  product_id: number | null;
  product_code: string | null;
  source_image_id: number | null;
  stage: string | null;
  event_type: string;
  level: "INFO" | "WARN" | "ERROR";
  message: string;
  details: Record<string, unknown> | null;
}

export interface ProductDetail extends ProductSummary {
  sources: SourceImage[];
  analysis: Analysis | null;
  generated: GeneratedImage[];
  grouping_reason: string | null;
  jobs: Job[];
  events: AuditEvent[];
  queued: boolean;
  is_current: boolean;
}

export interface SystemStatus {
  api: { ok: boolean };
  database: { ok: boolean; error?: string };
  watcher: { ok: boolean; path: string; error: string | null };
  openai_configured: boolean;
  workflow_mode: "live" | "dry_run";
  generation_enabled: boolean;
  dry_run_reason: string | null;
  paused: boolean;
  current_product: number | null;
  current_stage: string | null;
  intake_stage: string;
  queued: number[];
  storage: { total: number; used: number; free: number; percent: number } | null;
  operator: string;
  root: string;
  folders: Record<string, boolean>;
}

export interface Stats {
  products_processed: number;
  processing: number;
  review_required: number;
  completed_today: number;
  failed: number;
  total_products: number;
  source_images: number;
}

export interface QueueData {
  products: ProductSummary[];
  inbox: {
    stabilizing: { name: string; size: number; stable_for: number; waiting_for: string }[];
    unassigned: SourceImage[];
    duplicates: SourceImage[];
    intake_stage: string;
    settle_remaining: number | null;
  };
  worker: { paused: boolean; current_product: number | null; current_stage: string | null; queued: number[]; generation_enabled: boolean; dry_run_reason: string | null };
  jobs: Job[];
}

export interface SettingItem {
  key: string;
  label: string;
  description: string;
  value: string | number | boolean;
  default: string | number | boolean;
  source: "settings" | "env" | "default";
  kind: "text" | "number" | "bool" | "select" | "path";
  choices: string[] | null;
  editable: boolean;
}

export interface ReviewItem {
  generated: GeneratedImage;
  product: { id: number; product_id: string; product_number: number; product_name: string | null; status: ProductStatus };
  sources: SourceImage[];
}

export interface DryRunReport {
  created_at: string;
  raw_folder: string;
  ai_used: boolean;
  files_scanned: number;
  already_registered: { file: string; product_id: string | null; status: string }[];
  duplicates: { file: string; duplicate_of: string }[];
  unreadable: { file: string; error: string }[];
  notes: string[];
  generation: string;
  files_moved: number;
  error?: string;
  groups: {
    attach_to?: string;
    proposed_product_id?: string;
    proposed_number?: number;
    proposed_name?: string;
    proposed_folder?: string;
    files: { file: string; width: number; height: number; format: string; sha256: string; phash: string; colours: string[] }[];
    confidence: number;
    reason: string;
    method?: string;
    analysis?: Partial<Analysis> & { notes?: string[] };
    model_references?: string[];
  }[];
}

export interface DryRun { id: number; status: "RUNNING" | "COMPLETED" | "FAILED"; created_at: string; report: DryRunReport | null }

export interface ReferenceImage { name: string; width: number | null; height: number | null; format: string; size: number; thumb_url: string; full_url: string }
