// Page check for demo/static (called by tests/test_demo_static.py; needs Playwright: PLAYWRIGHT_MODULE).
// Opens index.html from disk at the given widths, blocks every non-file request (the tracker included), clicks every
// board row and prints one JSON report: page errors, external requests, overflow, the visible dollar amounts, chips,
// row keys and the detail headings per row.
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
  await b.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
