// 設定画面の入口。APIのスキーマから編集欄を作り、接続・生成・履歴の操作をサーバーへ渡す。
"use strict";
const $ =
  /* 指定したセレクターに最初に一致するDOM要素を返す。一致しなければnull。 */
  (s) => document.querySelector(s),
  clone =
    /* 設定を構造化複製し、編集が保存済みの元オブジェクトへ波及するのを避ける。 */
    (v) => structuredClone(v);
const state = {
  info: null,
  schemas: null,
  spec: null,
  connections: [],
  presets: [],
  profile: null,
  current: null,
};
let uid = 0;
let previewRevision = 0;
const el =
  /* タグと属性からDOM要素を作る。on接頭辞はイベント登録に使い、本文はHTMLとして解釈しない。 */
  (tag, attrs = {}, text = "") => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  if (text) n.textContent = text;
  return n;
};
// 通知欄の文面を差し替え、失敗通知だけにエラー表示のクラスを付ける。
function notify(text, error = false) {
  $("#notice").textContent = text;
  $("#notice").className = error ? "error" : "";
}
// 本文なしはGET、ありはJSON POSTで同一画面のAPIを呼ぶ。JSONを返し、HTTP失敗は詳細付き例外に変換する。
async function api(path, body) {
  const r = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const value = await r.json();
  if (!r.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail || value),
    );
  return value;
}
const action =
  /* ボタンを操作関数へ接続し、実行中の再操作防止と通知を共通化する。 */
  (id, fn) =>
  $(id).addEventListener("click", /* クリック既定動作を止め、処理中は押下不可にする。失敗は通知し、成否にかかわらず再度有効にする。 */ async (e) => {
    e.preventDefault();
    const b = e.currentTarget;
    b.disabled = true;
    try {
      await fn();
    } catch (err) {
      notify(err.message, true);
    } finally {
      b.disabled = false;
    }
  });
// 指定IDのタブと対応するナビ項目だけを選択状態にし、切替前のヘルプを閉じる。
function tab(id) {
  const panels = [$("#historyList"), $("#currentJob")];
  for (const panel of panels) rememberJobPanes(panel);
  document
    .querySelectorAll(".tab")
    .forEach(/* 実際のタブ要素はDOMのIDで選択状態を判定する。 */ (n) => n.classList.toggle("active", n.id === id));
  document
    .querySelectorAll(".nav")
    .forEach(/* ナビ項目はdata-tabで選択先との一致を判定する。 */ (n) => n.classList.toggle("active", n.dataset.tab === id));
  hideTip();
  for (const panel of panels) restoreJobPanes(panel);
}
// スキーマ内のローカル参照をルートの定義から解決する。参照でなければ元を返す。
function deref(schema, root) {
  return schema.$ref ? root.$defs[schema.$ref.split("/").pop()] : schema;
}
// 参照を解決し、明示既定値、固定値、null許容、型に応じてフォーム初期値を組み立てる。
function defaultValue(schema, root) {
  schema = deref(schema, root);
  if ("default" in schema) return clone(schema.default);
  if (schema.const !== undefined) return schema.const;
  if (schema.anyOf) {
    const choice = schema.anyOf.find(/* 複数候補からnull型を探し、任意項目が未設定で開始できるようにする。 */ (v) => v.type === "null");
    return choice ? null : defaultValue(schema.anyOf[0], root);
  }
  if (schema.type === "object")
    return Object.fromEntries(
      Object.entries(schema.properties || {}).map(/* オブジェクトの各プロパティ名と、再帰的に確定した初期値の組を返す。 */ ([k, v]) => [
        k,
        defaultValue(v, root),
      ]),
    );
  if (schema.type === "array") return [];
  if (schema.enum) return schema.enum[0];
  return schema.type === "boolean"
    ? false
    : schema.type === "integer" || schema.type === "number"
      ? 0
      : "";
}
// 折りたたみ可能な見出し・説明・入力領域を作り、外枠と追加先を別に返す。
function block(title, description, open = true) {
  const box = el("details", { class: "card" });
  box.open = open;
  box.append(el("summary", {}, title));
  if (description) box.append(el("p", { class: "muted" }, description));
  const body = el("div", { class: "grid" });
  box.append(body);
  return [box, body];
}
// スキーマの型やフィールド名に応じた入力要素を作り、妥当な変更をsetへ渡す。制約と補足を表示する。
function control(name, schema, value, set, root) {
  const outer = el("div", {
    class: "control hint",
    "data-help": schema.description || schema.title || name,
  });
  let shape = deref(schema, root);
  const id = "input-" + ++uid,
    label = el("label", { for: id }, schema.title || name);
  label.append(
    el(
      "span",
      {
        class: "help",
        tabindex: "0",
        "aria-label": (schema.title || name) + "の説明",
        "aria-describedby": "tooltip",
      },
      "?",
    ),
  );
  outer.append(label);
  const variants = schema.anyOf?.map(/* 複合型の各候補を解決し、参照先の型も描画方式の判定に使う。 */ (v) => deref(v, root));
  const obj = variants?.find(/* 複合型に入力欄を持つオブジェクト候補があるか判定する。 */ (v) => v.type === "object" && v.properties);
  if (obj) {
    const check = el("input", { type: "checkbox", id });
    check.checked = value !== null && value !== undefined;
    const checklabel = el("label", { class: "checklabel" });
    checklabel.append(check, document.createTextNode("条件を有効にする"));
    outer.append(checklabel);
    const inner = el("div", { class: "grid" });
    outer.append(inner);
    /* 任意の入れ子条件を再描画する。有効化時は未設定値を初期化し、無効化時はモデルへnullを返す。 */ function draw() {
      inner.replaceChildren();
      if (check.checked) {
        if (value == null) value = defaultValue(obj, root);
        set(value);
        renderObject(inner, obj, value, root);
      } else {
        value = null;
        set(null);
      }
    }
    check.addEventListener("change", draw);
    draw();
    outer.classList.add("subsection");
    return outer;
  }
  if (shape.type === "object" && shape.properties) {
    outer.classList.add("subsection");
    const inner = el("div", { class: "grid" });
    outer.append(inner);
    renderObject(inner, shape, value, root);
    return outer;
  }
  if (name === "connection_id") {
    const input = el("select", { id });
    for (const p of state.connections)
      input.append(el("option", { value: p.id }, p.name + " · " + (state.info.connection_help[p.kind]?.label || p.kind)));
    if (!state.connections.some(/* 編集中の接続IDが登録一覧にあるか確認する。なければ入力値を消さず未登録として表示する。 */ (p) => p.id === value))
      input.append(el("option", { value }, value + " (未登録)"));
    input.value = value;
    input.addEventListener("change", /* 接続先の変更時に、表示名ではなく選択されたプロファイルIDを記録する。 */ () => set(input.value));
    outer.append(input);
    return outer;
  }
  let input;
  const isJson =
    shape.type === "array" ||
    shape.type === "object" ||
    (!shape.type && !shape.enum && !schema.anyOf) ||
    variants?.some(/* 複合型に配列候補があればJSON入力欄として扱う。 */ (v) => v.type === "array");
  if (shape.enum) {
    input = el("select", { id });
    for (const v of shape.enum)
      input.append(
        el(
          "option",
          { value: String(v) },
          name === "kind"
            ? state.info.connection_help[v]?.label || v
            : ["database_auth", "file_auth"].includes(name)
              ? name === "database_auth" &&
                state.profile?.kind === "sqlserver" &&
                v === "password"
                ? "SQL Server 認証"
                : authLabels[v] || v
              : String(v),
        ),
      );
    input.value = String(value ?? shape.default ?? shape.enum[0]);
    input.addEventListener("change", /* 列挙選択の値を文字列として編集中のモデルへ渡す。 */ () => set(input.value));
  } else if (shape.type === "boolean") {
    input = el("input", { id, type: "checkbox" });
    input.checked = Boolean(value);
    input.addEventListener("change", /* チェック状態を文字列ではなく真偽値として保存する。 */ () => set(input.checked));
  } else if (isJson) {
    input = el("textarea", {
      id,
      rows: name === "parameters" || name.includes("template") ? "6" : "3",
      spellcheck: "false",
    });
    input.value = JSON.stringify(
      value === undefined ? defaultValue(schema, root) : value,
      null,
      2,
    );
    input.addEventListener("input", /* 有効なJSONだけをモデルに反映する。不正な編集中テキストは残し、フォーム側で送信を止める。 */ () => {
      try {
        const next = JSON.parse(input.value);
        input.setCustomValidity("");
        set(next);
      } catch {
        input.setCustomValidity("JSONの構文を確認してください");
      }
    });
    outer.append(
      el(
        "span",
        { class: "json-hint" },
        'JSONで編集 · 文字列は " "、空値は null',
      ),
    );
  } else if (name === "description") {
    input = el("textarea", { id, rows: "6" });
    input.value = value ?? "";
    input.maxLength = 1000;
    input.addEventListener("input", /* 説明欄の入力を加工せず編集モデルへ反映する。 */ () => set(input.value));
  } else if (["ssh_private_key", "connection_string"].includes(name)) {
    input = el("textarea", {
      id,
      rows: "4",
      spellcheck: "false",
      autocomplete: "off",
    });
    input.value = value ?? "";
    input.placeholder = "空欄は同じ接続先の保存済み値を維持";
    input.addEventListener("input", /* 複数行の認証入力を取り込み、空欄を明示の空文字ではなくnullにする。 */ () => set(input.value || null));
  } else {
    const numeric = shape.type === "integer" || shape.type === "number";
    const secret =
      shape.format === "password" ||
      schema.format === "password" ||
      variants?.some(/* 複合型候補にもパスワード形式が含まれるか調べる。 */ (v) => v.format === "password");
    input = el("input", {
      id,
      type: secret ? "password" : numeric ? "number" : "text",
      autocomplete: secret ? "new-password" : "off",
    });
    if (numeric) {
      input.step = shape.type === "integer" ? "1" : "any";
      for (const [k, a] of [
        ["minimum", "min"],
        ["maximum", "max"],
      ])
        if (shape[k] !== undefined) input[a] = shape[k];
    }
    if (shape.minLength) input.minLength = shape.minLength;
    if (shape.maxLength) input.maxLength = shape.maxLength;
    input.value = value ?? "";
    input.addEventListener("input", /* 秘密欄・複合型・数値で変換規則を分ける。複合型はJSONにならない入力も文字列として維持する。 */ () => {
      let v = input.value;
      if (secret) {
        set(v || null);
        return;
      }
      if (variants) {
        if (v === "") {
          set(null);
          return;
        }
        try {
          v = JSON.parse(v);
        } catch {}
      } else if (numeric) v = Number(v);
      set(v);
    });
    if (secret) input.placeholder = "空欄は保存済みの値を維持";
  }
  outer.append(input);
  return outer;
}
// 表示対象の各プロパティを描画し、モデルで未設定の項目だけ初期値を補う。
function renderObject(container, schema, model, root, exclude = []) {
  for (const [name, prop] of Object.entries(schema.properties || {})) {
    if (exclude.includes(name)) continue;
    if (!(name in model)) model[name] = defaultValue(prop, root);
    container.append(
      control(name, prop, model[name], /* 個別コントロールの更新を対応するモデルのプロパティへ書き戻す。 */ (v) => (model[name] = v), root),
    );
  }
}
// 主な形式は独立して選べる。既存の追加形式は「その他」に残す。
const FILE_FORMATS = [
  ["json", "JSON"], ["csv", "CSV"], ["image", "画像（合成）"],
  ["xlsx", "Excel"], ["xml", "XML"], ["parquet", "Parquet"],
  ["avro", "Avro"], ["pdf", "PDF"],
];

