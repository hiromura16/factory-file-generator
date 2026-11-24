"""初回のUIとファイル生成の入口を、隔離した一時領域で検証する。"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import file_adapter
from factorylib import create_app


def test_quickstart_fixed_local_json(tmp_path):
    """任意の編集値を送っても、JSONだけを既定の場所に30件生成する。"""
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs/quickstart",
            json={"rows": 99, "files": {"formats": ["csv"]}, "connection_id": "other"},
        )
        assert response.status_code == 202, response.text
        run = response.json()
        app.state.runtime.thread.join(timeout=10)
        result = client.get(f"/api/runs/{run['id']}").json()
        assert result["status"] == "completed", result
        assert result["stats"]["processed"] == 30
        assert result["spec"]["connection_revision"] == 1
        folder = tmp_path / "exports" / "quickstart-json" / run["id"]
        files = list((folder / "json").glob("*.json"))
        assert len(files) == 1
        assert len(json.loads(files[0].read_text())) == 30
        assert not (folder / "csv").exists()
        assert not (folder / "images").exists()


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_quickstart_rejects_reconfigured_default(tmp_path, kind):
    """既定IDの名前が同じでも、別の場所や外部出力には書き込まない。"""
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        response = client.post(
            "/api/connections",
            json={
                "id": "default",
                "name": "試験出力先",
                "kind": kind,
                "directory": str(tmp_path / "unexpected"),
                "bucket": "unused-test",
            },
        )
        assert response.status_code == 200, response.text
        assert client.post("/api/runs/quickstart", json={}).status_code == 409
        assert client.get("/api/runs").json()["runs"] == []
        assert not (tmp_path / "unexpected").exists()




def test_quickstart_rejects_another_profile_name_for_app_directory(tmp_path):
    """同じ場所を指していても、別のパスを既定の出力先として扱わない。"""
    landing = tmp_path / "exports"
    landing.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(landing, target_is_directory=True)
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        response = client.post(
            "/api/connections",
            json={"id": "default", "name": "試験", "kind": "local", "directory": str(alias)},
        )
        assert response.status_code == 200
        assert client.post("/api/runs/quickstart", json={}).status_code == 409
        assert list(landing.iterdir()) == []


def test_quickstart_rejects_unavailable_trusted_landing(tmp_path, monkeypatch):
    """アプリ設定のパスが固定ディレクトリとして開けなければ受付しない。"""
    landing = tmp_path / "exports"
    landing.touch()
    monkeypatch.setenv("LANDING_DIR", str(landing))
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        assert client.post("/api/runs/quickstart", json={}).status_code == 422
        assert client.get("/api/runs").json()["runs"] == []


def test_quickstart_stays_with_open_directory(tmp_path, monkeypatch):
    """運用上の名前変更後でも、受付時のディレクトリ参照だけを使用する。"""
    landing = tmp_path / "exports"
    original = tmp_path / "saved-exports"
    handle = file_adapter.handle

    def move_then_run(spec, ctx, profile, store, *, local_root_fd):
        landing.rename(original)
        landing.mkdir()
        assert os.fstat(local_root_fd).st_ino == original.stat().st_ino
        return handle(spec, ctx, profile, store, local_root_fd=local_root_fd)

    monkeypatch.setattr(file_adapter, "handle", move_then_run)
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        response = client.post("/api/runs/quickstart", json={})
        assert response.status_code == 202
        run_id = response.json()["id"]
        app.state.runtime.thread.join(timeout=10)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
        assert list(landing.iterdir()) == []
        assert len(list((original / "quickstart-json" / run_id / "json").glob("*.json"))) == 1


def test_update_and_delete_labels_do_not_write_during_preview():
    """既存の重み付き生成を使い、100%削除でも削除処理は実行しない。"""
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        spec = client.get("/api/info").json()["defaults"]
        spec["fields"].append(
            {
                "name": "file_event_operation",
                "generator": "choice",
                "choices": ["insert", "update", "delete"],
                "weights": [0, 0, 100],
            }
        )
        result = client.post("/api/preview", json=spec).json()
        assert result["writes"] is False
        assert {row["file_event_operation"] for row in result["records"]} == {"delete"}
        assert client.get("/api/runs").json()["runs"] == []

def test_generation_stages_and_frontend_contract():
    """配信HTMLの順序と、実行モデルへ書き戻すJavaScriptを検証する。"""
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        html = client.get("/").text
        assert 'id="quickstart"' in html
        assert html.index('id="runEditor"') < html.index("Step 4.") < html.index("Step 5.")
        preview = client.post(
            "/api/preview", json={"rows": 3, "files": {"formats": ["image"]}}
        )
        assert preview.status_code == 200
        assert preview.json()["writes"] is False
        assert client.get("/api/runs").json()["runs"] == []
    node = shutil.which("node")
    if not node:
        pytest.skip("画面ロジックの検査にはNode.jsが必要です")
    script = Path(__file__).with_name("generation_flow.test.cjs")
    subprocess.run([node, "--test", str(script)], check=True, timeout=30)
