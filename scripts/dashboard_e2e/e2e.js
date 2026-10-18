// Real-browser smoke test of the central dashboard (Chrome via puppeteer-core).
// Needs a FRESH central server with one admin root / "correct horse battery"
// (see README.md in this folder). Writes screenshots to $OUT.
const puppeteer = require("puppeteer-core");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const OUT = process.env.OUT, BASE = process.env.BASE || "http://127.0.0.1:8765";
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const problems = [];
// The webhook step runs when WEBHOOK_PORT is set: a tiny receiver on that port records what the server sends.
// The central server must then have CENTRAL_DATA_KEY set and CENTRAL_WEBHOOK_ALLOW_HTTP=1 (the receiver is plain http).
const WEBHOOK_PORT = process.env.WEBHOOK_PORT;
const received = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--no-proxy-server"] });
  const receiver = WEBHOOK_PORT ? http.createServer((req, res) => {
    let body = ""; req.on("data", (c) => (body += c));
    req.on("end", () => { received.push({ headers: req.headers, body }); res.writeHead(200); res.end("ok"); });
  }).listen(Number(WEBHOOK_PORT), "127.0.0.1") : null;
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 860 });
  page.on("dialog", (d) => d.accept());          // the dashboard asks "are you sure?" before removing things
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("401")) problems.push("console: " + m.text()); });
  page.on("response", (r) => { if (r.status() >= 400 && !(r.status() === 401 && (r.url().includes("/admin/me") || r.url().includes("/admin/login")))) problems.push(r.status() + " " + r.url()); });
  const shot = async (n) => page.screenshot({ path: path.join(OUT, n + ".png") });
  const text = async () => page.evaluate(() => document.body.innerText);
  const click = async (label) => { const ok = await page.evaluate((l) => { const scope = document.querySelector("dialog[open]") || document; const name = (x) => (x.dataset.view ? x.querySelector("span").textContent : x.textContent).trim();   // a menu link also holds the alert count
const b = [...scope.querySelectorAll("button,a")].find((x) => name(x) === l && x.offsetParent !== null); if (b) { b.click(); return true; } return false; }, label); if (!ok) throw new Error("no button: " + label); await sleep(400); };
  const check = async (name, ok) => { console.log((ok ? "PASS " : "FAIL ") + name); if (!ok) { problems.push("check failed: " + name); console.log("   page text: " + (await text()).replace(/\s+/g, " ").slice(0, 400)); await shot("fail-" + name.replace(/\W+/g, "-").slice(0, 30)); } };

  await page.goto(BASE + "/", { waitUntil: "load" });
  await check("redirect to /dashboard + login form shown", page.url().endsWith("/dashboard") && (await page.$("#login-form")) !== null && await page.$eval("#login", (e) => !e.hidden));
  await shot("01-login");
  await page.type("#login-user", "root"); await page.type("#login-pass", "wrong password!!");
  await click("Sign in"); await sleep(1200);          // the server takes its time over a wrong password
  await check("wrong password message", (await text()).includes("Wrong username or password."));
  await page.$eval("#login-pass", (e) => (e.value = ""));
  await page.type("#login-pass", "correct horse battery"); await click("Sign in"); await sleep(600);
  await check("signed in, overview shown", (await text()).includes("Overview") && (await text()).includes("Needs a look"));
  await shot("02-overview");

  await click("PCs"); await page.type("#new-pc", "Scan-Desk-1"); await click("Add PC"); await sleep(600);
  const code = await page.$eval(".secret", (e) => e.textContent).catch(() => "");
  await check("enrollment code dialog", code.length >= 8);
  await shot("03-enrollment-code");
  await click("Done"); await sleep(500);
  await check("PC listed as not enrolled", (await text()).includes("Scan-Desk-1") && (await text()).includes("not enrolled yet"));
  await click("Rename"); await page.$eval("dialog input", (e) => { e.select(); }); await page.type("dialog input", "Scan-Desk-1-A"); await click("Rename"); await sleep(600);
  await check("PC renamed", (await text()).includes("Scan-Desk-1-A"));
  await shot("04-pcs");

  await click("Projects"); await click("New project...");
  await sleep(500);
  await page.type("dialog input[type=text], dialog input:not([type])", "Bank customer file");
  await click("Save"); await sleep(900);
  await check("project created and opened", (await text()).includes("Bank customer file") && (await text()).includes("Versions"));
  await click("Assign to all PCs"); await sleep(700);
  await check("assigned to the PC", (await text()).includes("assigned"));
  await shot("05-project");

  // the settings form: change a value, save, and see it in the saved version
  await click("Edit / new version"); await sleep(500);
  await check("the project opens as a form with a JSON tab", (await text()).includes("Quality checks") && (await text()).includes("JSON (advanced)"));
  await shot("05b-project-form");
  await page.evaluate(() => { const d = document.querySelector("dialog[open]"); const lab = [...d.querySelectorAll("label")].find((l) => l.textContent.startsWith("Pass score")); const i = lab.querySelector("input"); i.value = "77"; });
  await click("JSON (advanced)"); await sleep(300);
  await check("the form's change shows in the JSON tab", await page.evaluate(() => /"pass_score": 77/.test(document.querySelector("dialog[open] textarea").value)));
  await shot("05c-project-json");
  await click("Form"); await sleep(300);
  await click("Save"); await sleep(900);
  await check("new version saved from the form", (await text()).includes("v2") && (await text()).includes('"pass_score": 77'));

  // the fleet and activity views: a station that has not enrolled yet, and no activity reported
  await click("Fleet"); await sleep(600);
  await check("fleet lists the new station as not enrolled",
    (await text()).includes("Fleet") && (await text()).includes("Scan-Desk-1-A") && (await text()).includes("Not enrolled yet"));
  await check("fleet says in words how the fleet is doing", await page.$eval(".lead", (e) => e.textContent.length > 20));
  await shot("05d-fleet");
  await page.evaluate(() => [...document.querySelectorAll(".station a")].find((a) => a.textContent === "Scan-Desk-1-A").click());
  await sleep(600);
  await check("a station has its own page with settings, activity and events",
    (await text()).includes("Back to the fleet") && (await text()).includes("Recent events") && (await text()).includes("Scan-Desk-1-A"));
  await shot("05e-station");
  await click("Fleet"); await sleep(400);
  await click("Activity"); await sleep(600);
  await check("activity explains that nothing has been reported yet",
    (await text()).includes("No activity reported") && (await page.$("svg.chart")) !== null);
  await click("Documents"); await click("Errors"); await sleep(300);
  await check("the activity measure can be switched", await page.evaluate(() => document.querySelector(".seg button.on:last-child, .seg button.on") !== null));
  await shot("05e-activity");

  // documents: a station reports one, an administrator finds it by its NIC and opens it
  const PCS_NIC = "553216789V";
  const reg = await (await fetch(BASE + "/pcs/register", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ code }) })).json();
  const projectId = (await page.evaluate(() => fetch("/admin/projects").then((r) => r.json())))[0].id;
  const send = (part, body) => fetch(BASE + "/pcs/" + reg.pc_id + "/documents/doc-e2e-1/" + part,
    { method: "PUT", headers: { "content-type": "application/json", authorization: "Bearer " + reg.api_key }, body: JSON.stringify(body) });
  const sent = [await send("meta", { project_id: projectId, batch_id: "batch-1", page_count: 3, created_at: new Date().toISOString(),
      validation_status: "approved", file_name: "kamal.pdf", size: 2048, sha256: "a".repeat(64), delivered_to: ["local_folder"], delivery_status: "delivered_local" }),
    await send("index", { fields: { nic: PCS_NIC, full_name: "Kamal Perera", branch: "Galle" }, validation_status: "approved" })];
  await check("a station reported a document", sent.every((r) => r.status === 200));
  await click("Search"); await sleep(600);
  await check("search page opens", (await text()).includes("Search documents") && (await text()).includes("Kamal Perera"));
  await page.type(".searchform input[type=search]", "553 216-789v");
  await page.evaluate(() => document.querySelector(".searchform button[type=submit]").click()); await sleep(700);
  await check("found by NIC however it is typed", (await text()).includes("1 document found") && (await text()).includes("full_name: Kamal Perera"));
  await shot("05f-search");
  await click("Open"); await sleep(600);
  await check("the document opens with its fields, PDF details and history",
    (await text()).includes("Index fields") && (await text()).includes(PCS_NIC) && (await text()).includes("stays on") && (await text()).includes("Revision 1"));
  await shot("05g-document");
  const csv = await page.evaluate(() => fetch("/admin/documents/export.csv?q=553216789V").then((r) => r.text()));
  await check("the search can be exported as a spreadsheet", csv.startsWith("scanned_at,station,project") && csv.includes("Kamal Perera"));
  await click("Fleet"); await sleep(500);
  await page.evaluate(() => [...document.querySelectorAll(".station a")].find((a) => a.textContent === "Scan-Desk-1-A").click()); await sleep(600);
  await check("the station page lists the documents it sent", (await text()).includes("Documents sent to the server") && (await text()).includes("1 document reported by this station"));

  await click("Templates"); await sleep(500); await shot("06-templates");
  await click("Admins"); await click("Add admin...");
  const inputs = await page.$$("dialog input"); await inputs[0].type("auditor"); await inputs[1].type("a very long passphrase");
  await page.evaluate(() => { const i = document.querySelectorAll("dialog input")[2]; i.value = "auditor"; });
  await click("Create"); await sleep(700);
  await check("auditor account created", (await text()).includes("auditor (read-only)"));
  await click("Add admin...");
  const more = await page.$$("dialog input"); await more[0].type("finder"); await more[1].type("a very long passphrase");
  await page.evaluate(() => { document.querySelectorAll("dialog input")[2].value = "searcher"; });
  await click("Create"); await sleep(700);
  await check("searcher account created, with no projects yet", (await text()).includes("searcher: no projects"));
  await click("Projects..."); await sleep(400);
  await page.evaluate(() => { for (const box of document.querySelectorAll("dialog input[type=checkbox]")) box.checked = true; });
  await click("Save"); await sleep(700);
  await check("the searcher was given the project", (await text()).includes("searcher: 1 project"));
  await shot("07-admins");
  await click("Audit log"); await sleep(700);
  await check("audit shows the actions", (await text()).includes("pc_created") && (await text()).includes("admin_user_created"));
  await shot("08-audit");
  await click("Settings"); await sleep(500); await shot("09-settings");
  if (WEBHOOK_PORT) {
    const SECRET = "e2e-signing-secret", ADDRESS = "http://127.0.0.1:" + WEBHOOK_PORT + "/services/TOKEN-IN-THE-ADDRESS";
    const inCard = (label) => page.evaluate((l) => { const card = [...document.querySelectorAll(".card")].find((c) => c.querySelector("h2") && c.querySelector("h2").textContent === "Alert notifications");
      const b = [...card.querySelectorAll("button")].find((x) => x.textContent.trim() === l); if (b) { b.click(); return true; } return false; }, label);
    await check("the webhook card is there and nothing is set up yet", (await text()).includes("Alert notifications") && (await text()).includes("No webhook yet"));
    await page.evaluate((url, secret) => { const card = [...document.querySelectorAll(".card")].find((c) => c.querySelector("h2") && c.querySelector("h2").textContent === "Alert notifications");
      const inputs = card.querySelectorAll("input"); inputs[0].value = url; inputs[1].value = secret; inputs[2].checked = true; }, ADDRESS, SECRET);
    await inCard("Save"); await sleep(900);
    await check("saved: the page shows the host and never the address or secret",
      (await text()).includes("Sending to 127.0.0.1") && !(await text()).includes("TOKEN-IN-THE-ADDRESS") && !(await text()).includes(SECRET));
    await inCard("Send a test message"); await sleep(1200);
    const test = received.find((r) => JSON.parse(r.body).event === "test");
    await check("a test message arrived", !!test && JSON.parse(test.body).text.includes("test message"));
    await check("... with a valid signature", !!test && test.headers["x-dok-signature"] === "sha256=" + crypto.createHmac("sha256", SECRET)
      .update(test.headers["x-dok-timestamp"] + "." + test.body).digest("hex"));
    await check("the page says the last message was received", (await text()).includes("was received"));
    await shot("09b-webhook");
    await inCard("Remove the webhook"); await sleep(300);
  }
  await click("Overview"); await sleep(500);
  await check("overview counts the new PC and project", (await text()).includes("Projects"));

  await page.reload({ waitUntil: "load" }); await sleep(600);
  await check("a reload keeps the session (csrf from /admin/me)", (await text()).includes("Overview"));
  await click("Sign out"); await sleep(600);
  await check("sign out returns to the login form", await page.$eval("#login", (e) => !e.hidden));

  // the read-only account: sees the data, is offered nothing to change
  await page.type("#login-user", "auditor"); await page.type("#login-pass", "a very long passphrase");
  await click("Sign in"); await sleep(700);
  await check("auditor signed in and marked read-only", (await text()).includes("auditor (read-only)") && (await text()).includes("Overview"));
  await click("Fleet"); await sleep(500);
  await check("auditor can read the fleet", (await text()).includes("Scan-Desk-1-A"));
  await click("Projects"); await sleep(500);
  await check("auditor sees projects but no New project button", (await text()).includes("Bank customer file") && !(await text()).includes("New project..."));
  await click("PCs"); await sleep(500);
  await check("auditor sees PCs but no Add PC / Delete", !(await text()).includes("Add PC") && !(await text()).includes("Delete"));
  await click("Admins"); await sleep(500);
  await check("auditor sees admins but no Disable / Reset password", (await text()).includes("root") && !(await text()).includes("Reset password") && !(await text()).includes("Add admin..."));
  await shot("10-auditor");
  await click("Sign out"); await sleep(600);

  // the searcher: finds documents of the given project, is shown nothing else
  await page.evaluate(() => { document.getElementById("login-user").value = ""; });
  await page.type("#login-user", "finder"); await page.type("#login-pass", "a very long passphrase");
  await click("Sign in"); await sleep(800);
  await check("searcher lands on the search page and is marked search only", (await text()).includes("Search documents") && (await text()).includes("search only"));
  await check("the searcher's menu has only Search", await page.evaluate(() => [...document.querySelectorAll("#nav a")].filter((a) => !a.hidden).length === 1));
  await check("the searcher sees the document", (await text()).includes("Kamal Perera"));
  await page.evaluate(() => { location.hash = "#/fleet"; }); await sleep(600);
  await check("a typed-in address for another page shows the search instead", (await text()).includes("Search documents") && !(await text()).includes("Back to the fleet"));
  await shot("11-searcher");
  await click("Sign out"); await sleep(600);
  await browser.close();
  if (receiver) receiver.close();
  console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no browser errors");
  process.exit(problems.length ? 1 : 0);
})().catch((e) => { console.log("CRASH", e.message); process.exit(2); });
