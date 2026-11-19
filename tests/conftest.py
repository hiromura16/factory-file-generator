# モジュール読解: 実環境の設定を引き継がないテスト用保存領域と進捗コンテキストを用意する。
import pytest


# 読解 isolated: 外部設定の環境変数を取り除き、各テストの保存領域を一時ディレクトリへ向ける。
@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in [
        "APP_PASSWORD_FILE",
        "SQL_HOST",
        "MQTT_HOST",
        "LANDING_DIR",
        "PROFILE_KEY_FILE",
        "FILE_ALLOWED_ROOTS",
        "SQLITE_ROOT",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("APP_PASSWORD", raising=False)


# 読解 Context: 実行管理を起動せずに出力パスと累計進捗を観測できるテスト用スタブ。
class Context:
    run_id = "test-job"
    outputs = []

    # 読解 Context.__init__: テスト実行ごとに空の出力一覧と進捗を作り、別実行と混ざらないようにする。
    def __init__(self):
        self.stats = {}
        self.outputs = []

    # 読解 Context.check: 実ホストの資源検査や停止要求を再現せず、生成処理を先へ進める。
    def check(self):
        pass

    # 読解 Context.wait: 実時間の待機を行わず、出力検証をタイミングに依存させない。
    def wait(self, ms):
        pass

    # 読解 Context.progress: 報告された差分を項目別に加えて、最終実績を表明できるようにする。
    def progress(self, **values):
        for k, v in values.items():
            self.stats[k] = self.stats.get(k, 0) + v

    # 読解 Context.pace: 本番の速度制限を省略し、件数だけで進む合成実行にする。
    def pace(self, completed):
        pass
