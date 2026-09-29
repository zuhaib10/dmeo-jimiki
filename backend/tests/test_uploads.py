"""Dashboard uploads land safely in raw images and flow through the normal pipeline."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from synth import DESIGNS, render


def jpeg(design: str = "red_jhumka", variant: int = 0) -> bytes:
    buf = io.BytesIO()
    render(DESIGNS[design], variant).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def post(client: TestClient, *files: tuple[str, bytes]):
    return client.post("/api/uploads", files=[("files", (n, b, "image/jpeg")) for n, b in files])


def test_upload_saves_into_raw_and_processes(env):
    env.use_vision()
    from synth import TRUTH
    TRUTH.update({"IMG_1.JPG": "red_jhumka", "IMG_2.JPG": "red_jhumka"})
    with TestClient(app) as client:
        r = post(client, ("IMG_1.JPG", jpeg()), ("IMG_2.JPG", jpeg(variant=1)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 2 and body["target"] == "raw"
    assert sorted(p.name for p in settings.raw_dir.iterdir() if p.is_file()) == ["IMG_1.JPG", "IMG_2.JPG"]
    assert not list(settings.raw_dir.glob(".upload-*")), "no temp files left behind"
    env.run()
    from test_pipeline import products
    [p] = products()
    assert len(p.sources) == 2 and p.status == "COMPLETED"


def test_upload_never_overwrites_existing_file(env):
    (settings.raw_dir / "IMG_1.JPG").write_bytes(jpeg("green_hoop"))
    before = (settings.raw_dir / "IMG_1.JPG").read_bytes()
    with TestClient(app) as client:
        r = post(client, ("IMG_1.JPG", jpeg()))
    assert r.json()["results"][0]["saved_as"] == "IMG_1 (1).JPG"
    assert (settings.raw_dir / "IMG_1.JPG").read_bytes() == before


def test_upload_rejects_non_images_and_bad_types(env):
    with TestClient(app) as client:
        r = post(client, ("notes.txt", b"hello"), ("fake.jpg", b"not really a jpeg"))
    statuses = [x["status"] for x in r.json()["results"]]
    assert statuses == ["rejected", "rejected"]
    assert not any(settings.raw_dir.glob("*.jpg"))


def test_upload_skips_duplicates(env):
    data = jpeg()
    with TestClient(app) as client:
        r = post(client, ("a.jpg", data), ("b.jpg", data))
        assert [x["status"] for x in r.json()["results"]] == ["saved", "duplicate"]
        env.run()  # a.jpg becomes a product
        r2 = post(client, ("again.jpg", data))
        assert r2.json()["results"][0]["status"] == "duplicate"


def test_upload_sanitises_paths(env):
    with TestClient(app) as client:
        r = post(client, ("../../evil.jpg", jpeg()))
    assert r.json()["results"][0]["saved_as"] == "evil.jpg"
    assert (settings.raw_dir / "evil.jpg").exists()
    assert not (settings.root / "evil.jpg").exists()


def test_reference_upload_goes_to_reference_folder_and_is_used(env):
    with TestClient(app) as client:
        r = client.post("/api/uploads?target=reference",
                        files=[("files", ("earrings-model-01.jpg", jpeg("pink_flower"), "image/jpeg"))])
        assert r.json()["results"][0]["status"] == "saved"
        again = client.post("/api/uploads?target=reference",
                            files=[("files", ("copy.jpg", jpeg("pink_flower"), "image/jpeg"))])
        assert again.json()["results"][0]["status"] == "duplicate"
        refs = client.get("/api/references").json()["items"]
    assert [x["name"] for x in refs] == ["earrings-model-01.jpg"]
    assert not any(settings.raw_dir.glob("*.jpg")), "references never enter the product pipeline"

    # a product uploaded afterwards uses the reference for its model image
    with TestClient(app) as client:
        post(client, ("IMG_9.JPG", jpeg()))
    env.run()
    from test_pipeline import products
    [p] = products()
    model = next(g for g in p.generated if g.image_type == "model")
    assert model.model_reference and model.model_reference.endswith("earrings-model-01.jpg")


def test_reference_added_after_queueing_is_picked_up(env):
    settings.update_override("WORKFLOW_MODE", "dry_run")
    with TestClient(app) as client:
        post(client, ("IMG_9.JPG", jpeg()))
    env.run()
    with TestClient(app) as client:
        client.post("/api/uploads?target=reference", files=[("files", ("model.jpg", jpeg("pink_flower"), "image/jpeg"))])
    settings.update_override("WORKFLOW_MODE", "live")
    from app.services.recovery import requeue_waiting
    requeue_waiting(env.manager)
    env.drain()
    from test_pipeline import products
    [p] = products()
    model = next(g for g in p.generated if g.image_type == "model")
    assert model.model_reference and model.model_reference.endswith("model.jpg")
