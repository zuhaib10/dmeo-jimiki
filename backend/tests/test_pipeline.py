"""End-to-end workflow tests (acceptance tests 1-6 + safety/idempotency)."""
from __future__ import annotations

import shutil
import threading

from PIL import Image
from sqlalchemy import select

from app import database
from app.config import settings
from app.database import session_scope
from app.models import (GeneratedImage, GenStatus, Product, ProductStatus, SourceImage, SourceStatus,
                        ValidationResult)
from app.services.features import sha256_file
from app.services.job_manager import JobManager
from app.services.product_numbering import allocate_product_number
from app.services.recovery import recover
from synth import write


def products() -> list[Product]:
    with session_scope() as s:
        rows = s.scalars(select(Product).order_by(Product.product_number)).all()
        for p in rows:
            _ = p.sources, p.generated, p.analysis
        return rows


def gen_status(p: Product) -> dict[str, str]:
    return {g.image_type: g.status for g in p.generated}


def assert_outputs(p: Product) -> None:
    for g in p.generated:
        for path in (g.png_path, g.webp_path):
            with Image.open(path) as im:
                assert im.size == (settings.IMAGE_WIDTH, settings.IMAGE_HEIGHT)
    folder = settings.output_dir / p.folder_name
    assert len(list(folder.glob("*.png"))) == 3
    assert len(list(folder.glob("*.webp"))) == 3


# --------------------------------------------------------------------- Test 1
def test_single_product_single_photo(env):
    src = write("red_jhumka", env.raw, "IMG_0001.JPG")
    original_hash = sha256_file(src)
    env.run()
    [p] = products()
    assert p.product_id == "JMK-000001" and p.product_number == 1
    assert p.status == ProductStatus.COMPLETED, gen_status(p)
    assert gen_status(p) == {"white_background": "COMPLETED", "model": "COMPLETED", "closeup": "COMPLETED"}
    assert_outputs(p)
    assert p.folder_name == f"1 - {p.product_name}"
    stem = f"1-{p.product_name}"
    assert (settings.output_dir / p.folder_name / f"{stem}-white-background.png").exists()
    assert (settings.output_dir / p.folder_name / f"{stem}-model.webp").exists()
    # raw moved only after success, bytes untouched
    assert not src.exists()
    archived = settings.completed_dir / p.folder_name / "IMG_0001.JPG"
    assert archived.exists() and sha256_file(archived) == original_hash
    assert p.sources[0].status == SourceStatus.ARCHIVED
    assert p.sources[0].original_path == str(src.resolve())


# --------------------------------------------------------------------- Test 2
def test_one_product_five_photos(env):
    env.use_vision()
    for v in range(5):
        write("green_hoop", env.raw, f"IMG_10{v}.JPG", variant=v)
    env.run()
    [p] = products()
    assert len(p.sources) == 5
    assert p.grouping_confidence is not None and p.grouping_reason
    assert len(p.generated) == 3
    assert p.status == ProductStatus.COMPLETED, gen_status(p)


# --------------------------------------------------------------------- Test 3
def test_three_different_products(env):
    write("red_jhumka", env.raw, "a.jpg")
    write("green_hoop", env.raw, "b.jpg")
    write("blue_drop", env.raw, "c.jpg")
    env.run()
    ps = products()
    assert len(ps) == 3
    assert sorted(p.product_number for p in ps) == [1, 2, 3]
    assert len({p.product_id for p in ps}) == 3
    assert all(len(p.sources) == 1 for p in ps)


# --------------------------------------------------------------------- Test 4
def test_restart_does_not_regenerate(env):
    write("red_jhumka", env.raw, "a.jpg")
    env.run()
    calls = len(env.provider.calls)
    assert calls == 3
    # simulate a fresh process
    m2 = JobManager()
    recover(m2)
    env.manager = m2
    env.run()
    assert len(env.provider.calls) == calls
    [p] = products()
    assert p.status == ProductStatus.COMPLETED