// プレビュー前にも、何をいくつどこへ出すかを確認できるようにする。
function updateFileSelection() {
  const formats = state.spec.files.formats.map((key) => FILE_FORMATS.find(([id]) => id === key)?.[1] || key);
  const profile = state.connections.find((item) => item.id === state.spec.connection_id);
  $("#selectionSummary").textContent =
    (formats.join("・") || "形式未選択") + " ／ " + state.spec.rows + "件 ／ " + (profile?.name || "出力先未登録") +
    (state.spec.files.formats.includes("image") && state.spec.files.write_sidecars ? " ／ 画像の付帯JSONあり" : "");
  $("#preview").disabled = $("#start").disabled = !formats.length;
  invalidateFilePreview();
}

// 編集中または応答待ちのプレビューを、現在の設定の表示と混同しない。
function invalidateFilePreview() {
  previewRevision += 1;
  $("#previewPanel").hidden = true;
}

// 新規画面は未選択から始め、プリセットを選んだときだけ保存済み形式を復元する。
function newFileSpec() {
  const spec = clone(state.info.defaults);
  spec.files.formats = [];
  spec.files.write_sidecars = false;
  for (const field of spec.fields) delete field.primary_key;
  return spec;
}

// 画面に共通契約のうちファイル生成に不要な設定を出さない。
function fileSpecView(spec) {
  if (state.info?.kind !== "files") return spec;
  const view = clone(spec);
  delete view.sql;
  delete view.stream;
  for (const field of view.fields || []) delete field.primary_key;
  return view;
}

// 主要形式はチェックでも単独選択ボタンでも変更できる。別の形式を暗黙に追加しない。
function renderFileFormats(groups) {
  const inputs = new Map();
  for (const [index, [key, label]] of FILE_FORMATS.entries()) {
    const input = el("input", { type: "checkbox", value: key });
    input.checked = state.spec.files.formats.includes(key);
    inputs.set(key, input);
    input.addEventListener("change", () => {
      const previous = state.spec.files.formats.filter((value) => value !== key);
      state.spec.files.formats = input.checked ? [...previous, key] : previous;
      refreshFileFormats();
    });
    const option = el("label", { class: "format-option" });
    option.append(input, document.createTextNode(label));
    if (index < 3) {
      const only = el("button", { type: "button", class: "ghost", "data-only-format": key }, ["JSONだけ", "CSVだけ", "画像だけ"][index]);
      only.addEventListener("click", () => {
        state.spec.files.formats = [key];
        if (key === "image") state.spec.files.write_sidecars = false;
        for (const [value, checkbox] of inputs) checkbox.checked = value === key;
        refreshFileFormats();
      });
      const card = el("div", { class: "format-card" });
      card.append(option, only);
      groups[0].append(card);
    } else {
      groups[1].append(option);
    }
  }
  function refreshFileFormats() {
    updateFileSelection();
    const details = $("#fileFormatOptions");
    if (details) renderSelectedFileOptions(details, state.schemas.run);
  }
}

// 選んでいる形式に必要な設定だけを表示する。完了印はデータ形式の選択と別扱い。
function renderSelectedFileOptions(body, root) {
  body.replaceChildren();
  const files = state.spec.files;
  const schema = deref(root.properties.files, root);
  const selected = files.formats;
  const include = new Set([
    "completion_mode", "records_per_file", "path_template", "filename_template", "write_manifest",
  ]);
  if (selected.some((key) => ["json", "csv", "xml"].includes(key))) include.add("compression");
  for (const key of selected) {
    for (const option of Object.keys(schema.properties)) {
      if (option.startsWith(key + "_")) include.add(option);
    }
  }
  if (selected.includes("image")) {
    include.add("image");
    include.add("write_sidecars");
  }
  for (const key of Object.keys(schema.properties)) {
    if (!include.has(key)) continue;
    const prop = key === "write_sidecars"
      ? {...schema.properties[key], title: "画像の付帯JSONも保存（任意）"}
      : schema.properties[key];
    body.append(control(key, prop, files[key], (value) => {
      files[key] = value;
      updateFileSelection();
    }, root));
  }
}

// 更新・削除はファイル操作ではなく、既存の生成ルールで付ける任意のラベル。
const FILE_OPERATION_FIELD = "file_event_operation";
const FILE_OPERATIONS = ["insert", "update", "delete"];

// この画面が扱えるルールだけを識別し、同名の手作業ルールは書き換えない。
function fileOperationRule(field) {
  return field?.name === FILE_OPERATION_FIELD && field.generator === "choice" &&
    field.data_type === "string" && field.primary_key === false &&
    field.null_probability === 0 && field.condition === null &&
    JSON.stringify(field.choices) === JSON.stringify(FILE_OPERATIONS) &&
    Array.isArray(field.weights) && field.weights.length === 3 &&
    field.weights.every((value) => Number.isInteger(value) && value >= 0) &&
    field.weights.reduce((sum, value) => sum + value, 0) === 100;
}

// 既存スキーマの重み付き選択へ保存するため、設定の再読込やプレビューでも同じ結果になる。
function applyFileOperationRates(update, remove, root) {
  if (![update, remove].every((value) => Number.isInteger(value) && value >= 0) ||
      update + remove > 100) return "更新率と削除率の合計は100%以下の整数にしてください。";
  const fields = state.spec.fields;
  const index = fields.findIndex((field) => field.name.toLowerCase() === FILE_OPERATION_FIELD);
  const existing = fields[index];
  if (existing && !fileOperationRule(existing))
    return "同名の独自項目があるため、更新率・削除率を設定できません。";
  if (!update && !remove) {
    if (existing) fields.splice(index, 1);
    return "";
  }
  const field = {
    ...(existing || defaultValue(root.$defs.FieldRule, root)),
    name: FILE_OPERATION_FIELD,
    data_type: "string",
    generator: "choice",
    primary_key: false,
    null_probability: 0,
    condition: null,
    choices: FILE_OPERATIONS.slice(),
    weights: [100 - update - remove, update, remove],
  };
  if (existing) fields[index] = field;
  else fields.push(field);
  return "";
}

