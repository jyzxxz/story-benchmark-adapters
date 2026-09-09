"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "sample-id", "segment-count", "frame-count", "missing-count", "load-error",
    "workspace", "kind-label", "scope-label", "scene-image", "visual-empty",
    "image-state-title", "image-state-detail", "image-warning", "speaker-label",
    "page-label", "page-context", "story-text", "choice-panel", "choice-context",
    "choice-list", "end-note", "end-detail", "timeline", "jump-form", "jump-page",
    "jump-button", "previous", "autoplay", "autoplay-label", "next", "delay",
    "playback-status", "outline-toggle", "outline", "outline-list", "outline-count",
    "observation-rule", "evidence-banner", "evidence-title", "evidence-detail", "run-status", "collection-link",
  ].map((id) => [id, byId(id)]));

  const kindNames = {
    opening: "公共固定开头", story: "正文", choice: "实际选择记录",
    frame: "画面记录", notice: "记录说明",
  };
  const asText = (value) => typeof value === "string" ? value : "";
  const asCount = (value) => Number.isInteger(value) && value >= 0 ? String(value) : "—";
  const setText = (node, value) => { node.textContent = asText(value); };
  const safeImage = (value) => typeof value === "string"
    && /^images\/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:png|jpe?g|webp)$/i.test(value);
  const stopNames = {
    native_error: "生成或原生播放失败", adapter_error: "接入或记录过程失败",
    delivery_unknown: "请求结果未确认", budget_exceeded: "因预算限制停止",
    timeout: "运行超时", interrupted: "运行被中断", not_started: "尚未开始生成",
  };

  let data = null;
  let pages = [];
  let position = 0;
  let timer = null;
  let playing = false;
  let imageEpoch = 0;
  let outlineItems = [];

  function stopLabel() {
    const status = data.status || {};
    if (status.native_ended === true) return "原生流程已结束";
    if (status.scope_reached === true) return "已到统一观察边界";
    return typeof stopNames[status.stop_reason] === "string"
      ? stopNames[status.stop_reason] : "记录提前停止或结束状态未确认";
  }

  function renderEvidence() {
    const status = data.status || {};
    const kind = status.evidence_kind === "live" ? "live"
      : status.evidence_kind === "fixture" ? "fixture" : "unknown";
    ui["evidence-banner"].dataset.evidence = kind;
    if (kind === "fixture") {
      setText(ui["evidence-title"], "固定响应测试 · 不能作为真实故事质量结果");
      setText(ui["evidence-detail"], "这份回放来自测试夹具，用于检查程序与记录流程，不是模型真实生成的评测样本。");
    } else if (kind === "live") {
      setText(ui["evidence-title"], "真实生成记录");
      setText(ui["evidence-detail"], "记录标记为真实模型运行。这只说明生成方式，不代表质量达标或故事已经完整结束。");
    } else {
      setText(ui["evidence-title"], "证据类型未确认");
      setText(ui["evidence-detail"], "记录没有明确标记真实生成或固定响应测试；核对原始记录之前，不能把它当作真实生成结果。");
    }
    const visible = asCount(status.visible_chars);
    setText(ui["run-status"], "停止状态：" + stopLabel() + (visible === "0"
      ? " · 未保存新增正文" : visible !== "—" ? " · 新增正文 " + visible + " 字符" : " · 新增正文数量未记录"));
  }

  function updatePlayButton() {
    ui.autoplay.setAttribute("aria-pressed", String(playing));
    setText(ui["autoplay-label"], playing ? "暂停播放" : "自动播放");
  }

  function stop(message) {
    playing = false;
    window.clearTimeout(timer);
    timer = null;
    updatePlayButton();
    if (message) setText(ui["playback-status"], message);
  }

  function isChoicePage(page) {
    return page.kind === "choice" || (Array.isArray(page.choices) && page.choices.length > 0);
  }

  function schedule() {
    window.clearTimeout(timer);
    timer = null;
    if (!playing) return;
    if (isChoicePage(pages[position])) {
      stop("已在选择页暂停。请阅读当次选择记录，再点击下一页继续。");
      return;
    }
    if (position >= pages.length - 1) {
      stop("已到本次记录终点。可以返回前页重新阅读。");
      return;
    }
    const delay = Number(ui.delay.value);
    timer = window.setTimeout(() => showPage(position + 1, true),
      [5000, 8000, 12000, 20000].includes(delay) ? delay : 8000);
  }

  function emptyImage(title, detail) {
    ui["scene-image"].hidden = true;
    ui["visual-empty"].hidden = false;
    setText(ui["image-state-title"], title);
    setText(ui["image-state-detail"], detail);
  }

  function renderImage(page) {
    const epoch = ++imageEpoch;
    const image = ui["scene-image"];
    image.onload = null;
    image.onerror = null;
    image.removeAttribute("src");
    image.hidden = true;
    ui["image-warning"].hidden = true;
    if (safeImage(page.image)) {
      emptyImage("正在载入本页画面", "从此回放包的本地图片目录读取。");
      image.alt = "第 " + (position + 1) + " 页保存的画面";
      image.onload = () => {
        if (epoch !== imageEpoch) return;
        image.hidden = false;
        ui["visual-empty"].hidden = true;
        if (page.image_status === "placeholder") {
          setText(ui["image-warning"], "原记录标记为占位画面");
          ui["image-warning"].hidden = false;
        } else if (page.image_status === "missing") {
          setText(ui["image-warning"], "原记录标记画面不完整");
          ui["image-warning"].hidden = false;
        }
      };
      image.onerror = () => {
        if (epoch !== imageEpoch) return;
        emptyImage("本页图片无法读取", "请保留回放包的 images 文件夹及原有目录结构。不会用其他画面替代。");
      };
      image.src = page.image;
      return;
    }
    if (page.image && !safeImage(page.image)) {
      emptyImage("本页图片路径无效", "播放器只读取 images 文件夹中的本地图片，已阻止其他地址。");
    } else if (page.image_status === "missing") {
      emptyImage("本页画面缺失", "记录中没有可用的对应图片；保留正文，不补图或借用其他页画面。");
    } else if (page.image_status === "placeholder") {
      emptyImage("本页为占位画面", "原记录未提供可用的实际画面。");
    } else if (page.kind === "opening") {
      emptyImage("共同的故事起点", "以下是生成前已经提供的固定开头，不计入新增正文。");
    } else if (page.kind === "notice") {
      emptyImage("本次记录说明", "以下说明记录的范围与停止位置，不是额外生成的剧情或结局。");
    } else {
      emptyImage("本页未记录画面", "按原有记录显示文字，不推测其对应的图片。");
    }
  }

  function renderChoices(page) {
    const choices = Array.isArray(page.choices)
      ? page.choices.filter((choice) => choice && typeof choice === "object") : [];
    ui["choice-list"].replaceChildren();
    ui["choice-panel"].hidden = !isChoicePage(page);
    if (!isChoicePage(page)) return;
    let context = "已保存当次选项；选择的执行状态未记录。";
    if (page.selection_executed === true) context = choices.some((choice) => choice.selected === true)
      ? "高亮项是当次已经执行的选择。" : "当次选择已执行，但本页没有保存对应的选项标记。";
    if (page.selection_executed === false) context = "此处没有已执行的选择，本页只展示已记录的选项。";
    if (!choices.length) context = "此页标记为选择记录，但未保存可显示的选项。";
    setText(ui["choice-context"], context);
    choices.forEach((choice, index) => {
      const item = document.createElement("li");
      const number = document.createElement("span");
      const label = document.createElement("span");
      const badge = document.createElement("span");
      const executed = choice.selected === true && page.selection_executed === true;
      item.className = "choice-item" + (executed ? " selected" : "");
      number.className = "choice-number";
      number.textContent = String(index + 1).padStart(2, "0");
      label.className = "choice-label";
      label.textContent = asText(choice.label);
      badge.className = "choice-badge";
      badge.textContent = executed ? "当次已执行"
        : choice.selected === true ? (page.selection_executed === false ? "已标记 · 未执行" : "已标记 · 执行未确认")
        : page.selection_executed === true ? "未选择" : "仅记录";
      item.append(number, label, badge);
      ui["choice-list"].append(item);
    });
  }

  function endingText() {
    const status = data.status || {};
    if (status.native_ended === true) {
      return "原生流程记录为结束。这里只展示本次保存的路径，不代表其他分支都已被访问。";
    }
    if (status.scope_reached === true) {
      return "已到统一观察边界。这是本次记录的终点，不代表故事完整结束，也不会额外补写结局。";
    }
    return stopLabel() + "。未确认完整故事已经结束，没有保存的后续内容不会在回放中补写。";
  }

  function showPage(index, automatic = false) {
    if (!Number.isInteger(index) || index < 0 || index >= pages.length) return;
    if (!automatic) stop();
    position = index;
    const page = pages[index];
    setText(ui["kind-label"], kindNames[page.kind] || "故事记录");
    setText(ui["speaker-label"], asText(page.speaker)
      || (page.kind === "opening" ? "固定开头" : page.kind === "choice" ? "当次选择"
        : page.kind === "notice" ? "记录说明" : page.kind === "frame" ? "画面记录" : "旁白 / 记录"));
    setText(ui["story-text"], asText(page.text));
    ui["story-text"].hidden = !asText(page.text).length;
    setText(ui["page-label"], String(index + 1) + " / " + String(pages.length));
    ui["page-context"].hidden = page.kind !== "opening";
    setText(ui["page-context"], page.kind === "opening" ? "测试前已提供的公共内容 · 不计入新增正文" : "");
    renderImage(page);
    renderChoices(page);
    ui["end-note"].hidden = index !== pages.length - 1;
    setText(ui["end-detail"], endingText());
    ui.previous.disabled = index === 0;
    ui.next.disabled = index === pages.length - 1;
    ui.autoplay.disabled = index === pages.length - 1;
    ui.timeline.value = String(index + 1);
    ui.timeline.setAttribute("aria-valuetext", "第 " + (index + 1) + " 页，共 " + pages.length + " 页");
    ui["jump-page"].value = String(index + 1);
    outlineItems.forEach((button, itemIndex) => {
      if (itemIndex === index) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (isChoicePage(page)) {
      stop("选择记录为只读。请阅读当次选择，再点击下一页继续。");
    } else if (index === pages.length - 1) {
      stop("已到本次记录终点。可以返回前页重新阅读。");
    } else {
      setText(ui["playback-status"], playing
        ? "正在自动播放 · 遇到选择页会暂停"
        : "方向键或空格翻页 · 遇到选择页，自动播放会暂停");
      schedule();
    }
  }

  function buildOutline() {
    const fragment = document.createDocumentFragment();
    outlineItems = pages.map((page, index) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      const heading = document.createElement("span");
      const excerpt = document.createElement("span");
      button.type = "button";
      button.className = "outline-item";
      heading.className = "outline-item-heading";
      heading.textContent = String(index + 1).padStart(2, "0") + "  ·  " + (kindNames[page.kind] || "故事记录");
      excerpt.className = "outline-excerpt";
      const source = asText(page.text) || (Array.isArray(page.choices)
        ? page.choices.map((choice) => asText(choice && choice.label)).join(" / ") : "");
      const characters = Array.from(source);
      excerpt.textContent = characters.slice(0, 84).join("") + (characters.length > 84 ? "…" : "");
      if (!excerpt.textContent) excerpt.textContent = page.image ? "查看保存的画面" : "查看记录";
      button.append(heading, excerpt);
      button.addEventListener("click", () => showPage(index));
      item.append(button);
      fragment.append(item);
      return button;
    });
    ui["outline-list"].replaceChildren(fragment);
    setText(ui["outline-count"], String(pages.length) + " 页");
  }

  function load() {
    data = window.STORY_REVIEW;
    if (!data || data.schema_version !== "playback.1" || !Array.isArray(data.pages)
      || data.pages.some((page) => !page || typeof page !== "object" || Array.isArray(page))) {
      throw new Error("无法读取回放数据。请确认 index.html、data.js、player.js 与 player.css 位于同一目录，并完整解压回放包后再打开。");
    }
    pages = data.pages;
    // Only the exporter-provided collection location is allowed. Standalone
    // samples never advertise a parent catalog that does not exist.
    ui["collection-link"].hidden = data.collection_href !== "../../index.html";
    setText(ui["sample-id"], asText(data.sample_id) || "匿名故事样本");
    const counts = data.counts || {};
    setText(ui["segment-count"], asCount(counts.story_segments));
    setText(ui["frame-count"], asCount(counts.frames));
    setText(ui["missing-count"], asCount(counts.missing_images));
    setText(ui["observation-rule"], asText(data.rule));
    renderEvidence();
    setText(ui["scope-label"], stopLabel());
    if (!pages.length) {
      emptyImage("这份记录没有可回放页面", "批次完成不代表故事生成成功，请检查本次结果中的停止原因。");
      setText(ui["speaker-label"], "没有可回放正文");
      setText(ui["kind-label"], "空记录");
      setText(ui["page-label"], "0 / 0");
      setText(ui["playback-status"], "没有保存的内容，不会在回放阶段重新生成。");
      return;
    }
    ui.timeline.max = String(pages.length);
    ui.timeline.disabled = false;
    ui["jump-page"].max = String(pages.length);
    ui["jump-page"].disabled = false;
    ui["jump-button"].disabled = false;
    ui.delay.disabled = false;
    ui["outline-toggle"].disabled = false;
    buildOutline();
    showPage(0);
  }

  ui.previous.addEventListener("click", () => showPage(position - 1));
  ui.next.addEventListener("click", () => showPage(position + 1));
  ui.timeline.addEventListener("input", () => showPage(Number(ui.timeline.value) - 1));
  ui["jump-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const pageNumber = Number(ui["jump-page"].value);
    if (Number.isInteger(pageNumber) && pageNumber >= 1 && pageNumber <= pages.length) showPage(pageNumber - 1);
    else {
      setText(ui["playback-status"], "请输入 1 到 " + pages.length + " 之间的页码。");
      ui["jump-page"].focus();
    }
  });
  ui.autoplay.addEventListener("click", () => {
    if (playing) {
      stop("已暂停。方向键或空格可以逐页阅读。");
      return;
    }
    if (!pages.length) return;
    playing = true;
    updatePlayButton();
    setText(ui["playback-status"], "正在自动播放 · 遇到选择页会暂停");
    schedule();
  });
  ui.delay.addEventListener("change", schedule);
  ui["outline-toggle"].addEventListener("click", () => {
    const expanded = ui["outline-toggle"].getAttribute("aria-expanded") !== "true";
    ui["outline-toggle"].setAttribute("aria-expanded", String(expanded));
    ui.outline.hidden = !expanded;
    ui.workspace.classList.toggle("with-outline", expanded);
    setText(ui["outline-toggle"], expanded ? "收起目录" : "页目录");
  });
  document.addEventListener("keydown", (event) => {
    if (!pages.length || event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented) return;
    const target = event.target;
    if (target instanceof Element && (target.closest("input, select, textarea") || target.isContentEditable)) return;
    // Keep Space's native activation on buttons, while arrow keys continue to
    // navigate after a reader clicks a transport or outline button.
    if (event.key === " " && target instanceof Element && target.closest("button, a")) return;
    if (["ArrowRight", "PageDown", " ", "ArrowLeft", "PageUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (event.key === "Home") showPage(0);
      else if (event.key === "End") showPage(pages.length - 1);
      else if (["ArrowLeft", "PageUp"].includes(event.key) || (event.key === " " && event.shiftKey)) showPage(position - 1);
      else showPage(position + 1);
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && playing) stop("离开页面后已暂停自动播放。");
  });
  window.addEventListener("pagehide", () => stop());

  try {
    load();
  } catch (error) {
    stop();
    setText(ui["load-error"], error instanceof Error ? error.message : "回放数据无法读取。");
    ui["load-error"].hidden = false;
    emptyImage("无法载入故事记录", "请保留完整的离线回放包，再打开 index.html。");
    setText(ui["sample-id"], "本地回放文件未能载入");
    setText(ui["playback-status"], "此页面不会请求远程内容或调用模型。");
  }
})();
