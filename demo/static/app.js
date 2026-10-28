// Model Moving Day — replay page. Everything shown comes from window.MMD (data.js = demo/data/census.json, the same data
// census/report.py renders into CENSUS.md, plus flip-test names from census/MANIFEST.json and per-test counts from the
// ladder files). No network, no keys, no model calls.
(function () {
  "use strict";
  const M = window.MMD, C = M.census, H = C.headline, DET = M.details;
  const RUNGS = ["Nano", "Lightning", "Super", "Ultra"];
  const $ = id => document.getElementById(id);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = s => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const short = site => site.split("/").pop();
  const usd = x => "$" + x.toFixed(3);
  const K = C.runs[Object.keys(C.runs)[0]].k;

  const headlineRun = t => {
    const runs = C.targets.find(x => x.target === t).runs.map(r => C.runs[r]);
    return runs.find(r => r.headline) || runs[runs.length - 1];
  };
  const cellsOf = site => {
    const run = C.runs[DET[site].run_id];
    return run.cells.filter(c => c.site === site);
  };
  const isOff = c => c.params && c.params.reasoning === "off";
  const label = c => c.rung + (isOff(c) ? " + reasoning off" : "");
  const chosenCell = s => cellsOf(s.site).find(c => c.role === "ladder" && c.rung === s.chosen && (s.fix === "reasoning_off") === isOff(c));
  const fallbackCell = s => cellsOf(s.site).find(c => c.role === "fallback");

  function dots(c, big) {
    const out = [];
    const failed = c.valid_runs - c.passed_runs;
    for (let i = 0; i < K; i++) {
      const cls = i < c.passed_runs ? "p" : i < c.passed_runs + failed ? "f" : c.status === "infra" ? "i" : "n";
      out.push(`<span class="dot ${cls}"></span>`);
    }
    const t = `${c.passed_runs} of ${c.valid_runs} valid runs passed` + (c.infra_runs ? `; ${c.infra_runs} runs set aside as infrastructure` : "");
    return `<span class="dots${big ? " big" : ""}" title="${esc(t)}" role="img" aria-label="${esc(t)}">${out.join("")}</span>`;
  }

  // ---- first screen: date strip, promise, three receipts, who does what, funnel, re-run tile ----
  const REPO = "https://github.com/retailbox-automation/model-moving-day/blob/main/";
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const decided = C.sites.filter(s => s.status === "chosen");
  const costly = decided.reduce((a, s) => (s.after_over_before || 0) > (a.after_over_before || 0) ? s : a, {});
  const words = n => ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][n] || String(n);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const testName = t => t.split("::").pop().replace(/\[[^\]]*\]$/, "");
  const RESULT_DATE = id => `${id.slice(0, 4)}-${id.slice(4, 6)}-${id.slice(6, 8)} ${id.slice(9, 11)}:${id.slice(11, 13)} UTC`;

  // the retirement date and the retiring models, as the pushed manifest states them (baseline.why)
  const ret = C.manifest.baseline.why.match(/replacement for (.+?) \(retiring (\d{4})-(\d{2})-(\d{2})/);
  if (ret) {
    const month = MONTHS[+ret[3] - 1], day = String(+ret[4]), year = ret[2];
    $("dsDate").textContent = `${month} ${day}, ${year}:`;
    $("dsWhat").textContent = "OpenAI shuts down " + ret[1].replace(/gpt-/g, "GPT-").replace(/-mini/g, " mini");
    $("calMonth").textContent = month; $("calDay").textContent = day; $("calYear").textContent = year;
  }
  if (M.scan) {
    const sc = M.scan;
    $("dsScan").innerHTML = `<a href="${REPO}${esc(sc.dir)}/SCAN.md" rel="noopener">${sc.T} of ${sc.K} popular Python LLM repositories</a> still name them in their tests (static scan, rule pushed before the scan).`;
    $("dsScan").hidden = false;
  }
  $("promiseSub").textContent = `The smallest NVIDIA Nemotron on Nebius Token Factory that the repository's own tests accept, ${K} runs per model in Token Factory Sandboxes, priced against the next bigger Nemotron. Or the reason the tests can't vouch for it.`;

  // three receipts: the decided sites whose receipt is compared with OpenAI's priced replacement, in census order
  const shownSites = decided.filter(s => !s.note);
  function ladderRow(name, c, extra) {
    const d = c ? dots(c) : `<span class="dots">${'<span class="dot n"></span>'.repeat(K)}</span>`;
    return `<div class="lrow"><span class="lname">${esc(name)}</span>${d}${c && c.role === "fallback" ? '<span class="tag tag-blue">fallback</span>' : ""}${extra || ""}</div>`;
  }
  function receiptCard(s) {
    const cells = cellsOf(s.site), ch = chosenCell(s), fb = fallbackCell(s), run = C.runs[DET[s.site].run_id];
    const small = ["Nano", "Lightning"].includes(s.chosen);
    let kind, tag, rest = "", rowsHtml, beside;
    const testsXruns = `<span class="rc-runs">${plural(s.flip_tests, "repo test")} × ${K} runs</span>`;
    if (s.fix) {
      kind = "remedy"; tag = "Moved with a remedy listed in advance";
      const asked = cells.find(c => c.rung === ch.rung && !isOff(c));
      const m = asked && asked.first_failure.match(/assert '(.*?)' == '(.*?)'/);
      rowsHtml = ladderRow(asked.rung, asked) +
        (m ? `<div class="lnote"><code>'${esc(m[1])}' ≠ '${esc(m[2])}'</code></div>` : "") +
        `<div class="lfix" title="the one fix the pre-registered list allows for this diagnosis"><span class="arrow">↓</span>${esc(s.fix.replace(/_/g, " "))}</div>` +
        ladderRow(ch.rung, ch) + (fb ? ladderRow(fb.rung, fb) : "");
      beside = testsXruns;
    } else {
      rowsHtml = RUNGS.map(g => ladderRow(g, cells.find(c => c.rung === g && !isOff(c)))).join("");
      if (small) { kind = "down"; tag = "Moved down"; beside = testsXruns; }
      else {
        const below = DET[s.site].cells.find(c => c.rung === RUNGS[RUNGS.indexOf(s.chosen) - 1] && c.role === "ladder" && !isOff(c));
        const tv = Object.entries(below.per_test_passed), bad = tv.filter(([, v]) => v[0] < v[1]);
        kind = "stayed"; tag = `Stayed on ${s.chosen}:`; rest = `${words(bad.length)} test${bad.length === 1 ? "" : "s"} said no`;
        beside = `<span class="rc-why">${esc(below.rung)} passed ${tv.length - bad.length} of ${tv.length} tests; ` +
          bad.map(([t, v]) => `<code>${esc(testName(t))}</code> ${v[0] === 0 ? "failed in every run" : `passed ${v[0]} of ${v[1]} runs`}`).join("; ") + `</span>`;
      }
    }
    const more = s.after_over_before && s.after_over_before > 1.5;
    return `<article class="rcard rc-${kind}" data-site="${esc(s.site)}"><div class="rc-in">` +
      `<span class="rc-tag"><span class="up">${esc(tag)}</span>${rest ? " " + esc(rest) : ""}</span>` +
      `<p class="rc-site">${esc(s.target)} · ${esc(short(s.site))}</p>` +
      `<div class="rc-ladder">${rowsHtml}</div>` +
      `<div class="rc-pick"><span class="rc-chip">${esc(ch.rung)}</span>${beside}</div>` +
      `<p class="rc-money"><b>${usd(ch.usd_per_1000_calls)}</b> per 1,000 calls<span class="sep"> · </span>next bigger, ${esc(fb.rung)}: ${usd(fb.usd_per_1000_calls)}</p>` +
      (more ? `<p class="rc-more">${s.after_over_before.toFixed(1)}× OpenAI's priced replacement, shown as is</p>` : "") +
      (s.fix ? `<span class="rc-rule">rule-backed, not a discovery</span>` : "") +
      `<a class="rc-diff" href="#site=${encodeURIComponent(s.site)}" data-site="${esc(s.site)}">see the branch diff</a>` +
      `</div></article>`;
  }
  $("receipts").innerHTML = shownSites.map(receiptCard).join("");
  $("receipts").dataset.run = shownSites.map(s => s.site).join(" ");

  // who does what: the ladder rungs answer the app's calls; the brains come from the census agent step logs
  const rungOf = m => RUNGS.find(g => m.toLowerCase().includes(g.toLowerCase()));
  const brains = {};
  Object.entries(M.brains || {}).forEach(([t, b]) => {
    b.diagnose_models.forEach(m => { const r = rungOf(m); if (r) (brains[r] = brains[r] || { diag: [], patch: [] }).diag.push(t); });
    b.patch_models.forEach(m => { const r = rungOf(m); if (r) (brains[r] = brains[r] || { diag: [], patch: [] }).patch.push(t); });
  });
  const roleText = b => {
    if (b.diag.length && b.diag.join() === b.patch.join()) return `diagnoses and writes the branch (${b.patch.join(", ")})`;
    return [b.diag.length ? `diagnoses red cells (${b.diag.join(", ")})` : "", b.patch.length ? `writes the branch (${b.patch.join(", ")})` : ""].filter(Boolean).join("; ");
  };
  $("roles").innerHTML = `<span class="who who-ladder">${RUNGS.map(g => `<b>${g}</b>`).join(" · ")} answer the calls of the repos' tests</span>` +
    RUNGS.filter(g => brains[g]).map((g, i) => `<span class="who who-brain${i ? " who-alt" : ""}"><b>Nemotron ${g}</b> ${esc(roleText(brains[g]))}</span>`).join("");

  // funnel: the 26 sites found, minus the Responses API and embedding sites set aside before the ladders, then the
  // sites whose flip tests a live model can pass (the ladder), then the decisions
  const nm0 = C.not_measured;
  const nResp = nm0.filter(x => /Responses API/i.test(x.why)).length, nEmb = nm0.filter(x => /embedding call site/i.test(x.why)).length;
  const routable = H.sites_found - nResp - nEmb;
  $("funnel").innerHTML = [[H.sites_found, "LLM call sites found"], [routable, `left after ${nResp + nEmb} set aside`],
    [H.sites_in_ladder, "where tests notice a wrong answer, a live model can pass"], [H.decided, `decided (${H.repositories_decided.join(", ")})`]]
    .map(([n, t]) => `<li class="fs" data-n="${n}"><span class="fn">${n}</span><span class="ft">${esc(t)}</span></li>`).join("");
  const noDecision = [...new Set(C.sites.filter(s => s.status === "infra").map(s => s.target))].map(t => {
    const rs = C.targets.find(x => x.target === t).runs.map(r => C.runs[r]), tot = {};
    rs.forEach(r => Object.entries(r.proxy_error_statuses).forEach(([k, v]) => { tot[k] = (tot[k] || 0) + v; }));
    const codes = Object.keys(tot).sort((a, b) => tot[b] - tot[a]).join("/");
    return `${t}: no decision (${[codes, rs.some(r => r.lost_jobs) ? "lost jobs" : ""].filter(Boolean).join(", ")})`;
  });
  $("fnote").textContent = [`set aside: ${nResp} Responses API (our proxy does not route them), ${nEmb} embedding (fixed model)`, ...noDecision, `our coverage gate ${H.gate_met ? "met" : "not met"}`].join(" · ");

  // the recorded re-run (not part of the census): a tile on the first screen, a panel with its own small player
  const R = M.rerun;
  if (R) {
    const where = `${R.site.split("/")[0]} :${R.site.split(":").pop()}`;
    $("rerunTile").innerHTML = `<span class="play" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8.5 6.2v11.6L18 12z" fill="currentColor"/></svg></span>` +
      `<span class="rt-text">Watch the agent re-run <code>${esc(where)}</code> <small>(terminal recording, not part of the census)</small></span>`;
    $("rerunTile").hidden = false;
    $("rerunTile").addEventListener("click", () => openRerun(where));
  }
  let player = null;
  function ansi(s) {
    const parts = s.replace(/\r\n/g, "\n").replace(/\r/g, "").split(/\x1b\[([0-9;]*)m/);
    let out = "", open = false;
    parts.forEach((p, i) => {
      if (i % 2 === 0) { out += esc(p); return; }
      if (open) { out += "</span>"; open = false; }
      const cls = p.split(";").map(Number).map(c => c === 1 ? "b" : c >= 30 && c <= 37 ? "c" + (c - 30) : "").filter(Boolean);
      if (cls.length) { out += `<span class="${cls.join(" ")}">`; open = true; }
    });
    return out + (open ? "</span>" : "");
  }
  function castPlayer(text, pre, onTick) {
    const lines = text.split("\n").filter(Boolean);
    const ev = []; let t = 0;
    lines.slice(1).forEach(l => { const e = JSON.parse(l); t += e[0]; if (e[1] === "o") ev.push([t, e[2]]); });
    const total = t;
    let pos = 0, speed = 4, raf = 0, last = 0, shown = -1;
    const running = () => raf !== 0;
    function render(force) {
      let n = 0; while (n < ev.length && ev[n][0] <= pos) n++;
      if (n !== shown || force) {
        pre.innerHTML = ansi(ev.slice(0, n).map(e => e[1]).join("")) + (running() && pos < total ? '<span class="cur"></span>' : "");
        shown = n; pre.scrollTop = pre.scrollHeight;
      }
      onTick(pos, total, running(), speed);
    }
    function frame(now) {
      pos = Math.min(total, pos + (now - last) / 1000 * speed); last = now;
      if (pos >= total) { raf = 0; render(true); return; }
      render(false); raf = requestAnimationFrame(frame);
    }
    return {
      total,
      play() { if (running()) return; if (pos >= total) { pos = 0; shown = -1; } last = performance.now(); raf = requestAnimationFrame(frame); render(true); },
      pause() { cancelAnimationFrame(raf); raf = 0; render(true); },
      toggle() { running() ? this.pause() : this.play(); },
      speed(x) { speed = x; render(true); },
      end() { cancelAnimationFrame(raf); raf = 0; pos = total; render(true); },
    };
  }
  const mmss = s => { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  function rerunHTML(where) {
    const cell = c => `<li>${dots(c)}<span class="rr-cl"><b>${esc(c.rung)}${isOff(c) ? " + reasoning off" : c.rung === R.chosen ? ", as requested" : ", next bigger"}</b> ${c.passed_runs}/${c.valid_runs}` +
      ` · $${c.usd_per_1000_calls} per 1,000 calls · ${c.seconds_per_call.toFixed(2)} s per call</span></li>`;
    const conf = R.confirm ? R.confirm.cells.map(c => `<li>${dots(Object.assign({ rung: "" }, c))}<span class="rr-cl"><b>Confirm on the patched code</b> ${c.passed_runs}/${c.valid_runs} (<code>${esc(R.confirm.run_id)}</code>)</span></li>`).join("") : "";
    const brainNames = R.brain_models.map(m => "Nemotron " + (rungOf(m) || m)).join(", ");
    return `<div class="rr-head"><h2 id="rerunTitle">Recorded re-run of <code>${esc(where)}</code></h2><button type="button" class="rr-x" aria-label="Close">×</button></div>` +
      `<p class="rr-sub">${RESULT_DATE(R.run_id)} · terminal recording, no cuts · not part of the census · rules pushed ${esc((R.manifest_pushed_at || "").slice(11, 19))} UTC, before the run (manifest <code>${esc(R.manifest_sha256.slice(0, 8))}</code>)</p>` +
      `<div class="rr-grid"><div class="term-wrap"><div class="term-bar"><button type="button" class="tb-play">Pause</button>` +
      `<span class="tb-speed" role="group" aria-label="Playback speed">${[1, 4, 16].map(x => `<button type="button" data-x="${x}">${x}×</button>`).join("")}</span>` +
      `<span class="tb-time"></span><button type="button" class="tb-end">Show all</button></div><pre class="term" aria-label="terminal output"></pre></div>` +
      `<div class="rr-side"><h3>Cells, ${K} runs each</h3><ul class="rr-cells">${R.cells.map(cell).join("")}${conf}</ul>` +
      `<p class="rr-p">Decision: <b>${esc(R.chosen)}</b> (${esc(R.reason.replace(/_/g, " "))}), the same as in the census.</p>` +
      `<h3>Every model call</h3><p class="rr-p">${R.calls} calls, ${R.calls_with_id} with a Token Factory response id; model cost $${R.model_cost_usd}. ` +
      `${R.brain_steps} of them are brain steps by ${esc(brainNames)} (${R.brain_seconds} s).</p>` +
      `<p class="rr-ids">${R.first_ids.map(i => `<code>${esc(i)}</code>`).join("")}<span>first ${R.first_ids.length} of ${R.calls} ids</span></p>` +
      `<p class="rr-links"><a href="${REPO}${esc(R.result)}" rel="noopener">RESULT.md</a> · <a href="rerun.cast" download>rerun.cast</a> · <a href="${REPO}${esc(R.cast)}" rel="noopener">recording in the repository</a></p>` +
      `</div></div>`;
  }
  function openRerun(where) {
    const d = $("rerunPanel");
    if (!d.dataset.built) {
      d.innerHTML = rerunHTML(where);
      const btn = d.querySelector(".tb-play"), time = d.querySelector(".tb-time"), sp = [...d.querySelectorAll(".tb-speed button")];
      player = M.rerun_cast ? castPlayer(M.rerun_cast, d.querySelector(".term"), (pos, total, run, x) => {
        btn.textContent = run ? "Pause" : pos >= total ? "Replay" : "Play";
        time.textContent = `${mmss(pos)} / ${mmss(total)} real time`;
        sp.forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.x === x)));
      }) : null;
      if (player) {
        btn.addEventListener("click", () => player.toggle());
        sp.forEach(b => b.addEventListener("click", () => player.speed(+b.dataset.x)));
        d.querySelector(".tb-end").addEventListener("click", () => player.end());
      } else d.querySelector(".term-wrap").hidden = true;
      d.querySelector(".rr-x").addEventListener("click", () => d.close());
      d.addEventListener("click", e => { if (e.target === d) d.close(); });
      d.addEventListener("close", () => player && player.pause());
      d.dataset.built = "1";
    }
    if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    if (player) player.play();
  }
  window.MMD_rerun = () => R && openRerun(`${R.site.split("/")[0]} :${R.site.split(":").pop()}`);

  // ---- decision board ----
  const rows = decided.map(s => ({ key: s.site, s }));
  const infraSites = C.sites.filter(s => s.status === "infra");
  const infraTargets = [...new Set(infraSites.map(s => s.target))];
  infraTargets.forEach(t => rows.push({ key: "target:" + t, infra: t, sites: infraSites.filter(s => s.target === t) }));

  function rungCell(cells, rung) {
    const cs = cells.filter(c => c.rung === rung);
    if (!cs.length) return `<span class="dots">${'<span class="dot n"></span>'.repeat(K)}</span>`;
    return cs.map(c => `<div class="stack">${dots(c)}${c.role === "fallback" ? '<span class="tag tag-blue">fallback</span>' : ""}${isOff(c) ? '<span class="tag tag-blue">reasoning off</span>' : ""}</div>`).join("");
  }
  function boardRow(r) {
    if (r.infra) {
      const cells = r.sites.map(s => cellsOf(s.site)).flat();
      const nano = cells.filter(c => c.rung === "Nano");
      const sum = { rung: "Nano", status: "infra", passed_runs: 0, valid_runs: 0, infra_runs: Math.max(...nano.map(c => c.infra_runs)) };
      return `<tr class="infra" data-key="${esc(r.key)}"><td data-label="Library">${esc(r.infra)}</td><td data-label="Call site">${r.sites.length} call sites</td>` +
        `<td class="nowrap" data-label="Flip tests">${r.sites.map(s => s.flip_tests).join(", ")}</td>` +
        `<td class="rung" data-label="Nano">${dots(sum)}</td>` + RUNGS.slice(1).map(g => `<td class="rung" data-label="${g}">${rungCell([], g)}</td>`).join("") +
        `<td class="nowrap" colspan="2" data-label="Model">no decision (infrastructure)</td></tr>`;
    }
    const s = r.s, cells = cellsOf(s.site), fb = fallbackCell(s), ch = chosenCell(s);
    const base = s.note ? "not compared" : usd(s.before_usd_per_1000);
    const more = s.after_over_before && s.after_over_before > 1.5;
    return `<tr class="${more ? "costly" : ""}" data-key="${esc(r.key)}" tabindex="0"><td data-label="Library">${esc(s.target)}</td>` +
      `<td data-label="Call site"><span class="site" title="${esc(s.site)}">${esc(short(s.site))}</span></td><td data-label="Flip tests">${s.flip_tests}</td>` +
      RUNGS.map(g => `<td class="rung" data-label="${g}">${rungCell(cells, g)}</td>`).join("") +
      `<td data-label="Model"><span class="chip">${esc(label(ch))}</span></td>` +
      `<td class="money" data-label="$ per 1,000 calls"><span class="mv">${usd(ch.usd_per_1000_calls)} · ${base} · ${fb ? usd(fb.usd_per_1000_calls) : "-"}${more ? '<span class="tag tag-red">costs more</span>' : ""}</span></td></tr>`;
  }
  $("board").innerHTML = `<table class="bt"><thead><tr><th>Library</th><th>Call site</th><th>Flip tests</th>` +
    RUNGS.map(g => `<th class="rung">${g}</th>`).join("") + `<th>Model</th><th>$ per 1,000 calls:<br>after · base · next bigger</th></tr></thead><tbody>` +
    rows.map(boardRow).join("") + `</tbody></table>`;
  $("legend").innerHTML = `<span class="dot p"></span>pass · <span class="dot f"></span>fail · <span class="dot i"></span>set aside (infrastructure error) · <span class="dot n"></span>not run · one dot = one of the ${K} runs of a cell · ` +
    `after = the chosen model · base = OpenAI's replacement (gpt-6-luna) priced on the same tokens, not run · next bigger = the next Nemotron up, measured on the same tests`;

  // ---- how ----
  const hr = headlineRun(H.repositories_decided[0]);
  $("howSteps").innerHTML = [
    "Install the target repository at a pinned commit into a Token Factory Sandboxes image.",
    "Run its own tests three times through a recording proxy: with a known-good answer, an empty answer and a plausible wrong answer. A test counts for a call site (a flip test) only if it passes the first run and fails the other two.",
    `Freeze the tests by hash and push the rules before the first ladder job: manifest <code>${hr.manifest_sha256.slice(0, 8)}</code> pushed ${hr.pushed_at.slice(11, 19)} UTC (2026-10-24), first decision ladder ${hr.started_utc.slice(11, 19)} UTC${C.smoke.length ? `, after a declared smoke run at ${C.smoke[0].run_id.slice(9, 11)}:${C.smoke[0].run_id.slice(11, 13)}:${C.smoke[0].run_id.slice(13, 15)} UTC` : ""}.`,
    `Climb the ladder Nano → Lightning → Super → Ultra, ${K} runs per cell, pass on ${hr.required_passes} of ${K}; runs hit by 402/404/429/5xx are set aside and re-run. Keep the smallest model that passes and measure the next one up as the fallback.`,
    "A red cell whose call log shows a diagnosis (set by a proxy rule) goes to the planner, Nemotron Super or Ultra, which may pick one fix from a pre-registered list; the fix counts only if the request really changed.",
    "Write the move as a local branch of the target (an opt-in helper, NEMOTRON_MOVE=1; with the flag off the code is the original) and re-run the tests on the patched code.",
  ].map(x => `<li>${x}</li>`).join("");
  $("diffList").innerHTML = [
    "The model for each call site is chosen by the target repository's own frozen tests, not by a price rule for each step.",
    "Each decision comes with a receipt: the chosen model, OpenAI's replacement priced on the same tokens, and the next bigger Nemotron measured on the same tests.",
    "The output is a migration branch confirmed by re-running the repository's tests on the patched code.",
    "Every cell is published, red ones and infrastructure losses included.",
  ].map(x => `<li>${x}</li>`).join("");

  // ---- detail ----
  function hunk(target, site) {
    const pf = M.patch_files[target]; if (!pf) return null;
    const key = site.replace(/[\/.:-]/g, "_");
    const lines = pf.diff.split("\n");
    const i = lines.findIndex(l => l.startsWith("+") && l.includes(key));
    if (i < 0) return null;
    let a = i; while (a > 0 && !lines[a].startsWith("@@")) a--;
    let b = i + 1; while (b < lines.length && !lines[b].startsWith("@@") && !lines[b].startsWith("diff --git") && !lines[b].startsWith("--- new file")) b++;
    let f = a; while (f > 0 && !lines[f].startsWith("+++ ")) f--;
    return { file: lines[f].replace(/^\+\+\+ b\//, ""), lines: lines.slice(a, b) };
  }
  function confirmText(target, site) {
    const p = C.patches.find(x => x.target === target); if (!p) return null;
    // the last confirm run is the one of the committed branch; earlier ones (failed attempts) are counted, not hidden
    const found = p.confirm_runs.map(r => { const m = r.cells.split(";").find(x => x.includes(site)); const k = m && m.match(/confirm (\d+\/\d+) (\w+)/); return k ? { frac: k[1], ok: k[2] === "pass", run: r.run_id } : null; }).filter(Boolean);
    if (!found.length) return null;
    return Object.assign({}, found[found.length - 1], { p, earlier: found.slice(0, -1) });
  }
  function perTestTable(site) {
    const d = DET[site], cells = d.cells;
    const head = cells.map(c => `<th>${esc(c.rung)}${isOff(c) ? "<br>+off" : ""}${c.role === "fallback" ? "<br><small>fallback</small>" : ""}</th>`).join("");
    const miss = t => cells.some(c => { const v = c.per_test_passed[t]; return v && v[0] < v[1]; });
    const tests = d.flip_tests.filter(miss).concat(d.flip_tests.filter(t => !miss(t)));
    const row = t => `<tr><td title="${esc(t)}">${esc(t.split("::").pop())}</td>` + cells.map(c => {
      const v = c.per_test_passed[t]; if (!v) return "<td>-</td>";
      return `<td class="${v[0] < 4 ? "bad" : "ok"}">${v[0]}/${v[1]}</td>`;
    }).join("") + "</tr>";
    const SHOW = 8, first = tests.slice(0, Math.max(SHOW, tests.filter(miss).length)), rest = tests.slice(first.length);
    const table = rows => `<div style="overflow-x:auto"><table class="tt"><thead><tr><th>test</th>${head}</tr></thead><tbody>${rows.map(row).join("")}</tbody></table></div>`;
    return `<p class="sub-h">Flip tests: runs passed per model (${d.flip_tests.length}; tests with a miss first)</p>` + table(first) +
      (rest.length ? `<details class="more"><summary>${rest.length} more flip tests, all passed on every model</summary>${table(rest)}</details>` : "");
  }
  function receipt(s) {
    const ch = chosenCell(s), fb = fallbackCell(s);
    const items = [[`${label(ch)} (chosen)`, ch.usd_per_1000_calls, ""], [`OpenAI's replacement, priced`, s.note ? null : s.before_usd_per_1000, "base"]];
    if (fb) items.push([`${label(fb)} (next bigger)`, fb.usd_per_1000_calls, "big"]);
    const max = Math.max(...items.map(x => x[1] || 0));
    const notes = C.dollar_notes.filter(n => n.includes(s.site)).map(md);
    return `<p class="sub-h">Receipt: $ per 1,000 calls, same tests</p><div class="bars">` + items.map(([n, v, cls]) =>
      `<div class="bar ${cls}"><span>${esc(n)}</span><span class="track"><span class="fill" style="width:${v == null ? 0 : (100 * v / max).toFixed(1)}%"></span></span><span class="v">${v == null ? "not compared" : usd(v)}</span></div>`).join("") +
      `</div>` + notes.map(n => `<p class="fine">${n}</p>`).join("") + `<p class="fine">Nano and Lightning cost the same per token on Token Factory ($0.06 in / $0.24 out per 1M), so a smaller model is not cheaper by itself.</p>`;
  }
  function detailSite(s) {
    const d = DET[s.site], run = C.runs[d.run_id], cells = cellsOf(s.site), ch = chosenCell(s);
    const order = c => (c.role === "fallback" ? 10 : 0) + RUNGS.indexOf(c.rung) + (isOff(c) ? .5 : 0);
    const cs = cells.slice().sort((a, b) => order(a) - order(b));
    let h = `<h2>${esc(s.target)} · <span class="sitename">${esc(short(s.site))}</span></h2>` +
      `<p class="dsub">${esc(s.role)} · ${s.flip_tests} flip test${s.flip_tests > 1 ? "s" : ""} · decision run <code>${esc(d.run_id)}</code> · manifest <code>${run.manifest_sha256.slice(0, 8)}</code> pushed ${run.pushed_at.slice(11, 19)} UTC, before the run</p>`;
    h += `<div class="cells">` + cs.map(c => `<div class="cell${c === ch ? " chosen" : ""}"><div class="ch">${esc(label(c))} <span class="frac">${c.passed_runs}/${c.valid_runs}</span>` +
      `${c.role === "fallback" ? '<span class="role">· fallback</span>' : ""}</div>${dots(c, true)}` +
      `<div class="meta">${c === ch ? "<b>kept</b> · " : ""}${usd(c.usd_per_1000_calls)} per 1,000 calls${c.infra_runs ? ` · ${c.infra_runs} runs set aside (infrastructure)${c.set_aside_failed || c.set_aside_passed ? `: ${c.set_aside_passed} passed, ${c.set_aside_failed} failed underneath` : ""}, re-run` : ""}</div></div>`).join("") + `</div>`;
    const fx = (run.fixes || []).find(f => f.site === s.site);
    if (fx) {
      const before = cells.find(c => c.rung === ch.rung && !isOff(c));
      h += `<div class="note"><svg viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7z" fill="currentColor"/></svg><p><b>Rule-backed fix, not a discovery.</b> ` +
        `${before ? (m => m ? `The test expects <code>'${esc(m[2])}'</code>; ${esc(before.rung)} answered <code>'${esc(m[1])}'</code> in every run, and ` : "")(before.first_failure.match(/assert '(.*?)' == '(.*?)'/)) : ""}a proxy rule flagged the leading newline. The pre-registered fix list allows only “reasoning off” for that diagnosis; ` +
        `the planner (${esc(fx.by.split("/").pop())}) picked it from three options, with a hint in its prompt. The change (<code>chat_template_kwargs.enable_thinking=false</code>) reached ${fx.applied_calls} of ${ch.calls || fx.applied_calls} requests, and ${esc(ch.rung)} then passed ${ch.passed_runs}/${ch.valid_runs}.</p></div>`;
    }
    if (s.note) h += `<div class="note warn"><svg viewBox="0 0 24 24"><path d="M12 3 2 21h20z" fill="currentColor"/></svg><p>${md(C.site_notes[s.note - 1])}</p></div>`;
    h += `<div class="dgrid"><div>${perTestTable(s.site)}</div><div>${receipt(s)}</div></div>`;
    const hk = hunk(s.target, s.site), cf = confirmText(s.target, s.site);
    if (hk && cf) {
      h += `<div class="diff"><div class="diff-head"><span>Branch ${esc(cf.p.branch)} · ${esc(cf.p.commit.slice(0, 7))} · local only · ${esc(hk.file)}</span>` +
        `<span class="ok-badge">confirm on the patched code ${esc(cf.frac)}</span></div><pre>` +
        hk.lines.map(l => `<span class="ln ${l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l)}</span>`).join("") + `</pre></div>` +
        `<p class="fine">The agent wrote the helper in (<code>nemotron_move</code>, off unless NEMOTRON_MOVE=1) and the patched code ran the same tests again in Sandboxes (run <code>${esc(cf.run)}</code>). Nothing was pushed to the upstream project.` +
        (cf.p.earlier_attempts ? ` This branch took ${cf.p.earlier_attempts + 1} attempts; the earlier ones are kept in the evidence.` : "") +
        cf.earlier.map(e => ` An earlier confirm run (<code>${esc(e.run)}</code>) of a failed attempt ended ${esc(e.frac)}: the tests did not even start.`).join("") + `</p>`;
    }
    return h;
  }
  function detailInfra(t) {
    const tg = C.targets.find(x => x.target === t), sites = infraSites.filter(s => s.target === t);
    const rs = tg.runs.map(r => C.runs[r]);
    let h = `<h2>${esc(t)} · <span class="sitename">${sites.length} call sites</span></h2><p class="dsub">No decision: in each of ${rs.length} decision runs (each declared in the manifest before it ran) most runs were set aside for infrastructure errors, so no site reached ${K} valid runs on Nano (at most ${Math.max(...rs.flatMap(r => r.cells.map(c => c.valid_runs)))}).</p>`;
    h += `<div style="overflow-x:auto"><table class="tt"><thead><tr><th>decision run</th><th>error replies from Token Factory</th><th>runs set aside</th><th>jobs lost (our Sandboxes client)</th><th>decided</th></tr></thead><tbody>` +
      rs.map(r => `<tr><td>${esc(r.run_id)}</td><td>${Object.entries(r.proxy_error_statuses).map(([k, v]) => `${v} × ${k}`).join(", ") || "-"}</td><td>${Math.max(...r.cells.map(c => c.infra_runs))} per site</td><td>${r.lost_jobs}${r.lost_runs ? ` (${r.lost_runs} runs)` : ""}</td><td>${r.decided} of ${r.sites}</td></tr>`).join("") + `</tbody></table></div>`;
    h += `<ul class="small" style="margin-top:16px">` + sites.map(s => `<li><code>${esc(s.site)}</code> · ${esc(s.role)} · ${s.flip_tests} flip test${s.flip_tests > 1 ? "s" : ""}</li>`).join("") + `</ul>`;
    const ext = tg.external_failures || [];
    if (ext.length) h += `<p class="fine">Its tests also call ${ext.map(e => esc(e.host)).join(", ")} live; in run <code>${esc(ext[0].run_id)}</code> one 429 from that host was counted as a model failure, because the infrastructure rule sees only the proxy's statuses.</p>`;
    h += `<p class="fine">The third run changed how the runs execute (one after another, with a token budget), declared before it ran but after two failed runs.</p>`;
    return h;
  }
  function show(key, scroll) {
    const r = rows.find(x => x.key === key) || rows.find(x => x.s && x.s.fix) || rows[0];
    $("detail").innerHTML = r.infra ? detailInfra(r.infra) : detailSite(r.s);
    document.querySelectorAll(".bt tbody tr").forEach(tr => tr.classList.toggle("sel", tr.dataset.key === r.key));
    if (scroll) $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  document.querySelectorAll(".bt tbody tr").forEach(tr => {
    const go = () => { history.replaceState(null, "", "#site=" + encodeURIComponent(tr.dataset.key)); show(tr.dataset.key, true); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  });
  const fromHash = () => { const m = location.hash.match(/site=([^&]+)/); return m ? decodeURIComponent(m[1]) : null; };
  show(fromHash(), false);
  window.addEventListener("hashchange", () => show(fromHash(), true));
  window.MMD_show = show;
  // "see the branch diff" on a receipt opens the same detail view as the board row, at its patch
  document.querySelectorAll(".rc-diff").forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    history.replaceState(null, "", "#site=" + encodeURIComponent(a.dataset.site));
    show(a.dataset.site, false);
    (document.querySelector("#detail .diff") || $("detail")).scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  // ---- what did not work ----
  const pq = infraTargets.map(t => {
    const rs = C.targets.find(x => x.target === t).runs.map(r => C.runs[r]);
    const errs = rs.map(r => Object.values(r.proxy_error_statuses).reduce((a, b) => a + b, 0));
    const lost = rs.reduce((a, r) => a + r.lost_jobs, 0);
    return `<li><span class="ic x">×</span><span>${esc(t)}: ${rs.length} declared runs, 503/500 errors and lost jobs → no decision. <span class="why">Error replies per run: ${errs.join(", ")}; jobs lost to our own Sandboxes client: ${lost}.</span></span></li>`;
  });
  const lt = DET[costly.site] && DET[costly.site].cells.find(c => c.rung === "Lightning");
  let one = "";
  if (lt) {
    const tv = Object.entries(lt.per_test_passed), bad = tv.filter(([, v]) => v[0] < 4);
    one = `<li><span class="ic w">!</span><span>${esc(short(costly.site))} went to ${esc(costly.chosen)} because of ${bad.length === 1 ? "one test" : bad.length + " tests"}: Lightning passed ${tv.length - bad.length} of ${tv.length} flip tests in every run, ` +
      `but <code>${esc(bad.map(b => b[0].split("::").pop()).join(", "))}</code> passed ${bad.map(b => b[1][0] + "/" + b[1][1]).join(", ")}. On ${esc(costly.chosen)} it costs ${costly.after_over_before.toFixed(1)}× the priced base.</span></li>`;
  }
  $("failedRows").innerHTML = pq.join("") +
    `<li><span class="ic x">×</span><span>Coverage gate (3 repositories, 5 independent decisions): not met. <span class="why">Decisions in ${H.repositories_decided.length} of ${H.repositories} repositories; ${H.decided} independent decisions per call site, ${H.independent_grouped} if instructor's sync and async faces count once. The gate was written before the runs in our internal notes, not in the pushed manifest.</span></span></li>` +
    one +
    `<li><span class="ic w">!</span><span>Each decision rests on few tests: ${Object.values(H.flip_tests).join(", ")}; the two Nano decisions on ${H.nano_tests} between them.</span></li>` +
    `<li><span class="ic w">!</span><span>Runs set aside as infrastructure and re-run: ${H.set_aside_runs_headline} in the runs behind the board, ${H.set_aside_runs_all} in all decision runs.</span></li>`;

  // ---- found, not measured ----
  const groups = [
    ["Responses API call sites", "our proxy does not route /v1/responses", w => /Responses API/i.test(w)],
    ["Streaming branch of llm", "its tests assert the old model's exact sentences", w => /exact|wording|sentence/i.test(w)],
    ["Embedding calls", "one embedding model on every rung, so not a ladder choice", w => /embedding/i.test(w)],
    ["No test fails on a wrong answer", "the tests reach the site but pass with an empty or wrong answer", w => /no test fails/i.test(w)],
    ["No kept test reaches the site", "found in the source only", w => /reached by none/i.test(w)],
  ];
  const nm = C.not_measured, used = new Set();
  const g = groups.map(([n, why, f]) => { const hit = nm.filter(x => !used.has(x) && f(x.why)); hit.forEach(x => used.add(x)); return [n, why, hit.length]; }).filter(x => x[2]);
  const rest = nm.filter(x => !used.has(x)).length;
  if (rest) g.push(["Other reasons", "each reason in the full list", rest]);
  $("unmeasuredTitle").textContent = `Found, not measured: ${nm.length}`;
  $("unmeasuredRows").innerHTML = g.map(([n, why, k]) => `<li><span class="ic g"></span><span>${esc(n)} (${k}) · <span class="why">${esc(why)}</span></span></li>`).join("");
  $("unmeasuredAll").innerHTML = nm.map(x => `<li><code>${esc(x.site)}</code>: ${esc(x.why)}</li>`).join("");

  // ---- real and synthetic ----
  $("realList").innerHTML = [
    "the target repositories and their own tests at pinned commits, frozen by hash",
    "live calls to Nemotron Nano, Lightning, Super and Ultra (and Qwen3-Embedding-8B) on Token Factory",
    "the Token Factory Sandboxes runs, the planner's fix choice and patch edits",
    "the patched branches and the confirm runs of the patched code",
  ].map(x => `<li>${esc(x)}</li>`).join("");
  $("synList").innerHTML = [
    "the empty and wrong answers in the controls (made up by the proxy)",
    "the \"before\" price: OpenAI's replacement priced on Nemotron's tokens, never called, pass rate assumed 1.0",
    "streaming events re-synthesized by the proxy; per-model request normalization during the ladder",
    "this page: a replay of the recorded census, no model calls",
  ].map(x => `<li>${esc(x)}</li>`).join("");
  $("foot").innerHTML = `<p>Model tokens for the decision ladders: $${H.decision_tokens_usd.toFixed(2)}. Every number on this page is in CENSUS.md, which <code>census/report.py</code> recomputes from the evidence files in the repository.</p>` +
    `<p>Test traffic, not production traffic. Built for the Nebius × NVIDIA Global AI Hackathon (Coding and Agentic Engineering) with NVIDIA Nemotron on Nebius Token Factory.</p>`;
})();
