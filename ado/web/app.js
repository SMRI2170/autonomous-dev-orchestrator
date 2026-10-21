"use strict";
const $ = (id) => document.getElementById(id);
const names = {
  QUEUED: "受付済み",
  BUILDING: "実装中",
  PUBLISHING: "PR作成待ち",
  REVIEW: "CI・レビュー待ち",
  AWAITING_HUMAN: "人間の確認待ち",
  BLOCKED: "停止",
  DONE: "完了",
  PLANNED: "dry-run確認済み",
};
const positions = [
  [30, 55],
  [320, 55],
  [610, 55],
  [610, 255],
  [320, 255],
  [30, 255],
  [30, 455],
  [320, 455],
  [610, 455],
];
const SVG = "http://www.w3.org/2000/svg";
let data,
  selected,
  selectedStage,
  demo = false,
  busy = false,
  failed = false,
  renderedSignature;
const mobile = window.matchMedia("(max-width:680px)");
let view = [0, 0, 870, 625],
  drag = null;
// Rebuilt nodes, so focus can be restored onto the matching element after replaceChildren().
let jobNodes = new Map(),
  stageNodes = new Map();
function visibleStage(id) {
  return id === "merge" ? "done" : id;
}
function text(id, value) {
  $(id).textContent = value ?? "—";
}
function element(tag, cls, value) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (value !== undefined) e.textContent = value;
  return e;
}
function svg(tag, attrs = {}, value) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (value !== undefined) e.textContent = value;
  return e;
}
function stateClass(state) {
  return state === "DONE"
    ? "done"
    : state === "BLOCKED"
      ? "blocked"
      : ["REVIEW", "AWAITING_HUMAN", "PLANNED"].includes(state)
        ? "waiting"
        : "active";
}
function clock(stamp) {
  return stamp
    ? new Date(stamp * 1000).toLocaleTimeString("ja-JP", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
}
function elapsed(stamp) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - stamp));
  return s < 60
    ? `${s}秒前`
    : s < 3600
      ? `${Math.floor(s / 60)}分前`
      : `${Math.floor(s / 3600)}時間前`;
}
function allJobs() {
  const jobs = [...data.jobs];
  for (const p of data.planned || []) {
    if (jobs.some((j) => j.repo === p.repo && j.issue === p.issue)) continue;
    jobs.push({
      id: `plan:${p.repo}:${p.issue}`,
      repo: p.repo,
      issue: p.issue,
      title: p.title,
      state: "PLANNED",
      stage: "spec",
      updated: p.time,
      reason: p.result === "ELIGIBLE" ? "" : p.result,
      waiting: [],
      files: [],
      events: [],
      calls: 0,
      repairs: 0,
      plan: p.result,
    });
  }
  return jobs;
}
function currentJob() {
  return allJobs().find((j) => String(j.id) === String(selected));
}
function translate(reason) {
  const exact = {
    "missing CodeRabbit review at current SHA":
      "現在のコミットへのCodeRabbitレビューを待っています",
    "CodeRabbit has not APPROVED current SHA":
      "CodeRabbitの正式な承認を待っています",
    "unresolved old review thread": "以前のレビューに未解決の指摘があります",
    "PR is draft, conflicted, behind or mergeability is unknown":
      "下書き・競合・baseの更新など、PRの状態確認が必要です",
    "Review/CI/human-merge deadline exceeded":
      "CI・レビュー・人間のマージ待ちが期限を超えました",
    "Maximum repair attempts reached": "修正回数の上限に達しました",
  };
  if (exact[reason]) return exact[reason];
  if (reason.startsWith("running CI "))
    return `CI「${reason.slice(11)}」を実行中です`;
  if (reason.startsWith("missing CI "))
    return `CI「${reason.slice(11)}」の結果を待っています`;
  return reason;
}
function activity(job) {
  if (!job) return "処理を開始すると、現在の工程と内容がここに表示されます。";
  const label = job.title;
  if (job.state === "PLANNED")
    return job.plan === "ELIGIBLE"
      ? `「${label}」は実行条件を満たしています。dry-runのため実装は開始していません。`
      : `「${label}」の事前確認で問題が見つかりました。`;
  if (job.state === "BLOCKED")
    return `「${label}」は${data.stages.find((s) => s.id === job.stage)?.label || "処理"}の段階で停止しました。`;
  if (job.state === "DONE")
    return `「${label}」のマージとIssue完了を確認しました。`;
  if (job.state === "AWAITING_HUMAN")
    return `「${label}」の変更は確認待ちです。PRの内容を確認してください。`;
  if (job.waiting?.length) return translate(job.waiting[0]);
  const descriptions = {
    spec: `「${label}」の受入条件と依存関係を確認しています。`,
    context: `「${label}」の実装に必要な資料を取得しています。`,
    implement: `「${label}」に沿ってMiniMaxが${job.repairs ? "修正" : "変更"}案を生成しています。`,
    validate: `「${label}」の変更案が許可された範囲内か検査しています。`,
    publish: `「${label}」の変更を保存済みです。PR公開の段階です。`,
    checks: `「${label}」のCI・レビュー証拠を取得しています。`,
    review: `「${label}」のレビューと未解決の指摘を確認しています。`,
    merge: `「${label}」のマージ結果を確認しています。`,
  };
  return descriptions[job.stage] || "保存された進捗を確認しています。";
}
function render() {
  const focusedJob = document.activeElement?.dataset?.job;
  $("demo-notice").hidden = !demo;
  $("connection-error").hidden = !failed;
  const options = ["", ...data.repositories];
  const repo = $("repository").value;
  if (
    Array.from($("repository").options)
      .map((x) => x.value)
      .join() !== options.join()
  ) {
    $("repository").replaceChildren(
      ...options.map((v) => {
        const o = element("option", "", v || "すべてのプロジェクト");
        o.value = v;
        return o;
      }),
    );
    $("repository").value = options.includes(repo) ? repo : "";
  }
  const jobs = allJobs().filter(
    (j) => !$("repository").value || j.repo === $("repository").value,
  );
  if (!jobs.some((j) => String(j.id) === String(selected))) {
    selected = jobs[0]?.id;
    selectedStage = null;
    if (mobile.matches) focusCurrent();
  }
  const job = currentJob();
  text("job-count", jobs.length);
  $("jobs").replaceChildren();
  jobNodes = new Map();
  for (const j of jobs) {
    const b = element("button", `job${j.id === selected ? " selected" : ""}`);
    b.setAttribute("aria-pressed", String(j.id === selected));
    b.dataset.job = String(j.id);
    jobNodes.set(String(j.id), b);
    const top = element("div", "job-top");
    top.append(
      element("span", "", `#${j.issue}`),
      age(j.updated),
    );
    const status = element("div", `job-status ${stateClass(j.state)}`);
    status.append(
      element("span", "mini-dot"),
      element("span", "", names[j.state] || j.state),
    );
    b.append(
      top,
      element("div", "job-title", j.title),
      status,
      element("div", "job-repo", j.repo.split("/").at(-1)),
    );
    b.onclick = () => {
      selected = j.id;
      selectedStage = null;
      if (mobile.matches) focusCurrent();
      render();
    };
    $("jobs").append(b);
  }
  if (!jobs.length) {
    const empty = element("div", "empty", "まだジョブがありません。");
    empty.append(
      element(
        "small",
        "",
        "workerで処理を開始すると、ここにIssueが並びます。デモで画面の動きを確認できます。",
      ),
    );
    $("jobs").append(empty);
  }
  if (focusedJob !== undefined) jobNodes.get(focusedJob)?.focus();
  const worker = data.worker.status;
  text(
    "worker-label",
    worker === "online"
      ? "worker応答あり"
      : worker === "offline"
        ? "worker停止 / 応答なし"
        : "workerの稼働情報なし",
  );
  $("worker-dot").className = `dot ${worker}`;
  text(
    "worker-time",
    data.worker.last_seen
      ? `最終応答 ${clock(data.worker.last_seen)}`
      : "進捗記録のみ表示",
  );
  text("project-name", job?.repo || "AUTONOMOUS DEVELOPMENT");
  text("job-title", job?.title || "作業の流れを、一目で。");
  text(
    "job-number",
    job
      ? `Issue #${job.issue}${job.pr ? ` · PR #${job.pr}` : ""}`
      : "Issueを選択してください",
  );
  text("mode", data.dry_run ? "DRY RUN" : "LIVE");
  text(
    "repair-count",
    job && job.state !== "PLANNED" ? `修正 ${job.repairs}回` : "",
  );
  text("job-state", names[job?.state] || "未開始");
  $("job-state").className = `state-pill ${job ? stateClass(job.state) : ""}`;
  text("activity", activity(job));
  text(
    "activity-label",
    job?.state === "BLOCKED"
      ? "停止の確認が必要"
      : worker === "online"
        ? "現在の動き"
        : "最後の進捗記録（workerの応答なし）",
  );
  text("updated", `取得 ${clock(data.time)}`);
  renderFlow(job);
  renderDetails(job);
  renderHistory(job);
}
function age(stamp) {
  const e = element("span", "job-age", stamp ? elapsed(stamp) : "");
  if (stamp) e.dataset.updated = stamp;
  return e;
}
function refreshAges() {
  for (const e of $("jobs").querySelectorAll(".job-age"))
    if (e.dataset.updated)
      e.textContent = elapsed(Number(e.dataset.updated));
}
function renderFlow(job) {
  const root = $("flow");
  const focusedStage = document.activeElement?.dataset?.stage;
  root.replaceChildren();
  stageNodes = new Map();
  const defs = svg("defs");
  const marker = svg("marker", {
    id: "arrow",
    viewBox: "0 0 8 8",
    refX: 7,
    refY: 4,
    markerWidth: 6,
    markerHeight: 6,
    orient: "auto-start-reverse",
  });
  marker.append(svg("path", { d: "M0 0 L8 4 L0 8 Z", fill: "#626c7b" }));
  defs.append(marker);
  root.append(defs);
  const current = data.stages.findIndex(
    (s) => s.id === visibleStage(job?.stage),
  );
  for (let i = 0; i < 8; i++) {
    const [x, y] = positions[i],
      [nx, ny] = positions[i + 1];
    let d;
    if (y === ny)
      d =
        nx > x
          ? `M${x + 210} ${y + 54} H${nx - 8}`
          : `M${x} ${y + 54} H${nx + 218}`;
    else d = `M${x + 105} ${y + 108} V${ny - 8}`;
    root.append(
      svg("path", {
        d,
        class: `edge${job && i < current ? " complete" : ""}`,
        "marker-end": "url(#arrow)",
      }),
    );
  }
  root.append(
    svg("path", {
      d: "M135 455 V417 Q135 402 150 402 H837 Q850 402 850 387 V160 Q850 140 827 140 H820",
      class: "edge loop",
      "marker-end": "url(#arrow)",
    }),
  );
  root.append(
    svg(
      "text",
      { x: 470, y: 391, class: "loop-label" },
      `レビューの指摘 → 修正${job ? ` · 現在 ${job.repairs}回` : ""}`,
    ),
  );
  data.stages.forEach((stage, i) => {
    const [x, y] = positions[i];
    let status = "未実行",
      cls = "node";
    if (job && i < current) {
      status = "通過済み";
      cls += " completed";
    }
    if (job && i === current) {
      status =
        job.state === "DONE"
          ? "完了"
          : job.state === "BLOCKED"
            ? "停止"
            : job.state === "PLANNED"
              ? "事前確認済み"
              : job.state === "AWAITING_HUMAN"
                ? "確認待ち"
                : job.stage === "merge"
                  ? "マージ確認中"
                  : job.state === "REVIEW"
                    ? "待機・確認中"
                    : "現在の工程";
      cls +=
        job.state === "DONE"
          ? " completed"
          : job.state === "BLOCKED"
            ? " current blocked"
            : " current";
    }
    if (selectedStage === stage.id) cls += " selected";
    const group = svg("g", {
      class: cls,
      role: "button",
      tabindex: 0,
      "data-stage": stage.id,
      "aria-label": `${stage.label}：${status}`,
      "aria-pressed": String(
        (visibleStage(selectedStage || job?.stage) || "spec") === stage.id,
      ),
    });
    group.append(
      svg("rect", { x, y, width: 210, height: 108 }),
      svg(
        "text",
        { x: x + 16, y: y + 24, class: "number" },
        `${String(i + 1).padStart(2, "0")}  /  ${["ISSUE", "CONTEXT", "MINIMAX", "POLICY", "GITHUB", "CI", "REVIEW", "HUMAN", "DONE"][i]}`,
      ),
      svg("text", { x: x + 16, y: y + 54, class: "label" }, stage.label),
      svg("circle", { cx: x + 20, cy: y + 83, r: 3, class: "status-mark" }),
      svg("text", { x: x + 30, y: y + 87, class: "status" }, status),
    );
    const select = (viaKeyboard) => {
      selectedStage = stage.id;
      renderDetails(job);
      renderFlow(job);
      if (viaKeyboard) stageNodes.get(stage.id)?.focus();
    };
    group.onclick = () => select(false);
    group.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        select(true);
      }
    };
    root.append(group);
    stageNodes.set(stage.id, group);
  });
  root.setAttribute("viewBox", view.join(" "));
  if (focusedStage !== undefined) stageNodes.get(focusedStage)?.focus();
}
function renderDetails(job) {
  const id = visibleStage(selectedStage || job?.stage) || "spec";
  const i = Math.max(
      0,
      data.stages.findIndex((s) => s.id === id),
    ),
    stage = data.stages[i];
  text("detail-number", String(i + 1).padStart(2, "0"));
  text("detail-title", stage.label);
  text("detail-description", stage.description);
  text("task-summary", job ? job.title : "まだジョブがありません。");
  text("detail-state", job ? `${names[job.state]} (${job.state})` : "—");
  text("detail-calls", job ? `${job.calls}回` : "—");
  text("detail-repairs", job ? `${job.repairs}回` : "—");
  text("detail-sha", job?.head?.slice(0, 7) || "—");
  const reasons = job?.reason ? [translate(job.reason)] : job?.waiting || [];
  $("reason-section").hidden = !reasons.length;
  text(
    "reason-heading",
    job?.state === "BLOCKED" ? "停止の理由" : "待機・確認の理由",
  );
  text("reason", reasons.map(translate).join(" / "));
  $("files").replaceChildren(
    ...(job?.files?.length
      ? job.files
      : ["変更ファイルはまだ記録されていません"]
    ).map((p) => element("li", "", p)),
  );
  $("links").replaceChildren();
  for (const [url, label] of [
    [job?.issue_url, "Issueを開く ↗"],
    [job?.pr_url, "PRを開く ↗"],
  ])
    if (url) {
      const a = element("a", "", label);
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      $("links").append(a);
    }
}
function renderHistory(job) {
  $("timeline").replaceChildren();
  const history = (job?.events || []).slice(-8).reverse();
  for (const e of history) {
    const row = element("li"),
      time = element("time", "", clock(e.time));
    const label =
      e.event === "STEP"
        ? `${data.stages.find((s) => s.id === e.stage)?.label || "マージ確認"}の工程に入りました`
        : e.event === "WAIT"
          ? (e.reasons || []).map(translate).join(" / ")
          : e.event === "BLOCKED"
            ? `停止：${translate(e.reason || "原因を確認してください")}`
            : `状態が「${names[e.event] || e.event}」になりました`;
    row.append(
      time,
      element("span", "timeline-dot"),
      element("span", "timeline-text", label),
    );
    $("timeline").append(row);
  }
  if (!history.length)
    $("timeline").append(
      element(
        "li",
        "empty",
        job?.state === "PLANNED"
          ? "dry-runで実行条件を確認済みです。モデル呼出し・GitHub書込みはありません。"
          : "工程が進むと、ここに履歴が表示されます。",
      ),
    );
}
async function refresh() {
  if (busy) return;
  busy = true;
  const mode = demo;
  try {
    const r = await fetch(mode ? "/api/demo" : "/api/status", {
      cache: "no-store",
    });
    if (!r.ok) throw Error("unavailable");
    const next = await r.json();
    if (mode !== demo) return;
    const signature = JSON.stringify({
      ...next,
      time: 0,
      worker: { ...next.worker, last_seen: 0 },
    });
    const changed = failed || renderedSignature !== signature;
    data = next;
    failed = false;
    if (changed) {
      renderedSignature = signature;
      render();
    } else {
      text("updated", `取得 ${clock(data.time)}`);
      text(
        "worker-time",
        data.worker.last_seen
          ? `最終応答 ${clock(data.worker.last_seen)}`
          : "進捗記録のみ表示",
      );
      refreshAges();
    }
  } catch {
    // Live polling fails silently otherwise, leaving stale progress on screen.
    failed = true;
    text(
      "connection-error",
      data
        ? "サーバーに接続できません。最後に取得した記録を表示しています。"
        : "サーバーに接続できません。`ado dashboard`の起動を確認してください。",
    );
    $("connection-error").hidden = false;
    // Retained records still age during an outage.
    refreshAges();
  } finally {
    busy = false;
  }
}
$("repository").onchange = () => {
  selected = null;
  selectedStage = null;
  render();
};
$("demo").onclick = () => {
  demo = !demo;
  $("demo").setAttribute("aria-pressed", String(demo));
  text("demo", demo ? "実データに戻る" : "デモを見る");
  selected = null;
  selectedStage = null;
  refresh();
};
function setView() {
  $("flow").setAttribute("viewBox", view.join(" "));
}
function focusCurrent() {
  const j = currentJob();
  const i = Math.max(
    0,
    data.stages.findIndex((s) => s.id === visibleStage(j?.stage)),
  );
  const [x, y] = positions[i];
  const w = mobile.matches ? 360 : 650;
  const h = mobile.matches ? 360 : 467;
  view = [x + 105 - w / 2, y + 54 - h / 2, w, h];
  setView();
}
mobile.addEventListener("change", () => {
  if (!data) return;
  if (mobile.matches) focusCurrent();
  else {
    view = [0, 0, 870, 625];
    setView();
  }
});
function zoom(factor) {
  const [x, y, w, h] = view;
  const nw = Math.min(1740, Math.max(350, w * factor));
  const nh = (nw * 625) / 870;
  view = [x + (w - nw) / 2, y + (h - nh) / 2, nw, nh];
  setView();
}
$("zoom-in").onclick = () => zoom(0.8);
$("zoom-out").onclick = () => zoom(1.25);
$("fit").onclick = () => {
  view = [0, 0, 870, 625];
  setView();
};
$("locate").onclick = () => {
  const j = currentJob();
  selectedStage = visibleStage(j?.stage);
  focusCurrent();
  render();
};
$("canvas").addEventListener("pointerdown", (e) => {
  if (e.target.closest(".node")) return;
  drag = { x: e.clientX, y: e.clientY, view: [...view] };
  $("canvas").setPointerCapture(e.pointerId);
});
$("canvas").addEventListener("pointermove", (e) => {
  if (!drag) return;
  const r = $("flow").getBoundingClientRect();
  view = [
    drag.view[0] - ((e.clientX - drag.x) * drag.view[2]) / r.width,
    drag.view[1] - ((e.clientY - drag.y) * drag.view[3]) / r.height,
    drag.view[2],
    drag.view[3],
  ];
  setView();
});
$("canvas").addEventListener("pointerup", () => {
  drag = null;
});
$("canvas").addEventListener("pointercancel", () => {
  drag = null;
});
refresh();
setInterval(refresh, 3000);