def test_restart_mid_generation_resumes_only_missing(env):
    write("red_jhumka", env.raw, "a.jpg")
    env.ingest()
    # pretend we crashed while generating the model image, after white completed
    pk = products()[0].id
    env.provider.fail = {"model": "permanent"}
    env.drain()
    with session_scope() as s:
        gi = s.scalars(select(GeneratedImage).where(GeneratedImage.product_id == pk,
                                                    GeneratedImage.image_type == "model")).one()
        gi.status = GenStatus.GENERATING
        s.get(Product, pk).status = ProductStatus.GENERATING_MODEL
    env.provider.fail = {}
    env.provider.calls.clear()
    m2 = JobManager()
    recover(m2)
    env.manager = m2
    env.drain()
    assert env.provider.calls == ["model"]
    assert products()[0].status == ProductStatus.COMPLETED


# --------------------------------------------------------------------- Test 5
def test_partial_failure_keeps_successes_and_sources(env):
    src = write("blue_drop", env.raw, "a.jpg")
    env.provider.fail = {"model": "permanent"}
    env.run()
    [p] = products()
    assert gen_status(p) == {"white_background": "COMPLETED", "model": "FAILED", "closeup": "COMPLETED"}
    assert p.status == ProductStatus.PARTIAL
    assert src.exists(), "source must stay in raw images until the product completes"
    white_png = next(g.png_path for g in p.generated if g.image_type == "white_background")
    white_hash = sha256_file(white_png)

    # operator retries only the failed image
    env.provider.fail = {}
    env.provider.calls.clear()
    with session_scope() as s:
        gi = s.scalars(select(GeneratedImage).where(GeneratedImage.product_id == p.id,
                                                    GeneratedImage.image_type == "model")).one()
        gi.status = GenStatus.PENDING
    env.manager.enqueue_generation(p.id)
    env.drain()
    assert env.provider.calls == ["model"]
    [p] = products()
    assert p.status == ProductStatus.COMPLETED
    assert sha256_file(white_png) == white_hash, "completed assets must not be regenerated"
    assert not src.exists()


# --------------------------------------------------------------------- Test 6
def test_new_photo_attaches_to_existing_product(env):
    env.use_vision()
    write("pink_flower", env.raw, "first.jpg", variant=0)
    env.run()
    [p] = products()
    assert p.status == ProductStatus.COMPLETED
    calls = len(env.provider.calls)
    write("pink_flower", env.raw, "later.jpg", variant=0)  # re-shot, same framing
    # make it a different file (not byte-identical) of the same product
    img = Image.open(env.raw / "later.jpg")
    img.rotate(3, fillcolor=img.getpixel((5, 5))).save(env.raw / "later.jpg", quality=88)
    env.run()
    [p2] = products()
    assert p2.product_number == p.product_number and p2.product_id == p.product_id
    assert len(p2.sources) == 2
    assert len(env.provider.calls) == calls, "completed assets are not regenerated"
    assert all(s.status == SourceStatus.ARCHIVED for s in p2.sources)
    assert (settings.completed_dir / p2.folder_name / "later.jpg").exists()


# ------------------------------------------------------------------ retries
def test_transient_failures_are_retried_with_backoff(env):
    write("red_jhumka", env.raw, "a.jpg")
    env.provider.fail = {"white_background": 2}
    env.run()
    [p] = products()
    white = next(g for g in p.generated if g.image_type == "white_background")
    assert white.status == GenStatus.COMPLETED
    assert white.generation_attempts == 3
    with session_scope() as s:
        gi = s.get(GeneratedImage, white.id)
        assert [a.status for a in gi.attempts] == ["ERROR", "ERROR", "SUCCESS"]
        assert gi.attempts[0].provider_metadata["status_code"] == 503


