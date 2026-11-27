(() => {
  "use strict";

  const formats = [
    { value: "json", label: "JSON / JSONL" }, { value: "csv", label: "CSV / TSV" },
    { value: "image", label: "人工画像" }, { value: "xlsx", label: "Excel（XLSX）" },
    { value: "xml", label: "XML" }, { value: "parquet", label: "Parquet" },
    { value: "avro", label: "Avro" }, { value: "pdf", label: "PDF" },
  ];
  const standard = { path: "files.completion_mode", equals: "manifest-v2" };
  const faultEnabled = { path: "faults.enabled", equals: true };

  function formatSection(format, title, description, fields) {
    return {
      title, description,
      fields: fields.map(field => ({ ...field, when: { path: "files.formats", equals: format } })),
    };
  }

  function expandExample(template, context) {
    if (typeof template !== "string" || template.length > 512) return "";
    return template.replace(/\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]{0,62})(?::(0?[1-9][0-9]?)[d])?\}/g,
      (match, key, width) => {
        if (match === "{{" || match === "}}") return match[0];
        if (!Object.hasOwn(context, key)) return match;
        const value = context[key];
        if (typeof value !== "string" && typeof value !== "number") return match;
        if (!width) return String(value);
        if (!Number.isSafeInteger(value)) return match;
        const digits = String(Math.abs(value));
        const sign = value < 0 ? "-" : "";
        return width.startsWith("0")
          ? sign + digits.padStart(Number(width) - sign.length, "0")
          : (sign + digits).padStart(Number(width), " ");
      });
  }

  function invalidPath(value, filename = false) {
    return !value || value.startsWith("/") || value.split("/").includes("..") ||
      /[\\\x00-\x1f]/.test(value) || (filename && value.includes("/"));
  }

  function summarize(spec, preview) {
    const options = spec.files ?? {};
    const selected = Array.isArray(options.formats) ? options.formats : ["json", "csv", "image"];
    const known = formats.filter(format => selected.includes(format.value));
    const rows = [
      { label: "保存先プロファイル", value: spec.connection_id ?? "default" },
      { label: "選択中の形式", value: known.map(format => format.label).join("、") || "形式を1つ以上指定してください" },
    ];
    const start = spec.start_sequence ?? 0;
    const count = spec.rows ?? 30;
    const products = options.completion_mode === "lakehouse-products-local";
    const exampleRecord = preview?.records?.[0] ?? {};
    const context = {
      ...spec.parameters, ...exampleRecord,
      run_key: spec.run_key ?? "trial-001", job_id: "preview-job",
      sequence: preview?.sample_sequences?.[0] ?? start, record_id: "preview-record",
    };
    const pathContext = {
      ...spec.parameters, run_key: context.run_key, job_id: context.job_id,
    };
    const folder = expandExample(options.path_template ?? "{run_key}/{job_id}", pathContext);
    const base = expandExample(options.filename_template ?? "{sequence:010d}-{record_id}", context);
    if (invalidPath(folder) || invalidPath(base, true)) {
      rows.push({ label: "パスの要確認", value: "相対パスを使用し、.. やバックスラッシュを避けてください。ファイル名に / は使えません。" });
    } else if (products) {
      rows.push({ label: "製品CSVの出力例", value: "csv/" + base + ".csv と、隣接する .csv.ready" });
    } else {
      const compressed = options.compression === "gzip";
      const shown = {
        json: "json/" + base + (options.json_mode === "object" ? "-000000.json" : options.json_mode === "jsonl" ? ".jsonl" : ".json") + (compressed ? ".gz" : ""),
        csv: "csv/" + base + ".csv" + (compressed ? ".gz" : ""),
        image: "images/preview-record." + (options.image?.format === "jpeg" ? "jpg" : "png"),
        xlsx: "xlsx/" + base + ".xlsx",
        xml: "xml/" + base + ".xml" + (compressed ? ".gz" : ""),
        parquet: "parquet/" + base + ".parquet",
        avro: "avro/" + base + ".avro",
        pdf: "pdf/" + base + ".pdf",
      };
      const examples = known.map(format => shown[format.value]);
      if (selected.includes("image") && options.write_sidecars !== false) examples.push("images/preview-record.meta.json");
      if (options.write_manifest !== false) examples.push("manifests/" + base + ".ready.json");
      rows.push({ label: "出力パス例（保存先からの相対）", value: examples.map(example => folder + "/" + example).join("\n") || "—" });
      const filename = options.filename_template ?? "{sequence:010d}-{record_id}";
      if (Number.isSafeInteger(count) && Number.isSafeInteger(options.records_per_file ?? 100) &&
          count > (options.records_per_file ?? 100) && known.some(format => format.value !== "image") &&
          !/\{(?:sequence|record_id)(?=[:}])/.test(filename)) {
        rows.push({ label: "ファイル名の重複確認", value: "複数の分割ファイルに同じ名前が付かないよう {sequence} または {record_id} を含めてください。" });
      }
    }

    const split = options.records_per_file ?? 100;
    const burstRows = spec.burst_rows > 0 ? spec.burst_rows : split;
    const limit = preview && preview.effective_batch_limit && spec.batch_size === split
      ? preview.effective_batch_limit
      : Math.min(split, burstRows);
    if (Number.isSafeInteger(count) && count > 0 && Number.isSafeInteger(limit) && limit > 0) {
      const image = options.image ?? {};
      const every = image.every ?? 1;
      const groups = products ? 1 : Math.ceil(count / limit);
      const images = selected.includes("image") && Number.isSafeInteger(start) && Number.isSafeInteger(every) && every > 0
        ? Math.floor((start + count - 1) / every) - Math.floor((start - 1) / every) : 0;
      const sidecars = images && options.write_sidecars !== false ? images : 0;
      const manifests = options.write_manifest === false ? 0 : groups;
      const documents = known.filter(format => format.value !== "image")
        .reduce((total, format) => total + (format.value === "json" && options.json_mode === "object" ? count : groups), 0);
      rows.push({ label: "成果物数の目安", value: (documents + images + sidecars + manifests).toLocaleString("ja-JP")
        + "ファイル（データ " + documents + "、画像 " + images + "、画像メタ情報 " + sidecars + "、完了通知 " + manifests + "）" });
      if (selected.includes("image")) {
        const pattern = { grid: "格子", gradient: "グラデーション", dots: "ドット" }[image.pattern ?? "grid"];
        rows.push({ label: "画像の設定", value: (image.width ?? 320) + " × " + (image.height ?? 240)
          + " px、" + (image.format === "jpeg" ? "JPEG" : "PNG") + "、" + (pattern ?? "未定義")
          + "、ノイズ " + (image.noise ?? 0) + "、擬似欠陥率 " + ((image.defect_probability ?? 0.05) * 100).toLocaleString("ja-JP", { maximumFractionDigits: 2 }) + "%" });
      }
    }
    if (typeof preview?.file_output?.csv_sample === "string") {
      rows.push({ label: "CSVサンプル（出力先へ書き込みなし）", value: preview.file_output.csv_sample });
    }
    if (selected.includes("json") && Array.isArray(preview?.json_payloads) && preview.json_payloads.length &&
        Number.isSafeInteger(count) && count > 0) {
      const encoder = new TextEncoder();
      const payloadBytes = preview.json_payloads.reduce((sum, payload) => sum
        + encoder.encode(JSON.stringify(payload, null, options.json_pretty && options.json_mode !== "jsonl" ? 2 : 0)).length + 1, 0);
      const bytes = (payloadBytes / preview.json_payloads.length) * count;
      rows.push({ label: "JSON単体の容量目安（非圧縮、サンプル換算）", value: Math.ceil(bytes / 1024).toLocaleString("ja-JP") + " KiB" });
    }
    return rows;
  }

  window.FactoryPresetConfig = {
    title: "ファイル出力の設定",
    description: "出力形式を選ぶと、対応する詳細設定が開きます。分割・ファイル名・完了通知を見本で確認できます。",
    sections: [
      {
        title: "保存先・出力形式",
        fields: [
          { path: "connection_id", label: "保存先プロファイル", type: "connection", default: "default", help: "認証情報は接続設定で管理します。" },
          { path: "files.formats", label: "出力形式（複数可）", type: "multiselect", options: formats, default: ["json", "csv", "image"] },
          { path: "files.completion_mode", label: "完了通知方式", type: "select", default: "manifest-v2", options: [
            { value: "manifest-v2", label: "通常ファイルと .ready.json" },
            { value: "lakehouse-products-local", label: "Lakehouse製品CSV（ローカル専用）" },
          ], help: "製品CSV専用モードには固定の列とファイル名が必要です。専用プリセットを複製して編集してください。" },
          { path: "files.records_per_file", label: "分割あたりの最大レコード数", type: "integer", min: 1, max: 10000, default: 100, help: "サーバーが見本のサイズやバースト設定に応じて分割数を調整します。" },
          { path: "files.path_template", label: "出力フォルダーの書式", type: "text", default: "{run_key}/{job_id}", help: "{job_id}で実行ごとの名前に分けます。絶対パスや .. は使用できません。" },
          { path: "files.filename_template", label: "ファイル名の書式（拡張子なし）", type: "text", default: "{sequence:010d}-{record_id}", help: "バッチの先頭レコード・実行キー・シーケンス・共有パラメーターを参照できます。" },
          { path: "files.compression", label: "外側の圧縮（JSON、CSV、XML）", type: "select", options: [{ value: "none", label: "圧縮しない" }, { value: "gzip", label: "gzip（.gz）" }], default: "none", when: standard },
          { path: "files.write_manifest", label: "全成果物の後に完了マーカーを書く", type: "boolean", default: true, help: "通常形式の .ready.json は成果物の名前・サイズ・SHA256を記録します。" },
        ],
      },
      formatSection("json", "JSON・JSONL", "取込先が想定する構造に合わせます。再構成テンプレートの参照は見本で確認できます。", [
        { path: "files.json_mode", label: "JSONの構造", type: "select", default: "array", options: [
          { value: "array", label: "レコードの配列" }, { value: "object", label: "1レコード1ファイル" }, { value: "jsonl", label: "1行1レコード（JSONL）" },
        ] },
        { path: "files.json_pretty", label: "読みやすく字下げ（JSONLでは適用外）", type: "boolean", default: false },
        { path: "files.json_template", label: "JSONの形を組み替える", type: "json", help: "nullなら元のレコードを使用。例: {\"sensor\":\"$row.entity_id\",\"key\":\"$meta.record_key\"}" },
      ]),
      formatSection("csv", "CSV・TSV", "文字コード、列の順序、NULL表記を確認してから取込先に合わせます。", [
        { path: "files.csv_delimiter", label: "区切り文字", type: "select", default: ",", options: [
          { value: ",", label: "カンマ（,）" }, { value: ";", label: "セミコロン（;）" },
          { value: "\t", label: "タブ（TSV）" }, { value: "|", label: "縦線（|）" },
        ], help: "一覧以外の1文字はJSONで指定できます。" },
        { path: "files.csv_encoding", label: "文字コード", type: "select", default: "utf-8-sig", options: [
          { value: "utf-8", label: "UTF-8（BOMなし）" }, { value: "utf-8-sig", label: "UTF-8（BOM付き、Excel向け）" },
        ] },
        { path: "files.csv_newline", label: "改行コード", type: "select", options: ["LF", "CRLF"], default: "LF" },
        { path: "files.csv_quotechar", label: "引用に使う1文字", type: "text", default: "\"" },
        { path: "files.csv_quoting", label: "引用するセル", type: "select", default: "minimal", options: [
          { value: "minimal", label: "必要なセルのみ" }, { value: "all", label: "すべて" }, { value: "nonnumeric", label: "数値以外" },
        ] },
        { path: "files.csv_header", label: "先頭行に列名を出力", type: "boolean", default: true },
        { path: "files.csv_null_value", label: "NULLの出力文字列", type: "text", default: "", help: "空欄は空文字。例: NULL、\\N。" },
      ]),
      formatSection("image", "人工画像・欠陥メタ情報", "画像は対象シーケンスごとに生成します。同じレコードキーで表データと照合できます。", [
        { path: "files.image.format", label: "画像の形式", type: "select", options: [{ value: "png", label: "PNG（可逆）" }, { value: "jpeg", label: "JPEG（非可逆）" }], default: "png" },
        { path: "files.image.width", label: "幅（px）", type: "integer", min: 32, max: 4096, default: 320 },
        { path: "files.image.height", label: "高さ（px）", type: "integer", min: 32, max: 4096, default: 240 },
        { path: "files.image.pattern", label: "パターン", type: "select", default: "grid", options: [{ value: "grid", label: "格子" }, { value: "gradient", label: "グラデーション" }, { value: "dots", label: "ドット" }] },
        { path: "files.image.every", label: "画像を作る間隔（シーケンス）", type: "integer", min: 1, max: 1000000, default: 1, help: "指定値の倍数のシーケンスだけに生成します。" },
        { path: "files.image.noise", label: "ノイズの強さ", type: "integer", min: 0, max: 64, default: 0 },
        { path: "files.image.defect_probability", label: "擬似欠陥の確率", type: "number", min: 0, max: 1, step: 0.01, default: 0.05, help: "0.05なら約5%の画像に傷状の線を描きます。" },
        { path: "files.image.quality", label: "JPEGの品質", type: "integer", min: 10, max: 100, default: 90, help: "PNGには影響しません。" },
        { path: "files.image.background", label: "背景色 #RRGGBB", type: "text", default: "#182a3a" },
        { path: "files.image.foreground", label: "前景色 #RRGGBB", type: "text", default: "#55a69b" },
        { path: "files.image.defect_color", label: "欠陥線の色 #RRGGBB", type: "text", default: "#ef463c" },
        { path: "files.write_sidecars", label: "画像ごとに .meta.json を追加", type: "boolean", default: true },
      ]),
      formatSection("xlsx", "Excel", "既定では = で始まるデータも文字として保存します。", [
        { path: "files.xlsx_sheet", label: "シート名", type: "text", default: "Data", help: "31文字まで。[: / \\ ? * [ ]] は使用できません。" },
        { path: "files.xlsx_allow_formulas", label: "数式として解釈する出力を許可", type: "boolean", default: false, help: "数式の動作を試験するときだけ有効にします。" },
      ]),
      formatSection("xml", "XML", "テキストをエスケープし、UTF-8で出力します。", [
        { path: "files.xml_root", label: "ルート要素名", type: "text", default: "records" },
        { path: "files.xml_row", label: "レコード要素名", type: "text", default: "record" },
      ]),
      formatSection("parquet", "Parquet", "データ項目から物理型を決め、日時はUTCに揃えます。", [
        { path: "files.parquet_compression", label: "内部圧縮", type: "select", options: ["snappy", "gzip", "zstd", "none"], default: "snappy" },
      ]),
      formatSection("avro", "Avro", "数値・日時の論理型を保持したレコードを作ります。", [
        { path: "files.avro_record_name", label: "レコード名", type: "text", default: "SourceRecord" },
        { path: "files.avro_namespace", label: "名前空間", type: "text", default: "factory.source" },
        { path: "files.avro_codec", label: "内部圧縮", type: "select", options: [{ value: "null", label: "圧縮しない" }, { value: "deflate", label: "deflate" }], default: "deflate" },
      ]),
      formatSection("pdf", "PDF", "指定した順に列をレポートへ載せます。空欄は全列を出力します。", [
        { path: "files.pdf_landscape", label: "A4横向き", type: "boolean", default: true },
        { path: "files.pdf_font_size", label: "文字サイズ（pt）", type: "integer", min: 5, max: 14, default: 8 },
        { path: "files.pdf_columns", label: "出力する列", type: "string-list", help: "列名を出力順に指定します。" },
      ]),
      {
        title: "生成する列とレコード数",
        description: "この並びがCSVヘッダーと各文書の列順になります。生成の詳細条件はJSONで変更できます。",
        fields: [
          {
            path: "fields", label: "生成するデータ項目", type: "table",
            default: { name: "new_column", data_type: "string", generator: "constant", value: "sample" },
            columns: [
              { path: "name", label: "項目名", type: "text" },
              { path: "data_type", label: "型", type: "select", options: ["string", "integer", "number", "decimal", "boolean", "datetime", "json"], default: "string" },
              { path: "generator", label: "生成器", type: "select", options: ["record_id", "uuid", "sequence", "integer", "uniform", "normal", "choice", "boolean", "constant", "template", "timestamp", "reference", "sum", "difference", "product", "ratio"], default: "constant" },
              { path: "value", label: "固定値 JSON", type: "json", when: { path: "generator", equals: "constant" } },
              { path: "minimum", label: "最小値または $参照 JSON", type: "json", when: { path: "generator", equals: ["integer", "uniform"] } },
              { path: "maximum", label: "最大値または $参照 JSON", type: "json", when: { path: "generator", equals: ["integer", "uniform"] } },
              { path: "mean", label: "平均または $参照 JSON", type: "json", when: { path: "generator", equals: "normal" } },
              { path: "stddev", label: "標準偏差または $参照 JSON", type: "json", when: { path: "generator", equals: "normal" } },
              { path: "start", label: "開始値または開始時刻 JSON", type: "json", when: { path: "generator", equals: ["sequence", "timestamp"] }, help: "時刻・共有値の参照は引用符で囲みます。" },
              { path: "step", label: "刻みまたは $参照 JSON", type: "json", when: { path: "generator", equals: ["sequence", "timestamp"] }, help: "時刻系列では秒単位。" },
              { path: "choices", label: "候補の配列または $参照 JSON", type: "json", when: { path: "generator", equals: "choice" } },
              { path: "weights", label: "候補の重み JSON", type: "json", when: { path: "generator", equals: "choice" } },
              { path: "probability", label: "true の確率または $参照 JSON", type: "json", when: { path: "generator", equals: "boolean" } },
              { path: "sources", label: "参照する先行項目", type: "string-list", when: { path: "generator", equals: ["reference", "sum", "difference", "product", "ratio"] } },
              { path: "null_probability", label: "欠損率", type: "number", min: 0, max: 1, step: 0.01, default: 0, },
            ],
          },
          { path: "parameters", label: "共有パラメーター", type: "json", help: "出力パス・ファイル名・値の生成条件が参照できます。" },
          { path: "run_key", label: "実行キー", type: "text", default: "trial-001" },
          { path: "rows", label: "レコード数", type: "integer", min: 1, max: 100000000, default: 30 },
          { path: "max_output_mb", label: "出力容量の上限（MiB）", type: "integer", min: 1, max: 1048576, default: 1024 },
          { path: "target_rate", label: "目標レコード数 / 秒", type: "integer", min: 0, max: 1000000, default: 10, help: "0は制限なし。" },
          { path: "seed", label: "乱数シード", type: "integer", min: 0, max: 2147483647, default: 42 },
          { path: "start_sequence", label: "開始シーケンス", type: "integer", min: 0, max: 1000000000, default: 0 },
          { path: "burst_rows", label: "バーストあたりのレコード数", type: "integer", min: 0, max: 1000000, default: 0 },
          { path: "burst_pause_ms", label: "バーストの間隔（ms）", type: "integer", min: 0, max: 3600000, default: 0 },
        ],
      },
      {
        title: "破損・中断を再現",
        description: "受け取り側が途中のファイルや意図的な破損をどう扱うかを試す設定です。",
        fields: [
          { path: "faults.enabled", label: "障害の設定を有効化", type: "boolean", default: false },
          { path: "faults.artifact_mode", label: "成果物で再現する破損", type: "select", default: "none", when: faultEnabled, options: [
            { value: "none", label: "なし" }, { value: "truncate_json", label: "JSON切り詰め" },
            { value: "invalid_json_utf8", label: "JSONの不正なUTF-8" }, { value: "csv_header_mismatch", label: "異なるCSVヘッダー" },
            { value: "truncate_image", label: "画像切り詰め" }, { value: "wrong_image_extension", label: "画像形式と拡張子の不一致" },
            { value: "manifest_checksum_mismatch", label: "完了マーカーのチェックサム不整合" },
          ] },
          { path: "faults.stage", label: "処理停止・遅延の発生位置", type: "select", when: faultEnabled, default: "before_batch", options: [{ value: "before_batch", label: "ファイル書き込み前" }, { value: "after_write", label: "書き込み後、完了マーカー前" }] },
          { path: "faults.action", label: "処理停止・遅延", type: "select", when: faultEnabled, default: "none", options: [{ value: "none", label: "なし" }, { value: "fail", label: "処理失敗" }, { value: "delay", label: "遅延" }] },
          { path: "faults.from_batch", label: "最初に適用するバッチ", type: "integer", min: 1, max: 100000000, when: faultEnabled },
          { path: "faults.every_batches", label: "発生間隔（バッチ）", type: "integer", min: 1, max: 100000000, when: faultEnabled },
          { path: "faults.max_occurrences", label: "最大発生回数", type: "integer", min: 0, max: 1000000, when: faultEnabled },
          { path: "faults.delay_ms", label: "遅延（ms）", type: "integer", min: 0, max: 60000, when: { path: "faults.action", equals: "delay" } },
          {
            path: "anomalies", label: "データ異常ルール", type: "table",
            default: { name: "異常注入", field: "", operation: "null", from_row: 0, probability: 1 },
            columns: [
              { path: "name", label: "ルール名", type: "text", default: "異常注入" },
              { path: "enabled", label: "有効", type: "boolean", default: true },
              { path: "field", label: "対象項目名", type: "text" },
              { path: "operation", label: "操作", type: "select", default: "null", options: [
                { value: "null", label: "欠測（NULL）" }, { value: "omit", label: "項目を省く" }, { value: "freeze", label: "値を固定" },
                { value: "set", label: "指定値に置換" }, { value: "add", label: "加算" }, { value: "multiply", label: "倍率" },
                { value: "ramp", label: "徐々に増減" }, { value: "copy_from", label: "別の行をコピー" }, { value: "timestamp_shift", label: "時刻をずらす" },
              ] },
              { path: "value", label: "異常値 JSON", type: "json", when: { path: "operation", equals: ["set", "add", "multiply", "ramp", "copy_from", "timestamp_shift"] } },
              { path: "from_row", label: "開始位置（0始まり）", type: "integer", min: 0, max: 100000000, default: 0 },
              { path: "to_row", label: "終了位置（空欄は最後まで）", type: "integer", min: 0, max: 100000000 },
              { path: "every", label: "何行ごとに適用するか", type: "integer", min: 1, max: 100000000, default: 1 },
              { path: "probability", label: "適用確率", type: "number", min: 0, max: 1, step: 0.01, default: 1 },
              { path: "bypass_validation", label: "意図的に型や制約を外す", type: "boolean", default: false },
              { path: "when", label: "適用条件 JSON", type: "json", help: "空欄なら条件を指定しません。" },
            ],
          },
        ],
      },
    ],
    summarize,
  };
})();
