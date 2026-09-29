from __future__ import annotations

import threading
import time

from app.config import settings
from app.services.image_converter import fit_to_target
from app.services.product_naming import build_name, file_stem, folder_name
from PIL import Image


def test_naming_pattern_and_conservatism():
    assert build_name("red", "stone-pearl", "jhumka") == "red-stone-pearl-jhumka"
    assert build_name("Silver", "crystal", "hoop earrings") == "silver-tone-crystal-hoop-earrings"
    assert build_name("", "", "jhumka") == "jhumka"
    # unverifiable materials become appearance words; marketing adjectives dropped
    assert build_name("ruby", "beautiful pearl", "jhumka") == "red-stone-pearl-jhumka"
    assert build_name("gold", "elegant pearl", "drop earrings") == "gold-tone-pearl-drop-earrings"
    assert build_name("gold-tone", "gold-tone", "jewellery") == "gold-tone-jewellery"
    assert build_name(None, None, None) == "jewellery"
    assert len(build_name("red", "a b c d e f g h", "earrings").split("-")) <= 6


def test_folder_and_file_names():
    assert folder_name(24, "red-stone-pearl-jhumka") == "24 - red-stone-pearl-jhumka"
    assert file_stem(24, "red-stone-pearl-jhumka") == "24-red-stone-pearl-jhumka"


def test_fit_to_target_exact_size():
    for size in [(1408, 1600), (1024, 1536), (1536, 1024)]:
        for t in ("white_background", "model", "closeup"):
            out = fit_to_target(Image.new("RGB", size, (255, 255, 255)), 1400, 1600, t)
            assert out.size == (1400, 1600)


def test_watcher_waits_for_copy_to_finish(env):
    from app.services.file_watcher import Stabilizer
    from synth import render, DESIGNS
    import io

    buf = io.BytesIO()
    render(DESIGNS["red_jhumka"]).save(buf, "JPEG")
    data = buf.getvalue()
    stable: list = []
    st = Stabilizer(stable.append)
    st.start()
    try:
        target = env.raw / "slow_copy.jpg"
        with open(target, "wb") as f:
            st.track(target)
            for i in range(0, len(data), len(data) // 6):
                f.write(data[i:i + len(data) // 6])
                f.flush()
                time.sleep(0.2)
                assert stable == [], "must not hand over a file that is still being written"
        deadline = time.time() + 5
        while not stable and time.time() < deadline:
            time.sleep(0.1)
        assert stable == [target]
    finally:
        st.stop()


def test_settings_api_hides_key(env):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        body = client.get("/api/settings").json()
        assert body["openai_api_key"] == "Configured"
        assert "test-key-not-real" not in str(body)
        r = client.put("/api/settings", json={"values": {"MAX_RETRIES": 5}})
        assert r.status_code == 200
        assert settings.MAX_RETRIES == 5
        assert client.put("/api/settings", json={"values": {"WORKFLOW_MODE": "bogus"}}).status_code == 400
