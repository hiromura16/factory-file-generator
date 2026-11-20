# ファイル生成ツール / JSON・CSV・画像・業務文書

実装の入口・データの流れ・各処理の境界は、[日本語コード読解ガイド](CODE_GUIDE.ja.md)を参照してください。

任意のデータ項目からJSON、CSV、人工的な検査パターン画像、XLSX、XML、Parquet、Avro、PDFを生成します。

| 接続方式 | 実装 |
| --- | --- |
| ローカル / マウント済み共有 | /landing等へ保存。構成例ではローカルのランディング領域を使用 |
| NFS | ホストで事前マウントしたNFSv3/v4をアプリへ渡す。実際のNFSマウントと任意のマウント元を検証 |
| SMB / Samba | SMB2/3で直接接続。ユーザー・ドメイン・パスワード、条件を整えたKerberos。暗号化を既定で要求 |
| FTP / FTPS | FTP、Explicit/Implicit FTPS、アクティブ/パッシブ、文字コード、ユーザー/匿名認証 |
| SFTP | SSHによる直接接続。パスワード、RSA/ECDSA/Ed25519鍵。known_hostsまたはSHA256によるホスト鍵検証 |
| S3互換 | R2等の既存バケットへ直接保存。明示的なアクセスキー認証 |

SMBとNFSは別方式です。SMBはアプリから直接接続します。NFSのマウントはホストで行い、compose.network-shares.yamlを使って/network/nfsへ渡せます。アプリ自体にはmount権限を与えません。FILE_ALLOWED_ROOTS（既定/landing:/network）の下へ保存します。NFS/共有側のUID/GIDとアプリUID1000を合わせてください。DockerイメージにはSMB Kerberos用GSSAPI拡張も含めます。利用には有効なチケットとドメイン設定が必要です。Dockerを使わず直接起動する場合はOSのKerberos開発パッケージを導入し、uv sync --extra kerberosで拡張を追加してください。

FTP/SFTP/SMBは一時ファイルの保存後に確定名へ変更します。JSON/CSV/画像を保存してから、SHA256・サイズ・レコードキーを含む.ready.jsonを最後に作成。下流はこの完了マニフェストを取り込みの目印にできます。ファイル名にはjob_id等を含め、他の書き込み処理と衝突させないでください。サーバー実装によってrenameの並行実行時の保証は異なります。S3では条件付きPutObjectを要求します。

JSONは配列・1件1ファイル・JSONL、任意の入れ子構造、整形、gzipを設定。CSVは区切り、引用規則、ヘッダー、NULL表現、改行、UTF-8/BOMを設定。画像は幅・高さ・PNG/JPEG・色・パターン・ノイズ・疑似欠陥率・間引きを設定します。画像は人工パターンで、外部の写真検索やAI生成APIは使用しません。

# Data Source Studio 共通仕様

3つのツールはそれぞれ独立したリポジトリ・アプリ・永続ボリュームです。設定は業種に依存しません。v2では接続プロファイル、任意のデータ項目、生成ルール、実行条件を画面で編集できます。

## 画面の使い方

1. 「接続先」で方式と認証を選び、保存してから接続テストします。方式ごとの説明を常に表示します。各項目にマウスを重ねるか「?」へキーボードで移動すると説明が出ます。
2. 「データをつくる」で任意の項目を追加し、型と生成器を指定。詳細条件で分布、重み、NULL率、参照、計算、条件上書きを設定します。
3. 共有パラメーターをJSONで定義し、`$名前`で生成条件から参照できます。項目は上から順に生成します。
4. 「プレビュー」で接続・書き込みなしに最大12件の正常位置・異常位置を確認し、生成を開始します。履歴から停止と設定の再編集ができます。
5. 「この設定を保存…」でJSON編集へ進み、プリセットIDを指定して保存。JSONの読み込み・書き出しにも対応します。

プリセットには接続IDだけを保存します。パスワード、接続文字列、トークン、SSH秘密鍵等は接続プロファイルへ暗号化保存し、読み戻しと書き出しには含めません。空欄は同じ接続先の既存値を維持し、接続先や認証方式が変わると以前の資格情報を引き継ぎません。明示的な削除はclear_secretsで指定します。

証明書、Wallet、known_hosts、DSN設定等は接続先画面からconnection-filesへ配置できます。ファイル自体は権限600で保存します。これらのファイルと設定暗号鍵を含む永続ボリュームのバックアップは保護してください。公開リポジトリには保存しません。

## 自由なデータ定義

型はstring / integer / number / decimal / boolean / datetime / json。生成器はrecord_id、uuid、sequence、integer、uniform、normal、choice、boolean、constant、template、timestamp、reference、sum、difference、product、ratio。

日時はタイムゾーン付きISO形式を指定し、UTCへ変換します。decimalはJSONでは正確な文字列、SQLではNUMERICへ変換します。テンプレートは`DEV-{entity_no:05d}`のような先行項目・共有値・sequence・record_idの参照に対応し、任意Pythonは実行しません。JSON構造テンプレートは`$row.項目` / `$parameters.名前` / `$meta.record_key`等を参照し、入れ子のオブジェクトと配列を定義できます。

項目名・SQL識別子は英字/下線で始まる英数字/下線、63文字まで。主キーはNULL不可。1レコードのJSONは1MiBまで、1実行は1億レコードまでです。拡張契約は`contracts/preset.schema.json` / `contracts/connection.schema.json`にあります。

## 大量生成