// 割合欄の変更を検証し、正常時だけ生成項目・プレビューへ反映する。
function renderFileOperationRates(body, root, refreshFields) {
  const existing = state.spec.fields.find(fileOperationRule);
  const inputs = ["更新率（%）", "削除率（%）"].map((label, index) => {
    const item = el("label", {}, label);
    const input = el("input", { type: "number", min: "0", max: "100", step: "1", required: "" });
    input.value = existing?.weights[index + 1] ?? 0;
    item.append(input);
    body.append(item);
    return input;
  });
  const change = () => {
    const message = applyFileOperationRates(...inputs.map((input) => input.valueAsNumber), root);
    inputs.forEach((input) => input.setCustomValidity(message));
    if (!message) {
      refreshFields();
      updateFileSelection();
    }
  };
  inputs.forEach((input) => input.addEventListener("input", change));
  if (state.spec.fields.some((field) => field.name.toLowerCase() === FILE_OPERATION_FIELD && !fileOperationRule(field))) {
    inputs.forEach((input) => { input.disabled = true; });
    body.append(el("p", { class: "muted" }, "同名の独自生成項目を優先しています。"));
  }
}

// ファイル生成の基本操作を三段階に揃え、高度な項目は個別に展開できるようにする。
function renderFileRun(target, root) {
  const props = root.properties;
  const [formats, formatBody] = block("Step 1. 投入するファイル形式", "JSON・CSV・画像を個別に選びます。「だけ」は他の形式をすべて外します。チェック欄で必要な組み合わせも選べます。");
  formatBody.classList.add("format-choices");
  const [extra, extraBody] = block("その他の形式", "", false);
  renderFileFormats([formatBody, extraBody]);
  formats.append(extra);
  const [settings, body] = block("Step 2. 生成する件数", "はじめは少ない件数で出力の見本を確認できます。");
  body.append(control("rows", props.rows, state.spec.rows, (value) => {
    state.spec.rows = value;
    updateFileSelection();
  }, root));
  const [details, detailsBody] = block("詳細設定", "生成ルール、ファイル形式の詳細、異常データ、処理障害を設定します。", false);
  details.id = "fileAdvanced";
  detailsBody.classList.add("advanced-stack");
  const [advanced, advancedBody] = block("生成速度と再現性", "シード値を揃えると同じデータを再現できます。", false);
  renderObject(advancedBody, root, state.spec, root, [
    "version", "rows", "connection_id", "connection_revision", "task",
    "parameters", "fields", "anomalies", "faults", "sql", "files", "stream",
  ]);
  const [fileOptions, fileBody] = block("形式ごとの詳細と完了通知", "選んだ形式に応じて、文字コードや画像サイズなどを変更できます。", false);
  fileBody.id = "fileFormatOptions";
  renderSelectedFileOptions(fileBody, root);
  detailsBody.append(advanced, fileOptions);
  const [parameters, parametersBody] = block("共有パラメーター", "複数の項目から値を参照できます。", false);
  parametersBody.append(control("parameters", props.parameters, state.spec.parameters,
    (value) => (state.spec.parameters = value), root));
  const [fields, fieldsBody] = block("生成する項目", "上から順に値を作ります。", false);
  fieldsBody.style.display = "block";
  renderFields(fieldsBody);
  const [operations, operationBody] = block("更新・削除イベントの割合", "出力データに付ける操作ラベルを設定します。", false);
  renderFileOperationRates(operationBody, root, () => renderFields(fieldsBody));
  const [anomalies, anomaliesBody] = block("異常値の設定", "期間・割合・条件を指定して、異常値を含むデータを生成します。", false);
  anomaliesBody.style.display = "block";
  renderAnomalies(anomaliesBody);
  const [faults, faultsBody] = block("出力障害のシミュレーション", "処理の遅延・停止や不正ファイルを再現します。", false);
  renderObject(faultsBody, deref(props.faults, root), state.spec.faults, root);
  detailsBody.append(parameters, fields, operations, anomalies, faults);
  const [destination, destinationBody] = block("Step 3. 出力先", "ローカルランディング、共有フォルダ（NFS・SMB）、その他の登録済み出力先を選びます。");
  destinationBody.append(control("connection_id", { ...props.connection_id, title: "出力先" }, state.spec.connection_id, (value) => {
    state.spec.connection_id = value;
    state.spec.connection_revision = null;
    updateFileSelection();
  }, root));
  const basics = el("div", { class: "file-basics" });
  basics.append(settings, destination);
  target.append(formats, basics, details);
  updateFileSelection();
}

// 共有マウントもあり得るので、通常実行では出力先を確認して版を固定する。
function fileRunSubmission() {
  const spec = runSpec();
  const destination = state.connections.find((item) => item.id === spec.connection_id);
  if (!destination) throw new Error("登録済みの出力先を選んでください。");
  const formats = spec.files.formats.map((key) => FILE_FORMATS.find(([id]) => id === key)?.[1] || key);
  const sidecars = spec.files.formats.includes("image") && spec.files.write_sidecars ? "（画像の付帯JSONあり）" : "";
  if (!window.confirm("「" + destination.name + "」へ " + formats.join("・") + sidecars + " を投入します。続行しますか？")) return null;
  spec.connection_revision = destination.revision;
  return spec;
}

// 編集中の出力先や生成設定は送らず、サーバー固定のお試し生成だけを開始する。
async function startQuickFileRun() {
  await showStartedFileRun(await api("/api/runs/quickstart", {}));
}

