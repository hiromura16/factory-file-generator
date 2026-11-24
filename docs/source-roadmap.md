# データソースの構成と拡張候補

## 実装済みのソース

SQL書き込み、ファイル出力、ストリーム送信、設備の読取り、業務API、ヒストリアン、ログ、作業者・RFID・バーコードは、個別のアプリで試せます。提供する方式と収集側の組み合わせは[データソース構成例](architecture.md)から各リポジトリを参照してください。

| 試験対象 | 確認する項目 |
| --- | --- |
| 設備の読取り | OPC UAのノード、Modbusレジスターの型・倍率・バイト順、固着や読取り停止 |
| 業務API | ページング、更新差分、再取得、429/503、期限切れ認証、欠落・重複 |
| ヒストリアン | 履歴の欠落、再取得、重複バックフィル、期間集計と生値の違い |
| ファイル | Excel・XML・Parquet・Avro・PDFを含む形式ごとの列型、文字コード、分割、破損 |
| ログ | 回転ファイルとSyslogの取込、時刻やイベントIDでの照合、重複 |
| 作業者・RFID・バーコード | 二重読み、読み飛ばし、順序逆転、別ロットへの誤登録 |

## 検討中のソース

| データソース | 検討する試験 |
| --- | --- |
| SECS/GEM / HSMS | 設備・ホスト間の状態遷移、接続復帰、タイムアウト、イベント収集条件とIDの不一致 |
| MTConnect | Agent/Adapterの再接続、観測値の更新停止、状態コードと時刻 |
| 検査装置・高頻度波形 | 振動・電流・音等のサンプリング欠落、時刻ずれ、画像や波形と検査結果の関連付け |

## プロトコルの参照先

OPC UAには値の監視に加え、アラームと履歴アクセスの仕様があります。設備側をサーバーとして用意し、収集クライアントの挙動を検証する対象に適しています。[OPC Foundationの仕様](https://reference.opcfoundation.org/specs/OPC-10000-1/full)

Modbusはclient/serverとgatewayの仕様を持つため、既存のメッセージ送信ツールとは異なる「読み出される設備」を試せます。[Modbus Organizationの仕様一覧](https://www.modbus.org/modbus-specifications)

半導体設備向けでは、SEMI E30はGEMの通信・制御モデル、E5はSECS-IIのメッセージ内容に対応します。[SEMI E30の公式説明](https://store-us.semi.org/products/e03000-semi-e30-specification-for-the-generic-model-for-communications-and-control-of-manufacturing-equipment-gem)

MTConnectは製造設備のデータを共通化する規格で、独自信号を変換するAdapterを含む構成を取ります。[MTConnect公式](https://mtconnect.org/)

## アプリを組み合わせる試験

これらを同じ検証シナリオで動かす場合は、equipment_id / work_order_id / lot_id / product_id等の業務キー、event_time、取り込み時刻、技術的なrecord_keyを分けて扱います。正常・異常の正解ラベルは業務データに混ぜず、別の試験結果として保持する設計を推奨します。そうすると、正常系の集計に加えて「設備異常→MES実績不整合→ERP在庫ずれ」を横断的に検証できます。