def test_retries_are_bounded(env):
    settings.update_override("MAX_RETRIES", 1)
    write("red_jhumka", env.raw, "a.jpg")
    env.provider.fail = {"closeup": 99}
    env.run()
    [p] = products()
    close = next(g for g in p.generated if g.image_type == "closeup")
    assert close.status == GenStatus.FAILED
    assert close.generation_attempts == 2
    assert p.status == ProductStatus.PARTIAL


# ------------------------------------------------------------ idempotency
def test_duplicate_source_file_is_ignored_and_untouched(env):
    a = write("red_jhumka", env.raw, "a.jpg")
    shutil.copy(a, env.raw / "a copy.jpg")
    env.run()
    [p] = products()
    assert len(p.sources) == 1
    with session_scope() as s:
        dup = s.scalars(select(SourceImage).where(SourceImage.status == SourceStatus.DUPLICATE)).one()
        assert dup.original_filename in ("a.jpg", "a copy.jpg")
    assert (env.raw / dup.original_filename).exists(), "duplicates are left untouched in raw images"
    # re-scan does not re-register anything
    env.run()
    assert len(products()) == 1


def test_already_processed_raw_image_is_not_reprocessed(env):
    a = write("red_jhumka", env.raw, "a.jpg")
    data = a.read_bytes()
    env.run()
    calls = len(env.provider.calls)
    (env.raw / "a.jpg").write_bytes(data)  # same photo dropped again
    env.run()
    assert len(products()) == 1
    assert len(env.provider.calls) == calls


# -------------------------------------------------------------- naming/numbers
def test_same_product_name_collision(env):
    # two different designs with identical deterministic names → distinct folders, nothing overwritten
    write("red_jhumka", env.raw, "a.jpg")
    env.run()
    write("red_jhumka", env.raw, "b.jpg", variant=0)
    img = Image.open(env.raw / "b.jpg")
    # a different product with a similar colour profile but very different geometry
    img.transpose(Image.FLIP_TOP_BOTTOM).resize((600, 1500)).save(env.raw / "b.jpg", quality=85)
    with session_scope() as s:
        s.get(Product, products()[0].id).product_name  # noqa: B018
    env.run()  # deterministic mode never attaches a non-identical photo to an existing product
    ps = products()
    assert len(ps) == 2
    assert ps[0].product_name == ps[1].product_name
    assert ps[0].folder_name != ps[1].folder_name
    paths = {g.png_path for p in ps for g in p.generated}
    assert len(paths) == 6


def test_product_numbers_unique_under_concurrency(env):
    results: list[int] = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            with session_scope() as s:
                n = allocate_product_number(s)
            with lock:
                results.append(n)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 120
    assert sorted(results) == list(range(1, 121))


# ------------------------------------------------------------- review/safety
def test_review_required_keeps_sources_and_approval_completes(env):
    src = write("red_jhumka", env.raw, "a.jpg")
    env.provider.recolour = {"white_background"}
    env.run()
    [p] = products()
    white = next(g for g in p.generated if g.image_type == "white_background")
    assert white.status == GenStatus.REVIEW_REQUIRED, white.review_reason
    assert "colour" in (white.review_reason or "").lower()
    assert p.status == ProductStatus.REVIEW_REQUIRED
    assert src.exists()
    with session_scope() as s:
        v = s.scalars(select(ValidationResult).where(ValidationResult.generated_image_id == white.id)).first()
        assert v.result == "REVIEW_REQUIRED" and v.flags

    # operator approves
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post(f"/api/generated/{white.id}/approve")
        assert r.status_code == 200, r.text
    # the API enqueues finalization on the process-wide manager (stopped when TestClient exits);
    # run the same finalization on the test manager
    env.manager.enqueue_generation(p.id)
    env.drain()
    [p] = products()
    assert p.status == ProductStatus.COMPLETED
    assert not src.exists()