// 受付結果を実行履歴へつなぐ。受付時点では生成完了と表示しない。
async function showStartedFileRun(run) {
  state.current = run.id;
  notify("生成を開始しました。実行ID: " + run.id);
  await refreshHistory();
}
// ファイル生成の設定だけを再描画する。
function renderRun() {
  const target = $("#runEditor");
  target.replaceChildren();
  renderFileRun(target, state.schemas.run);
}
// 生成項目を順序どおりカード表示し、並べ替え・複製・追加を編集モデルに反映できるようにする。
function renderFields(parent) {
  parent.replaceChildren();
  const root = state.schemas.run,
    schema = root.$defs.FieldRule;
  state.spec.fields.forEach(/* 項目と配列位置を結び付け、基本入力・詳細入力・順序変更の操作を持つカードを作る。 */ (field, index) => {
    const card = el("div", { class: "field-card", "data-field-index": index }),
      head = el("div", { class: "field-head" });
    for (const key of ["name", "data_type", "generator"])
      head.append(
        control(
          key,
          schema.properties[key],
          field[key],
          /* 個別カードで基本項目を変更したとき、同じフィールドオブジェクトへ書き戻す。 */ (v) => {
            field[key] = v;
          },
          root,
        ),
      );
    const buttons = el("div", { class: "field-actions" });
    for (const [label, help, fn] of [
      [
        "↑",
        "項目を1つ上へ移動します",
        /* 先頭でない場合だけ直前の項目と交換する。評価順に影響するため配列そのものを更新する。 */ () => {
          if (index)
            [state.spec.fields[index - 1], state.spec.fields[index]] = [
              field,
              state.spec.fields[index - 1],
            ];
        },
      ],
      [
        "↓",
        "項目を1つ下へ移動します",
        /* 末尾でない場合だけ直後の項目と交換する。 */ () => {
          if (index < state.spec.fields.length - 1)
            [state.spec.fields[index + 1], state.spec.fields[index]] = [
              field,
              state.spec.fields[index + 1],
            ];
        },
      ],
      [
        "複製",
        "この項目と詳細条件を複製します",
        /* 設定全体を複製して別名を付け、元の項目の直後へ挿入する。 */ () => {
          const copy = clone(field);
          copy.name = field.name + "_copy";
          state.spec.fields.splice(index + 1, 0, copy);
        },
      ],
      [
        "削除",
        "このデータ項目を削除します",
        /* 全項目がなくならないよう最後の1件は残し、対象位置の項目を削除する。 */ () => {
          if (state.spec.fields.length > 1) state.spec.fields.splice(index, 1);
        },
      ],
    ]) {
      const b = el("button", { type: "button", "data-help": help }, label);
      b.addEventListener("click", /* カード操作後に一覧を作り直し、変更後の位置で次の操作を受け付ける。 */ () => {
        fn();
        renderFields(parent);
      });
      buttons.append(b);
    }
    head.append(buttons);
    card.append(head);
    const detail = el("details");
    detail.append(
      el("summary", {}, "詳細条件 · 分布 / 書式 / 参照 / NULL / 条件分岐"),
    );
    const grid = el("div", { class: "grid" });
    renderObject(grid, schema, field, root, ["name", "data_type", "generator", "primary_key"]);
    detail.append(grid);
    card.append(detail);
    parent.append(card);
  });
  const add = el(
    "button",
    { type: "button", id: "addField" },
    "＋ データ項目を追加",
  );
  add.addEventListener("click", /* スキーマ既定値から例示用の項目を末尾に追加し、一覧を再描画する。 */ () => {
    const value = defaultValue(schema, root);
    value.name = "custom_" + (state.spec.fields.length + 1);
    value.value = "sample";
    state.spec.fields.push(value);
    renderFields(parent);
  });
  parent.append(add);
}
// 異常ルールを実行順に表示し、追加・削除を現在の実行設定に結び付ける。
function renderAnomalies(parent) {
  parent.replaceChildren();
  const root = state.schemas.run,
    schema = root.$defs.AnomalyRule;
  state.spec.anomalies.forEach(/* ルールの位置を見出しに示し、スキーマに従う編集欄と削除ボタンを描画する。 */ (rule, index) => {
    const card = el("div", { class: "field-card" });
    const head = el("div", { class: "section-head" });
    head.append(el("h3", {}, "異常ルール " + (index + 1)));
    const remove = el("button", { type: "button" }, "削除");
    remove.addEventListener("click", /* 選択した位置の異常ルールを削除し、見出しの番号を振り直す。 */ () => {
      state.spec.anomalies.splice(index, 1);
      renderAnomalies(parent);
    });
    head.append(remove);
    card.append(head);
    const grid = el("div", { class: "grid" });
    renderObject(grid, schema, rule, root);
    card.append(grid);
    parent.append(card);
  });
  const add = el(
    "button",
    { type: "button", id: "addAnomaly" },
    "＋ 異常ルールを追加",
  );
  add.addEventListener("click", /* 妥当な対象列を優先順で選び、範囲と値の例を持つ異常ルールを追加する。 */ () => {
    const value = defaultValue(schema, root);
    value.field =
      state.spec.fields.find(/* 簡単な例を作れるよう、valueという名前の既存項目を最優先する。 */ (f) => f.name === "value")?.name ||
      state.spec.fields.find(/* valueがなければ主キー以外の項目を探す。 */ (f) => !f.primary_key)?.name ||
      state.spec.fields[0].name;
    value.value = 999;
    value.from_row = 20;
    value.to_row = 39;
    state.spec.anomalies.push(value);
    renderAnomalies(parent);
  });
  parent.append(add);
}
// ブラウザーの入力制約を検査し、不正欄の折りたたみを開いて理由を表示し処理を中断する。
function validateForm(selector, showError = true) {
  const form = $(selector);
  for (const input of form.querySelectorAll("input,textarea,select")) {
    if (!input.checkValidity()) {
      if (showError) {
        for (let details = input.closest("details"); details;
          details = details.parentElement.closest("details")) {
          details.open = true;
        }
        input.reportValidity();
      }
      throw new Error("入力内容を確認してください。");
    }
  }
}
// フォームを検証した後に現在の実行設定を複製して返す。送信側の加工は編集中モデルから分離する。
function runSpec(showError = true) {
  validateForm("#runForm", showError);
  if (state.info.kind === "files" && !state.spec.files.formats.length)
    throw new Error("生成する形式を1つ以上選んでください。");
  const spec = clone(state.spec);
  delete spec.sql;
  delete spec.stream;
  if (spec.files.completion_mode === "lakehouse-products-local" &&
      (spec.files.formats.length !== 1 || spec.files.formats[0] !== "csv")) {
    throw new Error("Lakehouse製品用の完了形式ではCSVだけを選んでください。");
  }
  return spec;
}
const fieldsByKind = {
  sqlserver: ["host", "port", "database", "username", "password", "ssl_mode"],
  oracle: [
    "host",
    "port",
    "service_name",
    "sid",
    "username",
    "password",
    "ssl_mode",
  ],
  postgresql: ["host", "port", "database", "username", "password", "ssl_mode"],
  mysql: ["host", "port", "database", "username", "password", "ssl_mode"],
  mariadb: ["host", "port", "database", "username", "password", "ssl_mode"],
  sqlite: ["sqlite_path"],
  local: ["directory"],
  s3: [
    "endpoint_url",
    "bucket",
    "prefix",
    "region",
    "access_key",
    "secret_key",
    "session_token",
    "ssl_mode",
  ],
  mqtt: [
    "host",
    "port",
    "username",
    "password",
    "ssl_mode",
    "topic",
    "qos",
    "mqtt_version",
    "client_id",
    "keepalive",
    "retain",
    "delivery_attempts",
    "retry_backoff_ms",
  ],
  http: [
    "url",
    "http_method",
    "auth_mode",
    "username",
    "password",
    "bearer_token",
    "api_key",
    "api_key_header",
    "headers",
    "content_type",
    "ssl_mode",
    "delivery_attempts",
    "retry_backoff_ms",
  ],
  capture: [],
};
// 保存済みプロファイルと既定値から編集状態を作る。表示専用のリビジョンや設定済み情報は送信モデルに含めない。
function editConnection(profile) {
  const root = state.schemas.connection;
  state.profile = { ...defaultValue(root, root), ...clone(profile) };
  for (const key of ["revision", "configured_secrets", "configured_headers"])
    delete state.profile[key];
  $("#connectionTitle").textContent = profile.name || "新しい出力先";
  $("#secretState").textContent =
    "保存済み認証情報: " +
    ((profile.configured_secrets || []).join(", ") || "なし") +
    " / 保存済み追加ヘッダー: " +
    ((profile.configured_headers || []).join(", ") || "なし") +
    "。値は読み戻しません。";
  renderConnection();
  renderConnections();
}
const authByKind = {
  sqlserver: [
    "password",
    "windows_password",
    "kerberos",
    "entra_service_principal",
    "entra_managed_identity",
    "access_token",
  ],
  oracle: ["password", "external", "access_token"],
  postgresql: [
    "password",
    "kerberos",
    "client_certificate",
    "external",
    "access_token",
  ],
  mysql: ["password", "client_certificate", "external"],
  mariadb: ["password", "client_certificate", "external"],
};
const methodsByKind = {
  sqlserver: ["host", "dsn", "raw"],
  oracle: ["host", "dsn", "raw"],
  postgresql: ["host", "service", "unix_socket", "raw"],
  mysql: ["host", "unix_socket", "raw"],
  mariadb: ["host", "unix_socket", "raw"],
};
const driversByKind = {
  sqlserver: ["auto", "pymssql", "pyodbc"],
  oracle: ["auto", "oracledb_thin", "oracledb_thick"],
  postgresql: ["auto", "psycopg"],
  mysql: ["auto", "pymysql"],
  mariadb: ["auto", "pymysql"],
};
const authLabels = {
  password: "ユーザー / パスワード",
  windows_password: "Windows ドメイン資格情報",
  kerberos: "Kerberos 統合認証",
  client_certificate: "TLS クライアント証明書",
  external: "外部 / OS 認証",
  entra_service_principal: "Entra サービスプリンシパル",
  entra_managed_identity: "Entra Managed Identity",
  access_token: "取得済みアクセストークン",
  ssh_key: "SSH 秘密鍵",
  anonymous: "匿名FTP",
};
Object.assign(fieldsByKind, {
  sqlserver: [
    "driver",
    "database_auth",
    "connection_method",
    "host",
    "port",
    "database",
    "dsn",
    "connection_string",
    "odbc_driver",
    "domain",
    "username",
    "password",
    "db_access_token",
    "ssl_mode",
    "driver_options",
  ],
  oracle: [
    "driver",
    "database_auth",
    "connection_method",
    "host",
    "port",
    "service_name",
    "sid",
    "dsn",
    "connection_string",
    "username",
    "password",
    "db_access_token",
    "config_dir",
    "wallet_location",
    "wallet_password",
    "tls_ca_file",
    "ssl_mode",
    "driver_options",
  ],
  postgresql: [
    "driver",
    "database_auth",
    "connection_method",
    "host",
    "port",
    "database",
    "dsn",
    "connection_string",
    "unix_socket",
    "username",
    "password",
    "db_access_token",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "ssl_mode",
    "driver_options",
  ],
  mysql: [
    "driver",
    "database_auth",
    "connection_method",
    "host",
    "port",
    "database",
    "connection_string",
    "unix_socket",
    "username",
    "password",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "ssl_mode",
    "driver_options",
  ],
  mariadb: [
    "driver",
    "database_auth",
    "connection_method",
    "host",
    "port",
    "database",
    "connection_string",
    "unix_socket",
    "username",
    "password",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "ssl_mode",
    "driver_options",
  ],
  nfs: ["directory", "nfs_expected_source"],
  smb: [
    "host",
    "port",
    "share",
    "directory",
    "file_auth",
    "domain",
    "username",
    "password",
    "smb_encrypt",
  ],
  ftp: [
    "host",
    "port",
    "directory",
    "file_auth",
    "username",
    "password",
    "ssl_mode",
    "ftp_tls_mode",
    "ftp_passive",
    "ftp_encoding",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
  ],
  sftp: [
    "host",
    "port",
    "directory",
    "file_auth",
    "username",
    "password",
    "ssh_private_key",
    "ssh_key_password",
    "ssh_key_file",
    "ssh_host_key_policy",
    "ssh_host_key_sha256",
    "ssh_known_hosts",
  ],
  kafka: [
    "bootstrap_servers",
    "host",
    "port",
    "topic",
    "kafka_sasl_mechanism",
    "username",
    "password",
    "ssl_mode",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "delivery_attempts",
    "retry_backoff_ms",
  ],
  amqp: [
    "host",
    "port",
    "topic",
    "amqp_virtual_host",
    "amqp_exchange",
    "amqp_declare_queue",
    "username",
    "password",
    "ssl_mode",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "delivery_attempts",
    "retry_backoff_ms",
  ],
  nats: [
    "host",
    "port",
    "topic",
    "username",
    "password",
    "nats_token",
    "nats_credentials_file",
    "nats_jetstream",
    "ssl_mode",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "delivery_attempts",
    "retry_backoff_ms",
  ],
  websocket: [
    "url",
    "auth_mode",
    "username",
    "password",
    "bearer_token",
    "api_key",
    "api_key_header",
    "headers",
    "websocket_ack",
    "websocket_subprotocols",
    "ssl_mode",
    "tls_ca_file",
    "tls_cert_file",
    "tls_key_file",
    "tls_key_password",
    "delivery_attempts",
    "retry_backoff_ms",
  ],
});
// 接続種別ごとに候補と項目を絞り込み、選べない認証・ドライバー候補は一覧の既定へ補正する。
function renderConnection() {
  const root = state.schemas.connection,
    form = $("#connectionForm");
  form.replaceChildren();
  form.className = "grid";
  const props = clone(root.properties);
  props.kind.enum = state.info.connection_kinds;
  props.kind.description =
    state.info.connection_help[state.profile.kind]?.description ||
    props.kind.description;
  for (const [key, choices] of Object.entries({
    database_auth: authByKind[state.profile.kind],
    connection_method: methodsByKind[state.profile.kind],
    driver: driversByKind[state.profile.kind],
  })) {
    if (choices) {
      props[key].enum = choices;
      if (!choices.includes(state.profile[key]))
        state.profile[key] = choices[0];
    }
  }
  const fileAuth = {
    smb: ["password", "kerberos"],
    ftp: ["password", "anonymous"],
    sftp: ["password", "ssh_key"],
  }[state.profile.kind];
  if (fileAuth) {
    props.file_auth.enum = fileAuth;
    if (!fileAuth.includes(state.profile.file_auth))
      state.profile.file_auth = fileAuth[0];
  }
  const explanation = el(
    "p",
    { class: "note connection-explanation" },
    state.info.connection_help[state.profile.kind]?.description || "",
  );
  explanation.style.gridColumn = "1 / -1";
  form.append(explanation);
  const keys = [
    "id",
    "name",
    "kind",
    ...(fieldsByKind[state.profile.kind] || []),
    "timeout_seconds",
    "clear_secrets",
  ];
  for (const key of keys) {
    const c = control(
      key,
      props[key],
      state.profile[key],
      /* プロファイルに入力値を反映する。種別変更時だけ関連する方式の値を再初期化し描画し直す。 */ (v) => {
        state.profile[key] = v;
        if (key === "kind") {
          for (const name of ["driver", "database_auth", "connection_method"])
            state.profile[name] = defaultValue(props[name], root);
          renderConnection();
        }
      },
      root,
    );
    form.append(c);
  }
}

