# JIMIKI Image Studio

Local Windows application that turns raw jewellery photographs into three controlled ecommerce images per product, while keeping the jewellery itself unchanged.

Upload photographs from the dashboard, or copy them into `raw images\`. The app:

1. Detects each file and waits until it has finished copying.
2. Hashes it and skips duplicates.
3. Groups photographs that show the same physical product.
4. Assigns a permanent ID (`JMK-000024`) and number (`24`).
5. Analyses and names the product (`24 - red-stone-pearl-jhumka`).
6. Generates the white-background, model and close-up images.
7. Validates them against the source photographs.
8. Saves PNG masters and WebP copies.
9. Archives the raw files, only after the product succeeds.

Every state change is stored in SQLite and streamed live to the dashboard.

## Setup (Windows)

Requirements: Python 3.11+ and Node.js 20+.

```bat
setup.bat          :: creates backend\.venv, installs deps, builds the dashboard, creates backend\.env
notepad backend\.env   :: set OPENAI_API_KEY and/or REPLICATE_API_TOKEN (and JIMIKI_ROOT if different)
run.bat            :: starts http://127.0.0.1:8000 and opens the browser
```

Image generation uses OpenAI by default. Set `IMAGE_PROVIDER=replicate` (or change it on the Settings page) to use
Replicate's `xai/grok-imagine-image` (~$0.02/image) with `REPLICATE_API_TOKEN`. That model edits a single input image, so
only the sharpest product photo is sent and model reference photos are not used. Product analysis and integrity
checks still use OpenAI when `OPENAI_API_KEY` is set.

Missing folders under `JIMIKI_ROOT` are created automatically:

```
<JIMIKI_ROOT>\raw images\                     ← drop photographs here
<JIMIKI_ROOT>\raw images\completed images\    ← sources are moved here after success
<JIMIKI_ROOT>\reference model images\         ← model/pose/lighting references (optional)
<JIMIKI_ROOT>\Product Images\24 - red-stone-pearl-jhumka\
      24-red-stone-pearl-jhumka-white-background.png / .webp
      24-red-stone-pearl-jhumka-model.png / .webp
      24-red-stone-pearl-jhumka-closeup.png / .webp