62種類の編集可能なJSONプリセットを同梱。通常24種類、MES/ERP等の異常24種類、業務ファイル14種類を用意。少量・大量生成、分布・派生値・書式に加え、固着・ドリフト・時刻ずれ・数量不整合・処理障害等を設定できます。件数、開始位置、乱数seed、目標速度、バッチ件数、時間・容量上限、初期待機、ゆらぎ、バースト件数・待機を変更できます。目標速度0は速度制限なし。実速度は生成内容・接続先・ディスクに依存します。

実行は各ツール1件ずつ。全件をメモリには保持せず、行サイズに合わせてバッチを縮小します。ホストのメモリ余力・ディスク余力を監視し、閾値に達した場合は停止します。設定と履歴は永続化し、プロセス再起動で中断された実行はinterruptedになります。実行開始時に接続プロファイルの版を固定します。

## ローカル起動

Python 3.12とuv、Docker Composeを使用します。

```sh
uv sync --frozen
uv run python scripts/init_local.py --private-dir ./private --data-dir ./data --env-file .env
docker compose up -d --build
```

`.env`でポートと必要なマウント元を設定します。管理画面・操作API・メトリクスはログイン不要です。`uv run uvicorn app:app`で直接起動する場合はDATA_DIRを設定してください。

`DATA_DIR=/data`に設定DB、暗号鍵、実行履歴を保存。接続用ファイルの配置先はCONNECTION_FILES_DIR（既定DATA_DIR/connection-files）です。秘密鍵と設定DBを一緒に公開しないでください。

## APIと検証

ログイン不要のAPI: `/api/info`、`/api/schemas`、`/api/connections`、`/api/presets`、`/api/preview`、`/api/runs`。プロファイル保存後の`/api/connections/{id}/test`は保存済み設定を検証します。`/healthz`と`/metrics`も認証不要です。UIとAPIの変更操作は同一オリジンとJSONを要求します。

```sh
uv run pytest -q
uvx ruff check .
```

v0.1の固定MES/ERP設定JSONはv2の任意項目定義へ置き換えました。過去の実行履歴・SQLテーブル・出力ファイル・旧outboxは保持します。旧outboxの未配送データは新しい接続先へ自動転送しません。

実接続のスモークテストは FILE_TEST_PROFILES_FILE に非公開の接続プロファイル配列JSONを指定して実行できます。検証用の配送先を用意し、資格情報ファイルはGitへ追加しないでください。

プリセットの一覧はpresets/README.md、再生成は `uv run python scripts/build_presets.py --kind files`。新規プリセットだけを初回起動時に登録し、保存済みプリセットを更新で上書きしません。

## 異常シナリオと説明

プリセット選択時には、想定状況・発生データとタイミング・確認事項・実行条件を表示します。既存の通常プリセットにも説明を用意しています。異常ルールは正常行の生成後に適用し、対象の項目、適用開始/終了位置、頻度、確率、比較条件を変更できます。ルールは上から順に適用します。

データの異常があるだけでジョブが失敗するとは限りません。制約のないDBやメッセージブローカーは、不整合な業務データも受け付けるため、取り込み側の品質チェックで検知する試験です。型や必須項目の不正、処理障害のプリセットでは意図的にfailedで終了するケースもあります。

処理障害はこの実行だけの例外・待機で再現し、外部サーバーを停止したりネットワーク設定を変更しません。SQLはCOMMIT前に障害を入れるとそのバッチをrollback、ファイルは完了前に止めるとready.jsonなし、ストリームはACK後の異常でpendingを残し、同じrecord_keyの重複再送を検証できます。ストリームの自動再試行は実行条件で上書き可能です。

その他のデータソース（OPC UA、Modbus、業務API、SECS/GEM、履歴再生等）の追加案は[データソース拡張計画](docs/source-roadmap.md)を参照してください。

## 業務ファイルの追加形式

XLSXは列順・シート名を設定し、文字列を既定で数式として実行しない形で保存。XMLはUTF-8、ルートと行の要素名、NULL属性を指定。Parquetは列型を明示しSnappy/Gzip/Zstandard/無圧縮を選択。Avroはnullable型・decimal・UTC時刻のスキーマを同梱しdeflate/無圧縮を選択。PDFは日本語対応の複数ページの表で、列選択・縦横・フォントサイズを設定できます。

JSON/CSV/画像と同じバッチの.ready.jsonに、追加形式のサイズ・SHA256も記録します。CSVやXLSXの小数は文字列として保持する一方、Parquet/Avroではdecimal型に変換します。画像は人工パターン、PDFはデータ表であり、実際の検査装置の書式をそのまま再現するものではありません。

業務ファイルプリセットの再生成: `uv run python scripts/build_document_presets.py`。追加済みの設備・API・ヒストリアン・ログ・RFIDツールは[データソース構成](docs/architecture.md)を参照してください。

## 外部監視用の管理ラベル

Composeの管理ラベルは既定で `factory.guard.managed=true` です。外部監視側との合意が必要な場合は、非公開の配備設定で `FACTORY_GUARD_LABEL_PREFIX` を指定できます。例えば説明用の値 `synthetic-guard` を指定すると、`synthetic-guard.guard.managed=true` になります。

既存の外部監視との互換性は未確認です。変更前に監視側が照合するラベル名と影響を確認してください。この設定は既存コンテナや監視設定を自動移行せず、デプロイも行いません。

## Dokployでの配置とプロキシ

[Dokploy配置手順](docs/dokploy.md)に、準備・環境変数・永続化・非公開アクセス・確認・更新と、ビルド/実行時のプロキシの違いをまとめています。