def test_reject_preserves_source(env):
    src = write("red_jhumka", env.raw, "a.jpg")
    before = sha256_file(src)
    env.provider.recolour = {"model"}
    env.run()
    [p] = products()
    model = next(g for g in p.generated if g.image_type == "model")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        if model.status == GenStatus.REVIEW_REQUIRED:
            assert client.post(f"/api/generated/{model.id}/reject").status_code == 200
        else:  # colour-presence check may still pass for editorial shots → operator marks + rejects
            assert client.post(f"/api/generated/{model.id}/review").status_code == 200
            assert client.post(f"/api/generated/{model.id}/reject").status_code == 200
    [p] = products()
    assert p.status == ProductStatus.PARTIAL
    assert src.exists() and sha256_file(src) == before


def test_raw_files_never_modified(env):
    files = [write(n, env.raw, f"{n}.jpg") for n in ("red_jhumka", "green_hoop")]
    hashes = {f.name: sha256_file(f) for f in files}
    env.run()
    for p in products():
        for s in p.sources:
            assert sha256_file(s.current_path) == hashes[s.original_filename]


def test_dry_run_mode_stops_at_queued(env):
    settings.update_override("WORKFLOW_MODE", "dry_run")
    src = write("red_jhumka", env.raw, "a.jpg")
    env.run()
    [p] = products()
    assert p.status == ProductStatus.QUEUED
    assert p.product_name and p.analysis is not None
    assert env.provider.calls == []
    assert src.exists()
    # switching to live resumes from QUEUED
    settings.update_override("WORKFLOW_MODE", "live")
    from app.services.recovery import requeue_waiting
    requeue_waiting(env.manager)
    env.drain()
    assert products()[0].status == ProductStatus.COMPLETED


def test_dry_scan_creates_nothing(env):
    env.use_vision()
    write("red_jhumka", env.raw, "a.jpg")
    write("red_jhumka", env.raw, "b.jpg", variant=1)
    write("blue_drop", env.raw, "c.jpg")
    from app.services.dry_run import run_dry_scan
    report = run_dry_scan()
    assert report["files_scanned"] == 3
    assert [g["proposed_number"] for g in report["groups"]] == [1, 2]
    assert sorted(len(g["files"]) for g in report["groups"]) == [1, 2]
    assert products() == []
    assert env.provider.calls == []
    assert len(list(env.raw.glob("*.jpg"))) == 3
    with session_scope() as s:
        from app.services.product_numbering import peek_next_numbers
        assert peek_next_numbers(s, 1) == [1]  # nothing consumed


# ------------------------------------------------------ real-photo regressions
def test_different_products_on_same_display_card_stay_separate(env):
    """JIMIKI photos share an identical branded card; that must never merge different products."""
    for i, d in enumerate(["red_jhumka", "green_hoop", "blue_drop", "pink_flower"]):
        write(d, env.raw, f"card_{i}.jpg", card=True)
    env.run()
    ps = products()
    assert len(ps) == 4
    assert all(len(p.sources) == 1 for p in ps)


def test_near_identical_shots_merge_without_ai(env):
    a = write("red_jhumka", env.raw, "burst_1.jpg")
    from PIL import ImageEnhance
    # burst shot / re-export: same framing, slightly different exposure and compression
    ImageEnhance.Brightness(Image.open(a)).enhance(1.04).save(env.raw / "burst_2.jpg", quality=80)
    env.run()
    [p] = products()
    assert len(p.sources) == 2


def test_ai_grouping_merges_card_photos_of_same_product(env):
    env.use_vision()
    write("pink_flower", env.raw, "p1.jpg", card=True)
    write("pink_flower", env.raw, "p2.jpg", variant=2, card=True)
    write("green_hoop", env.raw, "g1.jpg", card=True)
    env.run()
    ps = products()
    assert sorted(len(p.sources) for p in ps) == [1, 2]
    assert all(p.analysis.source == "ai" for p in ps)