// 接続一覧を現在の編集対象の強調とリビジョン表示付きで再構築する。
function renderConnections() {
  $("#connectionList").replaceChildren();
  for (const p of state.connections) {
    const b = el("button", {
      class: "profile" + (state.profile?.id === p.id ? " active" : ""),
    });
    b.append(
      el("b", {}, p.name),
      el(
        "span",
        { class: "muted" },
        p.kind + " · " + p.id + " · rev " + p.revision,
      ),
    );
    b.addEventListener("click", /* 押された一覧項目を編集対象とし、サーバー保存はまだ行わない。 */ () => editConnection(p));
    $("#connectionList").append(b);
  }
}
// APIから接続一覧を再取得し、接続カード、再送先、実行フォームの候補を更新する。
async function refreshConnections() {
  state.connections = (await api("/api/connections")).connections;
  renderConnections();
  for (const select of [$("#retryConnection")]) {
    select.replaceChildren();
    state.connections.forEach(/* 再送用セレクターへ、表示名と送信用IDを持つ選択肢を追加する。 */ (p) =>
      select.append(el("option", { value: p.id }, p.name)),
    );
  }
  if (state.spec) renderRun();
}
// 今選ばれたプリセットの説明を反映し、なければ編集方法の案内を表示する。
function updatePresetDetails() {
  const item = state.presets.find(/* 選択欄の値と一致するプリセットを、表示名ではなくIDで探す。 */ (p) => p.id === $("#presetSelect").value);
  const parts = (item?.spec.description || "").trim().split(/\r?\n\s*\r?\n/);
  const scenario = parts.shift()?.replace(/^【想定(?:する状況)?】\s*/, "").trim();
  $("#presetScenario").textContent = scenario || "このひな型には状況の説明がありません。";
  $("#presetDetails").textContent = parts.join("\n\n");
  $("#presetDetailsPanel").hidden = parts.length === 0;
}
// 最新のプリセット一覧を取得し、存在する限り以前の選択を保つ。
async function refreshPresets() {
  state.presets = (await api("/api/presets")).presets;
  const select = $("#presetSelect"),
    previous = select.value;
  select.replaceChildren();
  state.presets.forEach(/* プリセットを表示名とIDで識別できる選択肢として追加する。 */ (p) =>
    select.append(el("option", { value: p.id }, p.name + " · " + p.id)),
  );
  if (state.presets.some(/* 更新前のIDが新一覧に残っているか確認する。 */ (p) => p.id === previous)) select.value = previous;
  updatePresetDetails();
}
// 検証済みフォームをJSONへ変換する。履歴の接続リビジョンと再送指定はプリセットへ持ち込まない。
function showJson() {
  validateForm("#runForm");
  const view = fileSpecView(state.spec);
  $("#presetJson").value = JSON.stringify({
    ...view,
    connection_revision: null,
    task: "generate",
  }, null, 2);
}
// 入力欄のJSONを解析し、プレビューAPIが検証した設定を返す。構文・検証失敗は呼出側へ伝える。
async function parsePreset(parsed = JSON.parse($("#presetJson").value)) {
  delete parsed.sql;
  delete parsed.stream;
  const verified = (await api("/api/preview", parsed)).validated_spec;
  delete verified.sql;
  delete verified.stream;
  return verified;
}
// 値を整形JSONとしてブラウザーからダウンロードし、不要になったオブジェクトURLを解放する。
function download(value, name) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2) + "\n"], {
      type: "application/json",
    }),
  );
  const a = el("a", { href: url, download: name });
  a.click();
  setTimeout(/* ダウンロード開始後、遅延して一時URLを解放する。 */ () => URL.revokeObjectURL(url), 1000);
}
// プレビュー応答をJSONと行表に表示し、欠落値と明示的な値を区別する。書込なしの表示は応答を基に決定する。
function previewResult(data) {
  $("#previewPanel").hidden = false;
  $("#previewPanel .badge").hidden = data.writes !== false;
  $("#previewNote").textContent = data.note || "出力結果";
  const output = data.file_output;
  const formats = $("#previewFormats");
  formats.replaceChildren();
  if (output?.formats) {
    for (const key of output.formats) {
      const label = FILE_FORMATS.find(([id]) => id === key)?.[1] || key;
      const item = el("section", { class: "format-preview" });
      item.append(el("h5", {}, label));
      if (key === "json") {
        item.append(el("pre", {}, JSON.stringify(data.json_payloads?.[0], null, 2)));
      } else if (key === "csv") {
        item.append(el("pre", {}, output.csv_sample || "見本なし"));
      } else if (key === "image") {
        const image = output.image;
        item.append(el("p", {}, image.format + " · " + image.width + " × " + image.height + " px · " + image.count.toLocaleString() + "枚（予定）"));
        if (image.sidecars) item.append(el("p", { class: "muted" }, "画像ごとに付帯JSONも保存します。"));
      }
      formats.append(item);
    }
    if (output.completion_marker) formats.append(el("p", { class: "muted" }, "取り込み用の完了印はデータ形式とは別に作成します。"));
  }
  const detail = data.validated_spec ? {...data, validated_spec: fileSpecView(data.validated_spec)} : data;
  $("#previewJson").textContent = JSON.stringify(detail, null, 2);
  const area = $("#previewTable");
  area.replaceChildren();
  if (data.records?.length) {
    const table = el("table"),
      tr = el("tr");
    const names = [...new Set(data.records.flatMap(/* 全サンプル行のキーを集計し、一部の行だけが持つ項目も列に含める。 */ (row) => Object.keys(row)))];
    tr.append(el("th", {}, "シーケンス"), el("th", {}, "適用した異常"));
    names.forEach(/* 各データ項目の名前をヘッダーセルとして追加する。 */ (k) => tr.append(el("th", {}, k)));
    table.append(tr);
    data.records.forEach(/* サンプル位置と異常名を値の行に対応付け、欠落を別表記で表示する。 */ (row, index) => {
      const r = el("tr");
      r.append(
        el("td", {}, String(data.sample_sequences?.[index] ?? index)),
        el("td", {}, (data.anomaly_hits?.[index] || []).join(" / ") || "正常"),
      );
      for (const name of names) {
        const v = row[name];
        r.append(
          el(
            "td",
            {},
            v === undefined
              ? "〈欠落〉"
              : typeof v === "object"
                ? JSON.stringify(v)
                : String(v),
          ),
        );
      }
      table.append(r);
    });
    area.append(table);
  }
  $("#previewPanel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
// 実行履歴から状態・進捗・出力を示すカードを作り、状態に合う操作だけを提示する。
function jobCard(run) {
  const c = el("div", { class: "card" });
  const h = el("div", { class: "section-head" });
  h.append(
    el("h3", {}, run.spec.name || run.id),
    el("span", { class: "badge" }, run.status),
  );
  c.append(h);
  c.append(
    el(
      "p",
      { class: "muted" },
      run.id + " · " + run.created_at + " · " + run.spec.connection_id,
    ),
  );
  const stats = el("div", { class: "stats" });
  for (const [key, label] of [
    ["processed", "処理済み"],
    ["events_per_second", "件 / 秒"],
    ["files", "ファイル"],
  ])
    if (run.stats[key] !== undefined) {
      const item = el("span", { class: "muted" }, label);
      item.append(el("b", {}, Number(run.stats[key]).toLocaleString()));
      stats.append(item);
    }
  c.append(stats);
  const progress = el("div", { class: "job-progress" });
  progress.append(
    el("div", {
      style:
        "width:" +
        Math.min(
          100,
          (100 * (run.stats.processed || 0)) /
            (run.spec.rows || run.spec.cycles || 1),
        ) +
        "%",
    }),
  );
  c.append(progress);
  if (run.error) c.append(el("p", { class: "danger" }, run.error));
  if (["running", "stopping"].includes(run.status)) {
    const b = el("button", { class: "danger" }, "停止");
    b.addEventListener("click", /* 停止APIを呼び出し、受理後に履歴を更新する。失敗しても画面操作を続けられるよう通知する。 */ async () => {
      try {
        await api("/api/runs/" + run.id + "/stop", {});
        await refreshHistory();
      } catch (e) {
        notify(e.message, true);
      }
    });
    c.append(b);
  }
  if (run.spec.version === 2) {
    const b = el("button", { class: "ghost" }, "この設定を編集");
    b.addEventListener("click", /* 保存済みv2設定を新しい生成編集の起点にする。旧リビジョンや再送指定のまま実行しない。 */ () => {
      state.spec = clone(run.spec);
      state.spec.connection_revision = null;
      state.spec.task = "generate";
      renderRun();
      tab("generate");
    });
    c.append(b);
  }
  if (
    state.info.kind === "files" &&
    run.outputs?.some(
      /* ファイル生成の出力に画像候補があるかを識別し、画像リンクの出し分けに使う。 */ (v) => typeof v === "object" && /\.(png|jpg)$/.test(v.path),
    )
  ) {
    const a = el(
      "a",
      {
        href: "/api/runs/" + run.id + "/image",
        target: "_blank",
        rel: "noopener",
      },
      "生成画像を表示",
    );
    c.append(a);
  }
  const detail = el("details", { "data-run-id": run.id });
  detail.append(el("summary", {}, "実行設定と詳細"));
  detail.append(el("pre", {}, JSON.stringify({...run, spec: fileSpecView(run.spec)}, null, 2)));
  c.append(detail);
  return c;
}
const jobPanePositions = new WeakMap();
// 非表示中はブラウザーが返す座標0で上書きせず、開いている同じ実行の記録だけを使う。
function rememberJobPanes(container) {
  const previous = jobPanePositions.get(container);
  const positions = new Map();
  for (const detail of container.querySelectorAll("details[data-run-id][open]")) {
    const pane = detail.querySelector("pre");
    const id = detail.dataset.runId;
    positions.set(id, pane.getClientRects().length
      ? [pane.scrollTop, pane.scrollLeft]
      : previous?.get(id) ?? [0, 0]);
  }
  jobPanePositions.set(container, positions);
  return positions;
}

// DOMへ接続され、表示されてからだけ復元する。短いJSONではブラウザーが範囲内へ収める。
function restoreJobPanes(container) {
  const positions = jobPanePositions.get(container);
  for (const detail of container.querySelectorAll("details[data-run-id][open]")) {
    const position = positions?.get(detail.dataset.runId);
    const pane = detail.querySelector("pre");
    if (!position || !pane.getClientRects().length) continue;
    [pane.scrollTop, pane.scrollLeft] = position;
  }
}

const jobWheelAnchorMs = 5000; // 2.5秒の履歴更新間隔をまたいで閲覧対象を保持する。
let recentJobWheel;

// 実際にホイールを使った表示枠を記憶する。再描画でポインター直下が変わっても対象を変えない。
document.addEventListener("wheel", (event) => {
  const pane = event.target.closest?.("details[data-run-id][open] pre");
  recentJobWheel = pane
    ? { pane, x: event.clientX, y: event.clientY, at: performance.now() }
    : undefined;
}, { passive: true });
document.addEventListener("pointermove", (event) => {
  if (recentJobWheel &&
      (event.clientX !== recentJobWheel.x || event.clientY !== recentJobWheel.y))
    recentJobWheel = undefined;
});
document.addEventListener("pointerdown", () => { recentJobWheel = undefined; });
document.addEventListener("pointerout", (event) => {
  if (!event.relatedTarget) recentJobWheel = undefined;
});

// 選択、フォーカス、直近のホイールを優先する。手掛かりが競合する場合は強制移動しない。
function readingJobAnchor(...containers) {
  let selected, focused, wheeled;
  const scrolled = [];
  const visible = [];
  const currentFocus = document.activeElement;
  if (recentJobWheel && performance.now() - recentJobWheel.at >= jobWheelAnchorMs)
    recentJobWheel = undefined;
  for (const container of containers) {
    for (const detail of container.querySelectorAll("details[data-run-id][open]")) {
      const pane = detail.querySelector("pre");
      const summary = detail.querySelector("summary");
      const anchor = (element, part) => {
        const bounds = element.getBoundingClientRect();
        if (bounds.bottom > 0 && bounds.top < window.innerHeight)
          return { container, id: detail.dataset.runId, part, top: bounds.top };
      };
      const jsonAnchor = anchor(pane, "pre");
      if (currentFocus === summary) focused ??= anchor(summary, "summary");
      if (!jsonAnchor) continue;
      if (jobPaneSelected(pane)) selected ??= jsonAnchor;
      if (currentFocus === pane) focused ??= jsonAnchor;
      if (recentJobWheel?.pane === pane) wheeled = jsonAnchor;
      if (pane.scrollTop > 0 || pane.scrollLeft > 0) scrolled.push(jsonAnchor);
      visible.push(jsonAnchor);
    }
  }
  return selected ?? focused ?? wheeled
    ?? (scrolled.length === 1 ? scrolled[0] : undefined)
    ?? (visible.length === 1 && window.scrollY > 0 ? visible[0] : undefined);
}

// 並び替えや前方への追加後も、読んでいた部分の画面内の位置を保つ。
function restoreJobAnchor(anchor) {
  if (!anchor) return;
  for (const detail of anchor.container.querySelectorAll("details[data-run-id][open]")) {
    if (detail.dataset.runId !== anchor.id) continue;
    const top = detail.querySelector(anchor.part).getBoundingClientRect().top;
    const shift = top - anchor.top;
    if (Math.abs(shift) > 0.5)
      window.scrollBy({ top: shift, behavior: "instant" });
    return;
  }
}

const pendingJobText = new Map();
const lastJobJson = new WeakMap();

// 選択範囲がJSONに触れている間は、既存のテキストノードを入れ替えない。
function jobPaneSelected(pane) {
  const selection = document.getSelection?.();
  if (!selection || selection.isCollapsed) return false;
  for (let index = 0; index < selection.rangeCount; index++) {
    if (selection.getRangeAt(index).intersectsNode(pane)) return true;
  }
  return false;
}

// 同じ本文には触れず、選択解除時に最新の内容だけを同じ表示枠へ反映する。
function writeJobText(pane, json) {
  if (pane.textContent === json) {
    pendingJobText.delete(pane);
    return;
  }
  if (jobPaneSelected(pane)) {
    pendingJobText.set(pane, json);
    return;
  }
  pendingJobText.delete(pane);
  const { scrollTop, scrollLeft } = pane;
  pane.textContent = json;
  pane.scrollTop = scrollTop;
  pane.scrollLeft = scrollLeft;
}

document.addEventListener("selectionchange", () => {
  for (const [pane, json] of pendingJobText) {
    if (!pane.isConnected) pendingJobText.delete(pane);
    else writeJobText(pane, json);
  }
});

// 読んでいるカード自体を移動せず、その前後を並べ替える。
function orderJobCards(container, cards, reading) {
  const keep = new Set(cards);
  for (const card of Array.from(container.children)) {
    if (keep.has(card)) continue;
    const pane = card.querySelector("pre");
    if (pane) pendingJobText.delete(pane);
    card.remove();
  }
  const anchor = cards.indexOf(reading);
  let next = null;
  for (let index = cards.length - 1; index >= 0; index--) {
    const card = cards[index];
    if (index !== anchor &&
        (card.parentElement !== container || card.nextElementSibling !== next)) {
      container.insertBefore(card, next);
    }
    next = card;
  }
}

// 履歴の行を更新しても、同じ実行の見出し・本文・選択は同じDOMに保つ。
function replaceJobCards(container, runs, readingId) {
  const positions = rememberJobPanes(container);
  const existing = new Map();
  const focused = document.activeElement;
  let reading, nextFocus;
  for (const card of container.children) {
    const detail = card.querySelector("details");
    if (!detail) continue;
    existing.set(detail.dataset.runId, card);
    const pane = detail.querySelector("pre");
    if (jobPaneSelected(pane)) reading = card;
    if (focused === pane || focused === detail.querySelector("summary")) {
      nextFocus = focused;
      reading ??= card;
    }
  }
  reading ??= existing.get(readingId);
  const remaining = new Map();
  const cards = runs.map((run) => {
    const card = existing.get(run.id);
    const json = JSON.stringify(run, null, 2);
    if (!card) return jobCard(run);
    const position = positions.get(run.id);
    if (position) remaining.set(run.id, position);
    if (lastJobJson.get(card) === json) return card;
    lastJobJson.set(card, json);
    const detail = card.querySelector("details");
    const replacement = jobCard(run);
    // 設定編集や停止ボタンは新しい応答で作り直し、詳細だけは接続したまま残す。
    while (card.firstElementChild !== detail) card.firstElementChild.remove();
    for (const child of Array.from(replacement.children)) {
      if (child.tagName !== "DETAILS") card.insertBefore(child, detail);
    }
    writeJobText(detail.querySelector("pre"), json);
    return card;
  });
  orderJobCards(container, cards, reading);
  jobPanePositions.set(container, remaining);
  restoreJobPanes(container);
  if (nextFocus?.isConnected && document.activeElement !== nextFocus)
    nextFocus.focus({ preventScroll: true });
}
let requestedHistory = 0;
let renderedHistory = 0;
// 実行一覧と現在の実行を取得し、終了遷移を通知する。ストリームでは再送待ち集計も更新する。
async function refreshHistory() {
  const request = ++requestedHistory;
  const runs = (await api("/api/runs")).runs;
  // 手動更新とポーリングの応答が逆転しても、表示済みの新しい状態を戻さない。
  if (request < renderedHistory) return;
  renderedHistory = request;
  // 応答を待っている間の操作も反映できるよう、開閉状態は描画直前に読む。
  const history = $("#historyList");
  const currentJob = $("#currentJob");
  const anchor = readingJobAnchor(history, currentJob);
  replaceJobCards(history, runs, anchor?.container === history ? anchor.id : undefined);
  const current = runs.find(/* 実行中または停止処理中の一覧項目を現在の実行として選ぶ。 */ (r) => ["running", "stopping"].includes(r.status));
  currentJob.hidden = !current;
  replaceJobCards(currentJob, current ? [current] : [],
    anchor?.container === currentJob ? anchor.id : undefined);
  restoreJobAnchor(anchor);
  if (current) {
    state.current = current.id;
  } else if (state.current) {
    state.current = null;
    notify("実行が終了しました。結果は実行履歴で確認できます。");
  }
  if (state.info.kind === "stream") {
    const out = await api("/api/outbox");
    if (request < renderedHistory) return;
    $("#outboxSummary").textContent = JSON.stringify(
      { counts: out.counts, legacy_pending: out.legacy_pending },
      null,
      2,
    );
  }
}
const tooltip = $("#tooltip");
let tipTarget = null;
// 浮動ヘルプを非表示にし、表示対象を記憶する参照も消す。
function hideTip() {
  tooltip.hidden = true;
  tipTarget = null;
}
// 対象のヘルプ文を浮動表示と固定欄へ反映し、画面端に収まる座標を計算する。
function showTip(target) {
  if (!target || !target.dataset.help) return;
  tipTarget = target;
  tooltip.textContent = target.dataset.help;
  $("#contextHelp").textContent = target.dataset.help;
  tooltip.hidden = false;
  const r = target.getBoundingClientRect(),
    w = tooltip.offsetWidth,
    h = tooltip.offsetHeight;
  tooltip.style.left =
    Math.max(12, Math.min(innerWidth - w - 12, r.left)) + "px";
  tooltip.style.top =
    Math.max(
      12,
      r.bottom + h + 12 < innerHeight ? r.bottom + 8 : r.top - h - 8,
    ) + "px";
}
document.addEventListener("mouseover", /* マウス直下のヘルプ対象が変わったときに表示を切り替え、対象外では閉じる。 */ (e) => {
  const target = e.target.closest("[data-help]");
  if (target !== tipTarget) showTip(target);
  if (!target) hideTip();
});
document.addEventListener("focusin", /* キーボードで移動したフォーカスの最寄りのヘルプ対象を表示する。 */ (e) =>
  showTip(e.target.closest("[data-help]")),
);
document.addEventListener("focusout", hideTip);
document.addEventListener("keydown", /* Escape入力で開いているヘルプを閉じる。 */ (e) => {
  if (e.key === "Escape") hideTip();
});
window.addEventListener(
  "scroll",
  /* スクロール時にフォーカス中の対象が画面内ならヘルプ位置を更新し、外なら閉じる。 */ () => {
    const focused = document.activeElement?.closest("[data-help]");
    if (focused) {
      const bounds = focused.getBoundingClientRect();
      if (bounds.bottom > 0 && bounds.top < innerHeight) showTip(focused);
      else hideTip();
    } else hideTip();
  },
  true,
);
// API情報とスキーマを読み、初期画面・操作イベント・履歴ポーリングを順に組み立てる。
async function init() {
  [state.info, state.schemas] = await Promise.all([
    api("/api/info"),
    api("/api/schemas"),
  ]);
  state.spec = newFileSpec();
  $("#title").textContent = state.info.title;
  document.title = state.info.title;
  $("#health").textContent = "ONLINE · " + state.info.kind.toUpperCase();
  await Promise.all([refreshConnections(), refreshPresets()]);
  editConnection(state.connections[0]);
  window.FactoryComparison.mount({
    app: "file-generator",
    listPresets: () => state.presets,
    currentSpec: () => runSpec(false),
    preview: (candidate) => api("/api/preview", candidate),
    metrics: (spec) => ({
      "生成する件数": spec.rows,
      "選んだファイル形式の数": spec.files.formats.length,
      "JSONを生成": spec.files.formats.includes('json') ? 'はい' : 'いいえ',
      "CSVを生成": spec.files.formats.includes('csv') ? 'はい' : 'いいえ',
      "疑似画像を生成": spec.files.formats.includes('image') ? 'はい' : 'いいえ',
      "ファイルあたりの件数": spec.files.records_per_file,
      "CSVヘッダー": spec.files.formats.includes('csv') ? (spec.files.csv_header ? 'あり' : 'なし') : null,
      "完了マニフェスト": spec.files.write_manifest ? 'あり' : 'なし',
    }),
  });
  $("#outboxPanel").hidden = true;
  $("#destinationAdvice").textContent = "構成例: ローカルの /landing に生成し、独立した取り込み処理でLakehouseへ転送します。クラウドへの直接投入を試す場合はS3互換プロファイルを追加します。ファイル名とSHA256を記録した完了印を取り込みの目印にできます。";
  document.querySelectorAll(".nav").forEach(/* 全ナビ要素を、それぞれのタブ選択と履歴更新へ接続する。 */ (n) =>
    n.addEventListener("click", /* ナビで選んだタブを開く。履歴を開く場合だけ最新状態を非同期取得する。 */ () => {
      tab(n.dataset.tab);
      if (n.dataset.tab === "history")
        refreshHistory().catch(/* 履歴取得エラーを画面に通知し、未処理のPromise拒否にしない。 */ (e) => notify(e.message, true));
    }),
  );
  $("#presetSelect").addEventListener("change", updatePresetDetails);
  action("#loadPreset", /* 選択済みプリセットを複製して編集に読み込み、保存用IDを同期する。サーバーには保存しない。 */ async () => {
    const p = state.presets.find(/* 選択されたプリセットのIDを使ってインメモリー一覧から元データを探す。 */ (p) => p.id === $("#presetSelect").value);
    if (p) {
      state.spec = clone(p.spec);
      state.spec.connection_revision = null;
      renderRun();
      $("#presetId").value = p.id;
      notify("プリセットを読み込みました。");
    }
  });
  action("#resetRun", /* 編集中の生成設定をサービス既定値の複製へ戻して再描画する。 */ () => {
    state.spec = newFileSpec();
    renderRun();
    notify("実行設定を初期値に戻しました。");
  });
  action("#toPreset", /* 現在のフォームからプリセット用JSONを用意し、プリセット画面を開く。 */ () => {
    showJson();
    $("#presetId").value = "";
    $("#presetDisplayName").value = "";
    $("#presetPurpose").value = "";
    tab("presets");
    $("#presetId").focus();
  });
  action("#refreshJson", /* 表示中のJSONを現在のフォームの検証済み設定で更新する。 */ () => {
    showJson();
    notify("画面の設定をJSONに反映しました。");
  });
  action("#applyJson", /* 編集JSONをサーバーで検証した後にフォームへ反映し、生成画面へ戻す。 */ async () => {
    state.spec = await parsePreset();
    renderRun();
    notify("JSONを検証し、画面へ反映しました。");
    tab("generate");
  });
  action("#savePreset", /* IDを検査し、サーバー検証済みの設定を保存する。成功後にフォームと一覧を同期する。 */ async () => {
    const id = $("#presetId").value;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(id))
      throw new Error(
        "プリセットIDを英数字・下線・ハイフンで指定してください。",
      );
    const draft = JSON.parse($("#presetJson").value);
    const displayName = $("#presetDisplayName").value.trim();
    const purpose = $("#presetPurpose").value.trim();
    if (displayName) draft.name = displayName;
    if (purpose) draft.description = purpose;
    const spec = await parsePreset(draft);
    await api("/api/presets/" + id, spec);
    $("#presetJson").value = JSON.stringify(spec, null, 2);
    state.spec = spec;
    renderRun();
    await refreshPresets();
    $("#presetSelect").value = id;
    updatePresetDetails();
    notify("プリセット " + id + " を保存しました。");
  });
  action("#exportPreset", /* JSONを検証してからダウンロードし、空の名前には既定値を使う。 */ async () =>
    download(await parsePreset(), ($("#presetId").value || "preset") + ".json"),
  );
  $("#importPreset").addEventListener("change", /* 選択ファイルをサイズ制限内で読み、検証済み設定を表示する。ファイル名は保存候補IDへ変換し、自動保存はしない。 */ async (e) => {
    try {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 2 * 1024 * 1024)
        throw new Error("JSONは2MiB以下にしてください。");
      $("#presetJson").value = await file.text();
      state.spec = await parsePreset();
      $("#presetId").value =
        file.name
          .replace(/\.json$/i, "")
          .replace(/[^A-Za-z0-9_-]/g, "-")
          .slice(0, 64) || "imported";
      renderRun();
      notify(
        "JSONを読み込み、画面へ反映しました。保存するとプリセットとして残せます。",
      );
    } catch (err) {
      notify(err.message, true);
    }
  });
  action("#preview", /* 書き込みなしの見本を取得し、待機中の変更で古くなった応答は表示しない。 */ async () => {
    const revision = previewRevision;
    const result = await api("/api/preview", runSpec());
    if (revision === previewRevision) previewResult(result);
  });
  action("#start", /* 出力先・形式を確認して受付済み実行を表示する。 */ async () => {
    const spec = state.info.kind === "files" ? fileRunSubmission() : runSpec();
    if (spec) await showStartedFileRun(await api("/api/runs", spec));
  });
  action("#quickstart", startQuickFileRun);
  $("#quickstart").disabled = state.info.kind !== "files";
  $("#runForm").addEventListener("submit", (event) => event.preventDefault());
  for (const event of ["input", "change", "click"]) {
    $("#runForm").addEventListener(event, invalidateFilePreview);
  }
  action("#newConnection", /* 現在時刻から仮IDを作り、許可種別の先頭で新規接続の編集画面を開く。保存前に通信はしない。 */ () =>
    editConnection({
      id: "connection-" + Date.now().toString(36),
      name: "新しい出力先",
      kind: state.info.connection_kinds[0],
    }),
  );
  action("#saveConnection", /* 接続フォームを検証して保存し、返されたリビジョンと接続一覧を表示に反映する。 */ async () => {
    validateForm("#connectionForm");
    const saved = await api("/api/connections", state.profile);
    await refreshConnections();
    editConnection(saved);
    notify("出力先プロファイルを保存しました。rev " + saved.revision);
  });
  action("#testConnection", /* 現在の編集内容ではなく指定IDの保存済み設定で接続試験し、その結果を通知する。 */ async () => {
    const result = await api(
      "/api/connections/" + encodeURIComponent(state.profile.id) + "/test",
      {},
    );
    notify(
      "保存済みの接続設定でテストしました。\n" +
        JSON.stringify(result, null, 2),
    );
  });
  action("#exportConnection", /* サーバーのエクスポート応答を受け取り、指定接続IDのJSONファイルとしてダウンロードする。 */ async () =>
    download(
      await api(
        "/api/connections/" + encodeURIComponent(state.profile.id) + "/export",
      ),
      state.profile.id + "-connection.json",
    ),
  );
  action("#refreshHistory", refreshHistory);
  action("#retryOutbox", /* 件数範囲を検査し、選択した接続先の未配送分を再送する実行を開始して履歴を更新する。 */ async () => {
    const limit = Number($("#retryLimit").value);
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000000)
      throw new Error("再送件数は1〜1000000にしてください。");
    const run = await api("/api/outbox/retry", {
      connection_id: $("#retryConnection").value,
      limit,
      batch_size: 100,
    });
    state.current = run.id;
    await refreshHistory();
    notify("未配送分の再送を開始しました。");
  });
  action("#uploadConnectionFile", /* 選んだ接続用ファイルをサイズ制限内でBase64化して送信し、保存された名前と権限を表示する。 */ async () => {
    const file = $("#connectionFile").files[0];
    if (!file) throw new Error("接続用ファイルを選んでください。");
    if (file.size > 1024 * 1024)
      throw new Error("1ファイル1MiB以下にしてください。");
    const bytes = new Uint8Array(await file.arrayBuffer());
    let text = "";
    for (const byte of bytes) text += String.fromCharCode(byte);
    const name = $("#connectionFileName").value || file.name;
    const result = await api("/api/connection-files", {
      name,
      content_base64: btoa(text),
    });
    $("#connectionFileResult").textContent =
      result.name +
      " (" +
      result.bytes +
      " bytes / mode " +
      result.permissions +
      ")";
    notify(
      "接続用ファイルを保存しました。プロファイルの対応する欄に " +
        name +
        " を指定してください。",
    );
  });
  await refreshHistory();
  showJson();
  setInterval(/* 実行中または履歴表示中だけ、定期的に最新の履歴を取得する。 */ () => {
    if (state.current || $("#history").classList.contains("active"))
      refreshHistory().catch(/* 定期更新の一時的な失敗を握りつぶし、次回のポーリングで回復できるようにする。 */ () => {});
  }, 2500);
}
init().catch(/* 初期化に失敗したときは詳細を通知し、稼働中の表示を読み込み失敗へ切り替える。 */ (err) => {
  notify(err.message, true);
  $("#health").textContent = "読み込み失敗";
});
