"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// 製品の描画・送信関数を、最小DOMと通信スタブに接続する。
function fixture() {
  const make = () => ({
    listeners: {},
    children: [],
    setAttribute() {},
    append(...nodes) { this.children.push(...nodes); },
    addEventListener(type, callback) { this.listeners[type] = callback; },
    querySelectorAll() { return []; },
  });
  const elements = new Map();
  const document = {
    createElement: make, createTextNode: (value) => value, addEventListener() {},
    querySelector(id) {
      if (!elements.has(id)) elements.set(id, make());
      return elements.get(id);
    },
  };
  const requests = [];
  const context = vm.createContext({
    document, structuredClone, window: { addEventListener() {}, confirm: () => false },
    fetch: async (route, options) => {
      requests.push({ route, body: JSON.parse(options.body || "null") });
      return { ok: true, json: async () => ({ id: "test-job" }) };
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const end = source.lastIndexOf("\ninit().catch(");
  assert.ok(end > 0);
  vm.runInContext(source.slice(0, end), context);
  const run = (script) => vm.runInContext(script, context);
  run(`state.info = {kind:"files"};
    state.spec = {files:{formats:["json","parquet"]},rows:99,connection_id:"remote",fields:[]};
    state.connections = [{id:"remote",kind:"s3",name:"外部",revision:2}];
    refreshHistory = async () => {};`);
  return { run, requests, make };
}

test("主要形式だけ切り替えても追加形式を消さない", () => {
  const { run, make } = fixture();
  const groups = [make(), make()];
  run("renderFileFormats")(groups);
  assert.equal(groups[0].children.length, 3);
  assert.equal(groups[1].children.length, 5);
  const input = groups[0].children[0].children[0];
  input.checked = false;
  input.listeners.change();
  assert.equal(run("JSON.stringify(state.spec.files.formats)"), '["parquet"]');
  run("state.spec.files.formats = []");
  assert.throws(() => run("runSpec()"), /形式を1つ以上/);
});

test("通常出力は明示承認が必要、クイックスタートは編集モデルを渡さない", async () => {
  const { run, requests } = fixture();
  assert.equal(run("fileRunSubmission()"), null);
  run('state.connections[0].kind = "local"');
  assert.equal(run("fileRunSubmission()"), null);
  run("window.confirm = () => true");
  assert.equal(run("fileRunSubmission().connection_revision"), 2);
  await run("startQuickFileRun()");
  assert.deepEqual(requests, [{ route: "/api/runs/quickstart", body: {} }]);
  assert.equal(run("state.spec.rows"), 99);
  assert.equal(run("state.spec.connection_id"), "remote");
});

test("更新・削除割合は生成ルールだけに反映し、独自列との衝突を拒否する", () => {
  const { run } = fixture();
  run("state.spec.fields = [{name:'existing'}]; var root = {$defs:{FieldRule:{type:'object'}}};");
  assert.match(run("applyFileOperationRates(70, 40, root)"), /100%以下/);
  assert.equal(run("state.spec.fields.length"), 1);
  assert.equal(run("applyFileOperationRates(30, 20, root)"), "");
  assert.equal(run("JSON.stringify(state.spec.fields[1].weights)"), "[50,30,20]");
  assert.equal(run("fileOperationRule(state.spec.fields[1])"), true);
  assert.equal(run("applyFileOperationRates(0, 0, root)"), "");
  assert.equal(run("state.spec.fields.length"), 1);
  run("state.spec.fields.push({name:'FILE_EVENT_OPERATION',generator:'constant'})");
  assert.match(run("applyFileOperationRates(50, 0, root)"), /独自項目/);
  assert.equal(run("state.spec.fields[1].generator"), "constant");
});