```

Other scripts:
- `dev.bat` runs the backend with auto-reload plus the Vite dev server on :5173.
- `run_tests.bat` runs the test suite.

## Using it

1. **Reference Models** page: upload model reference photos. They are used only for the model's look, pose, framing and lighting in the model and close-up images, never for the product.
2. **Overview** (Product Inbox): drag product photos onto the upload box or click it to choose files (JPG, PNG, HEIC, WebP).
   - Uploads are saved into `raw images\`. You can also copy files into that folder directly; both paths are processed the same way.
   - Byte-identical photos already in the studio are skipped.
   - An existing file with the same name is never overwritten; the upload gets a " (1)" suffix.
3. Products appear within seconds. Open **Image Studio** to follow generation.

JIMIKI product photos show the item on a branded display card over a stone prop. The prompts tell the image model that the card, logo and props are not part of the product, and the AI integrity check flags any output that still shows them.

## Workflow and states

`DISCOVERED → ANALYZING → GROUPING → NAMING → QUEUED → GENERATING_WHITE → GENERATING_MODEL → GENERATING_CLOSEUP → VALIDATING → COMPLETED`

Other outcomes: `PARTIAL`, `FAILED` or `REVIEW_REQUIRED`.

Progress is derived from completed stages, never from timers.

| Concern | Behaviour |
|---|---|
| Copy in progress | A file is registered only after its size and mtime are stable for `FILE_STABLE_SECONDS` and it opens as an image. |
| Batching | Grouping runs once no new file has arrived for `BATCH_SETTLE_SECONDS`. |
| Grouping | AI vision decides which photos show the same product, and deterministic colour checks can veto a merge. It is conservative: below `GROUPING_CONFIDENCE_THRESHOLD`, photos stay separate products. Without AI, only near-identical shots (bursts or re-exports) are merged, because every JIMIKI photo shares the same card and logo. A new photo of an existing product attaches to it (after AI confirmation) and keeps its number. |
| Numbering | Atomic `UPDATE … RETURNING` counter inside the product-creation transaction, backed by unique constraints. Numbers are never reused. |
| Naming | `[colour]-[feature]-[type]`. Marketing adjectives are removed. Unverifiable materials become appearance words (ruby → red-stone, gold → gold-tone). |
| Validation | Deterministic checks: file, dimensions, aspect, empty image, size, product visible, cropping, white background, dominant colour, visual similarity. Plus an optional AI integrity comparison (`ENABLE_PRODUCT_SIMILARITY_CHECK`). Technical failures are regenerated automatically. Integrity concerns go to **Review Required**. |
| Retries | Exponential backoff for transient API errors. At most `1 + MAX_RETRIES` attempts per image per run. Every attempt is recorded with safe provider metadata. |
| Idempotency | Completed images are never regenerated. A partial failure retries only the failed image. Byte-identical files are ignored and left in place. |
| Restart | Interrupted jobs are marked, images caught mid-generation return to `PENDING`, and the queue resumes. |
| Raw files | Opened read-only, never modified or deleted. Moved (never overwritten; cross-volume moves are hash-verified) only after the product is `COMPLETED`. |

## Dry run

- **No key for the selected image provider (`OPENAI_API_KEY` / `REPLICATE_API_TOKEN`), or `WORKFLOW_MODE=dry_run`:** products are ingested, grouped, analysed, numbered, named and queued. The UI shows **DRY RUN — IMAGE GENERATION DISABLED**. No images are generated and no files move. Switching to live later picks up the queued products.
- **Run Dry Scan (Overview page):** previews how everything currently in `raw images` would be processed, including groups, proposed numbers, names and model references. It creates nothing, consumes no numbers and moves nothing.

## Live demo script

1. Open the dashboard (`run.bat`) and check that the sidebar shows API, Database and Watcher as Online and the header shows **LIVE**.
2. Optional: press **Run Dry Scan** first to show the plan without spending API calls.
3. Copy 3–5 photographs of one piece (plus one photo of a different piece) into `raw images\`.
4. The Product Inbox shows *Files Detected*, then *Grouping Images*. Two products appear with IDs, numbers, names and analysis.
5. Open **Image Studio** to watch white → model → close-up → validation live.
6. The product ends as **COMPLETED**, with its source files moved to `completed images\<folder>`, or as **REVIEW_REQUIRED** with the reasons listed. Every step appears in **Logs**.

## Architecture

```
backend/app/
  main.py              FastAPI app, lifespan (DB init, recovery, workers, watcher), serves dashboard
  config.py            settings: defaults < .env < Settings page (DB)
  database.py          SQLite (WAL), sessions, versioned migrations, events published after commit
  models/              products, source_images, generated_images, generation_attempts, jobs,
                       audit_events, settings, sequences, product_analysis, validation_results, dry_runs
  api/                 products & review actions, system/queue/settings/audit, media, SSE events
  services/
    file_watcher.py    watchdog + copy stabilizer + periodic rescan
    image_ingestion.py hashing, duplicate detection, registration, features
    features.py        segmentation, colours, pHash/dHash, ORB, previews (OpenCV/Pillow)
    image_grouping.py  conservative grouping + existing-product matching
    product_analyzer.py / product_naming.py / product_numbering.py
    ai_provider.py     ALL OpenAI code (Responses structured output + Images edit); swap providers here
    image_generator.py prompt assembly, reference selection
    image_validator.py integrity validation
    image_converter.py exact 1400×1600 output, atomic PNG/WebP writes
    file_archiver.py   safe raw-file moves
    job_manager.py     intake worker + sequential generation worker
    recovery.py / dry_run.py / workflow.py / audit_logger.py / events.py
  prompts/*.txt        versioned prompts (version stored per generated image)
frontend/              React + TypeScript + Vite + Tailwind; live via Server-Sent Events
```

## Notes

- **Output size.** Outputs are exactly `IMAGE_WIDTH × IMAGE_HEIGHT`, 1400 × 1600 by default as the spec requires. That ratio is 7:8 rather than a strict 4:5; set 1280 × 1600 for exact 4:5. With `gpt-image-2`, the image is generated at the nearest size divisible by 16 (1408 × 1600) and trimmed. Older models generate at 1024 × 1536 and are fitted: padded on white for the white-background image, centre-cropped for the others.
- **Model references.** The model and close-up images both use them; the close-up is a tight detail shot worn on the model. Images whose filenames contain the product category (e.g. `earrings-01.jpg`, `hair-clip-02.jpg`) are preferred for that category. Otherwise references rotate by product number. References added after a product was queued are still picked up when it is generated.
- **Without AI vision,** grouping and analysis fall back to deterministic colour and shape features. The UI labels these results *Colour-based only*.
# dmeo-jimiki
