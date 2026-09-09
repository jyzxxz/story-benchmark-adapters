"use strict";

(() => {
  const ui = Object.fromEntries([
    "sample-total", "expected-total", "sealed-total", "unsealed-total", "coverage-summary",
    "coverage-banner", "coverage-state", "coverage-detail", "coverage-progress", "load-error",
    "visible-count", "sample-search", "filter-all", "filter-body", "filter-empty", "cards",
    "empty-state", "reset-filters", "observation-rule",
  ].map((id) => [id, document.getElementById(id)]));
  const text = (value) => typeof value === "string" ? value : "";
  const number = (value) => Number.isInteger(value) && value >= 0 ? value : null;
  const count = (value) => number(value) === null ? "—" : String(value);
  const set = (node, value) => { node.textContent = String(value); };
  const element = (tag, className, content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = String(content);
    return node;
  };
  const stopNames = {
    native_error: "生成或播放失败", adapter_error: "接入或记录失败",
    delivery_unknown: "请求结果未确认", budget_exceeded: "因预算停止",
    timeout: "运行超时", interrupted: "运行被中断", not_started: "尚未开始",
  };
  let data = null;
  let samples = [];
  let cards = [];
  let filter = "all";

  function safeEntry(sample) {
    return typeof sample.sample_id === "string"
      && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(sample.sample_id)
      && sample.entry_file === "samples/" + sample.sample_id + "/index.html";
  }

  function bodyCount(sample) {
    return number(sample.counts && sample.counts.story_segments);
  }

  function stopLabel(status) {
    if (status.native_ended === true) return "原生流程已结束";
    if (status.scope_reached === true) return "已到观察边界";
    return typeof stopNames[status.stop_reason] === "string"
      ? stopNames[status.stop_reason] : "提前停止或状态未确认";
  }

  function evidence(status) {
    if (status.evidence_kind === "live") return ["live", "真实生成记录"];
    if (status.evidence_kind === "fixture") return ["fixture", "固定响应测试"];
    return ["unknown", "证据类型未确认"];
  }

  function renderCoverage() {
    const coverage = data.coverage || {};
    const expected = number(coverage.expected_runs);
    const sealed = number(coverage.sealed_runs);
    const unsealed = number(coverage.unsealed_runs);
    set(ui["sample-total"], samples.length);
    set(ui["expected-total"], count(expected));
    set(ui["sealed-total"], count(sealed));
    set(ui["unsealed-total"], count(unsealed));
    set(ui["coverage-summary"], "含全部已导出的记录，零正文样本也保留");
    const complete = coverage.all_planned_sealed === true && expected !== null
      && sealed === expected && unsealed === 0 && samples.length === sealed;
    if (complete) {
      ui["coverage-banner"].dataset.state = "complete";
      set(ui["coverage-state"], "全部计划记录已收录");
      set(ui["coverage-detail"], "计划 " + expected + " 次，已封存并收录 " + sealed + " 份。封存表示记录已保存，不表示每份故事都生成成功。");
    } else if (coverage.all_planned_sealed === false || (unsealed !== null && unsealed > 0)
      || (expected !== null && samples.length < expected)) {
      ui["coverage-banner"].dataset.state = "partial";
      set(ui["coverage-state"], "当前包尚未覆盖全部计划记录");
      set(ui["coverage-detail"], (expected !== null ? "计划 " + expected + " 次，" : "计划总数未提供，")
        + "当前可打开 " + samples.length + " 份。" + (unsealed !== null ? "另有 " + unsealed + " 次尚未封存。" : "未封存数量未知。")
        + "缺少的记录不代表生成成功，也不能从评审统计中静默排除。");
    } else {
      ui["coverage-banner"].dataset.state = "unknown";
      set(ui["coverage-state"], "计划覆盖范围未能确认");
      set(ui["coverage-detail"], "当前可打开 " + samples.length + " 份记录。计划总数或完整封存状态不明确，不能据此声称已经包含全部计划结果。");
    }
    if (expected !== null && expected > 0 && sealed !== null) {
      ui["coverage-progress"].hidden = false;
      ui["coverage-progress"].max = expected;
      ui["coverage-progress"].value = Math.min(sealed, expected);
      ui["coverage-progress"].setAttribute("aria-valuetext", "计划 " + expected + " 次，已封存 " + sealed + " 次");
    }
  }

  function buildCard(sample, index) {
    const status = sample.status || {};
    const counts = sample.counts || {};
    const hasBody = bodyCount(sample) !== null && bodyCount(sample) > 0;
    const empty = bodyCount(sample) === 0;
    const [evidenceKind, evidenceLabel] = evidence(status);
    const valid = safeEntry(sample);
    const card = element("a", "card");
    if (valid) card.href = sample.entry_file;
    else {
      card.setAttribute("aria-disabled", "true");
      card.tabIndex = 0;
    }
    card.dataset.body = hasBody ? "present" : empty ? "empty" : "unknown";
    const cap = element("div", "card-cap");
    const ordinal = element("span", "card-ordinal", String(index + 1).padStart(2, "0"));
    ordinal.setAttribute("aria-hidden", "true");
    const badge = element("span", "evidence-badge", evidenceLabel);
    badge.dataset.kind = evidenceKind;
    cap.append(ordinal, badge);
    const body = element("div", "card-body");
    const heading = element("h3", "card-title", "样本 " + String(index + 1).padStart(2, "0"));
    const id = element("p", "sample-id", text(sample.sample_id) || "编号未记录");
    const state = element("p", "sample-status", (empty ? "无新增正文 · " : "") + stopLabel(status));
    const stats = element("dl", "card-stats");
    [["正文段", counts.story_segments], ["画面", counts.frames], ["已执行选择", counts.executed_choices]].forEach(([label, value]) => {
      const item = element("div");
      item.append(element("dt", "", label), element("dd", "", count(value)));
      stats.append(item);
    });
    const caution = element("p", "card-note", evidenceKind === "fixture"
      ? "仅验证程序流程，不能作为真实故事质量结果。"
      : evidenceKind === "unknown" ? "证据类型待核查，不能当作真实生成结果。"
        : empty ? "固定开头不代表生成成功。此项保留停止记录。"
          : "按已保存路径回放；完整性与质量需分别判断。");
    const action = element("div", "card-action");
    action.append(element("span", "", !valid ? "入口路径无效" : hasBody ? "阅读故事" : empty ? "查看停止记录" : "打开记录"), element("span", "arrow", valid ? "↗" : "!"));
    body.append(heading, id, state, stats, caution, action);
    card.append(cap, body);
    return card;
  }

  function applyFilter() {
    const query = ui["sample-search"].value.trim().toLowerCase();
    let visible = 0;
    cards.forEach((card, index) => {
      const sample = samples[index];
      const amount = bodyCount(sample);
      const matchesKind = filter === "all" || (filter === "hasbody" && amount !== null && amount > 0)
        || (filter === "empty" && amount === 0);
      const matchesSearch = !query || text(sample.sample_id).toLowerCase().includes(query);
      card.hidden = !(matchesKind && matchesSearch);
      if (!card.hidden) visible += 1;
    });
    set(ui["visible-count"], "显示 " + visible + " / " + samples.length + " 份记录");
    ui["empty-state"].hidden = visible > 0 || samples.length === 0;
    [ui["filter-all"], ui["filter-body"], ui["filter-empty"]].forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    });
  }

  function load() {
    data = window.STORY_CATALOG;
    if (!data || data.schema_version !== "catalog.1" || !Array.isArray(data.samples)
      || data.samples.some((sample) => !sample || typeof sample !== "object" || Array.isArray(sample))) {
      throw new Error("无法读取故事目录。请完整解压回放包，保留 index.html、catalog-data.js、catalog.js、catalog.css 与 samples 文件夹，然后重新打开 index.html。");
    }
    samples = data.samples;
    renderCoverage();
    set(ui["observation-rule"], text(data.rule));
    cards = samples.map(buildCard);
    ui.cards.replaceChildren(...cards);
    set(ui["filter-all"], "全部 " + samples.length);
    set(ui["filter-body"], "有正文 " + samples.filter((sample) => bodyCount(sample) !== null && bodyCount(sample) > 0).length);
    set(ui["filter-empty"], "零正文 " + samples.filter((sample) => bodyCount(sample) === 0).length);
    ui["sample-search"].disabled = false;
    [ui["filter-all"], ui["filter-body"], ui["filter-empty"]].forEach((button) => { button.disabled = false; });
    applyFilter();
    if (!samples.length) {
      const empty = element("div", "catalog-no-records");
      empty.append(element("strong", "", "当前包没有可打开的故事记录"),
        element("p", "", "请结合上方计划覆盖范围核对。尚未封存的记录不会被当作已生成的故事。"));
      ui.cards.append(empty);
    }
  }

  ui["sample-search"].addEventListener("input", applyFilter);
  [ui["filter-all"], ui["filter-body"], ui["filter-empty"]].forEach((button) => {
    button.addEventListener("click", () => { filter = button.dataset.filter; applyFilter(); });
  });
  ui["reset-filters"].addEventListener("click", () => {
    filter = "all";
    ui["sample-search"].value = "";
    applyFilter();
    ui["sample-search"].focus();
  });

  try {
    load();
  } catch (error) {
    set(ui["load-error"], error instanceof Error ? error.message : "本地目录无法读取。");
    ui["load-error"].hidden = false;
    set(ui["visible-count"], "目录尚未载入，不会请求远程内容。");
    set(ui["coverage-summary"], "记录范围无法读取");
  }
})();
