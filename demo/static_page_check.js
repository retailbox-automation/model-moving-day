// Page check for demo/static (called by tests/test_demo_static.py; needs Playwright: PLAYWRIGHT_MODULE).
// Opens index.html from disk at the given widths, blocks every non-file request (the tracker included), clicks every
// board row and prints one JSON report: page errors, external requests, overflow, the visible dollar amounts, chips,
// row keys and the detail headings per row; then the first screen at 1920x1200 (texts, cards, funnel, links, the re-run
// panel), sideways overflow of the first screen at 390-1920 px, and the page with no scan block.
const path = require("path");
const pw = require(process.env.PLAYWRIGHT_MODULE);
const URL = "file://" + path.resolve(__dirname, "static", "index.html");
(async () => {
  const b = await pw.chromium.launch(process.env.PW_EXEC ? { executablePath: process.env.PW_EXEC } : {});
  const out = {};
  const fit = {};
  for (const w of [1280, 1366, 1440, 1536, 1680]) {   // laptops: the board must keep every column inside its card
    const p = await b.newPage({ viewport: { width: w, height: 900 } });
    await p.route("**/*", r => r.request().url().startsWith("file://") ? r.continue() : r.abort());
    await p.goto(URL); await p.waitForTimeout(200);
    fit[w] = await p.evaluate(() => { const d = document.querySelector(".board"); return { board: d.clientWidth, table: d.querySelector("table").scrollWidth, page: document.documentElement.scrollWidth }; });
    await p.close();
  }
  out.fit = fit;
  for (const w of [1920, 390]) {
    const p = await b.newPage({ viewport: { width: w, height: 1000 } });
    const errors = [], external = [];
    p.on("pageerror", e => errors.push(String(e)));
    await p.route("**/*", r => { const u = r.request().url(); if (u.startsWith("file://")) return r.continue(); external.push(u); return r.abort(); });
    await p.goto(URL); await p.waitForTimeout(300);
    const rows = await p.$$eval(".bt tbody tr", t => t.map(r => r.dataset.key));
    const details = {};
    for (const k of rows) {
      await p.click(`.bt tbody tr[data-key="${k}"]`);
      await p.waitForTimeout(80);
      details[k] = await p.$eval("#detail", d => ({ h2: d.querySelector("h2").innerText, text: d.innerText }));
    }
    const r = await p.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      text: document.body.innerText,
      chips: [...document.querySelectorAll(".bt .chip")].map(c => c.innerText),
      money: [...document.querySelectorAll(".bt .money")].map(c => c.innerText),
    }));
    out[w] = Object.assign(r, { errors, external, rows, details });
    await p.close();
  }
  // the first screen at the picked image's size: what it shows, where it ends, its links and the re-run panel
  {
    const p = await b.newPage({ viewport: { width: 1920, height: 1200 } });
    const errors = [];
    p.on("pageerror", e => errors.push(String(e)));
    await p.route("**/*", r => r.request().url().startsWith("file://") ? r.continue() : r.abort());
    await p.goto(URL); await p.waitForTimeout(300);
    const f = await p.evaluate(() => {
      const q = s => document.querySelector(s), qa = s => [...document.querySelectorAll(s)];
      return {
        text: q(".first").innerText,
        tileBottom: q("#rerunTile").getBoundingClientRect().bottom,
        boardTop: q("#board-card").getBoundingClientRect().top,
        cardsBottom: Math.max(...qa(".rcard").map(c => c.getBoundingClientRect().bottom)),
        date: q("#dsDate").innerText, what: q("#dsWhat").innerText, source: q(".ds-src").href,
        calendar: [q("#calMonth").innerText, q("#calDay").innerText, q("#calYear").innerText],
        scan: q("#dsScan").hidden ? null : { text: q("#dsScan").innerText, href: q("#dsScan a").href },
        notice: q("#dsNotice").hidden ? null : { text: q("#dsNotice").innerText, href: q("#dsNotice").href, title: q("#dsNotice").title },
        cards: qa(".rcard").map(c => ({
          site: c.dataset.site, tag: c.querySelector(".rc-tag").innerText, chip: c.querySelector(".rc-chip").innerText,
          beside: c.querySelector(".rc-pick").innerText.replace(c.querySelector(".rc-chip").innerText, "").trim(),
          money: c.querySelector(".rc-money").innerText, more: (c.querySelector(".rc-more") || {}).innerText || null,
          rule: (c.querySelector(".rc-rule") || {}).innerText || null, note: (c.querySelector(".lnote") || {}).innerText || null,
          fix: (c.querySelector(".lfix") || {}).innerText || null,
          rows: [...c.querySelectorAll(".lrow")].map(r => ({ name: r.querySelector(".lname").innerText,
            dots: [...r.querySelectorAll(".dot")].map(d => d.className.replace("dot ", "")).join(""), fallback: !!r.querySelector(".tag") })),
        })),
        roles: qa(".roles .who").map(r => r.innerText.replace(/\s+/g, " ")),
        funnel: qa(".fs").map(s => [+s.dataset.n, s.querySelector(".fn").innerText, s.querySelector(".ft").innerText]),
        fnote: q("#fnote").innerText, tile: q("#rerunTile").innerText, pills: qa(".pill").map(x => x.innerText),
        tracker: !!q('script[src="https://t.retailbox-automation.com/t.js"]'),
      };
    });
    f.diffs = {};
    for (const site of f.cards.map(c => c.site)) {
      await p.click(`.rcard[data-site="${site}"] .rc-diff`); await p.waitForTimeout(150);
      f.diffs[site] = await p.evaluate(() => ({ h2: document.querySelector("#detail h2").innerText, diff: !!document.querySelector("#detail .diff"), hash: location.hash }));
    }
    await p.evaluate(() => window.scrollTo(0, 0));
    await p.click("#rerunTile"); await p.waitForTimeout(300);
    f.panel = await p.evaluate(() => ({ open: document.getElementById("rerunPanel").open, side: document.querySelector(".rr-side").innerText,
      head: document.querySelector(".rr-head").innerText + "\n" + document.querySelector(".rr-sub").innerText,
      links: [...document.querySelectorAll(".rr-links a")].map(a => a.getAttribute("href")) }));
    await p.click(".tb-end"); await p.waitForTimeout(100);
    f.panel.term = await p.$eval(".term", t => t.innerText);
    f.errors = errors;
    out.first = f;
    await p.close();
  }
  // no sideways scroll anywhere in the first screen, phone to desktop
  const firstFit = {};
  for (const w of [390, 1280, 1440, 1536, 1680, 1920]) {
    const p = await b.newPage({ viewport: { width: w, height: 900 } });
    await p.route("**/*", r => r.request().url().startsWith("file://") ? r.continue() : r.abort());
    await p.goto(URL); await p.waitForTimeout(200);
    firstFit[w] = await p.evaluate(() => ({ page: document.documentElement.scrollWidth,
      clipped: [...document.querySelectorAll(".first *")].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== "visible" && !e.matches("pre")).map(e => e.className),
      outside: [...document.querySelectorAll(".first *")].filter(e => { const r = e.getBoundingClientRect(); return r.width && (r.right > innerWidth + 0.5 || r.left < -0.5); }).map(e => e.className) }));
    await p.close();
  }
  out.firstFit = firstFit;
  // with no scan block in the data the scan line is hidden
  {
    const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
    await p.addInitScript(() => { let m; Object.defineProperty(window, "MMD", { configurable: true, get: () => m, set: v => { v.scan = null; m = v; } }); });
    await p.route("**/*", r => r.request().url().startsWith("file://") ? r.continue() : r.abort());
    await p.goto(URL); await p.waitForTimeout(200);
    out.noScan = await p.evaluate(() => ({ hidden: document.getElementById("dsScan").hidden, text: document.querySelector(".datestrip").innerText }));
    await p.close();
  }
  await b.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
