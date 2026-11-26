"""初回のUIとファイル生成の入口を、隔離した一時領域で検証する。"""
from test_operation_gate import confirmed_post

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
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
        assert 'id="previewFormats"' in html
        assert 'id="sqlActions"' not in html
        assert "作成DDL" not in html
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

@pytest.mark.parametrize(
    ("formats", "expected"),
    [
        (["json"], {"json"}),
        (["csv"], {"csv"}),
        (["image"], {"images"}),
        (["csv", "image"], {"csv", "images"}),
        (["json", "csv", "image"], {"json", "csv", "images"}),
    ],
)
def test_preview_and_local_run_use_only_selected_formats(tmp_path, formats, expected):
    """プレビューと確定ファイルを比較し、未選択の形式が出ないことを検証する。"""
    app = create_app("files", "試験", file_adapter)
    with TestClient(app) as client:
        spec = client.get("/api/info").json()["defaults"]
        spec.update(rows=3, target_rate=0)
        spec["files"].update(formats=formats, write_sidecars=False)
        # 未選択の本文テンプレートは、関係のないCSV／画像を妨げない。
        spec["stream"]["payload_template"] = "$row.missing_stream_field"
        if "json" not in formats:
            spec["files"]["json_template"] = "$row.missing_json_field"
        preview = client.post("/api/preview", json=spec)
        assert preview.status_code == 200, preview.text
        view = preview.json()
        assert view["writes"] is False
        assert view["file_output"]["formats"] == formats
        assert bool(view["json_payloads"]) == ("json" in formats)
        assert ("csv_sample" in view["file_output"]) == ("csv" in formats)
        assert ("image" in view["file_output"]) == ("image" in formats)
        assert client.get("/api/runs").json()["runs"] == []
        assert not list((tmp_path / "exports").glob("**/*"))

        run = confirmed_post(client,"/api/runs", json=spec)
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]
        app.state.runtime.thread.join(timeout=10)
        result = client.get(f"/api/runs/{run_id}").json()
        assert result["status"] == "completed", result
        folder = tmp_path / "exports" / spec["run_key"] / run_id
        assert {child.name for child in folder.iterdir() if child.is_dir()} == expected | {"manifests"}
        marker = json.loads(next((folder / "manifests").glob("*.ready.json")).read_text())
        assert {artifact["path"].split("/")[0] for artifact in marker["artifacts"]} == expected
        if "json" in formats:
            assert json.loads(next((folder / "json").glob("*.json")).read_text()) == view["json_payloads"]
        if "csv" in formats:
            output = next((folder / "csv").glob("*.csv")).read_bytes().decode("utf-8-sig")
            assert output == view["file_output"]["csv_sample"]
        if "image" in formats:
            images = list((folder / "images").glob("*.png"))
            assert len(images) == view["file_output"]["image"]["count"] == 3
            with Image.open(images[0]) as image:
                assert image.size == (
                    view["file_output"]["image"]["width"], view["file_output"]["image"]["height"],
                )
            assert not list((folder / "images").glob("*.json"))
