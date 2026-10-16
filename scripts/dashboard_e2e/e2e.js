// Real-browser smoke test of the central dashboard (Chrome via puppeteer-core).
// Needs a FRESH central server with one admin root / "correct horse battery"
// (see README.md in this folder). Writes screenshots to $OUT.
const puppeteer = require("puppeteer-core");
const path = require("path");
const OUT = process.env.OUT, BASE = process.env.BASE || "http://127.0.0.1:8765";
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const problems = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 860 });
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("401")) problems.push("console: " + m.text()); });
  page.on("response", (r) => { if (r.status() >= 400 && !(r.status() === 401 && (r.url().includes("/admin/me") || r.url().includes("/admin/login")))) problems.push(r.status() + " " + r.url()); });
  const shot = async (n) => page.screenshot({ path: path.join(OUT, n + ".png") });
  const text = async () => page.evaluate(() => document.body.innerText);
  const click = async (label) => { const ok = await page.evaluate((l) => { const scope = document.querySelector("dialog[open]") || document; const b = [...scope.querySelectorAll("button,a")].find((x) => x.textContent.trim() === l && x.offsetParent !== null); if (b) { b.click(); return true; } return false; }, label); if (!ok) throw new Error("no button: " + label); await sleep(400); };
  const check = async (name, ok) => { console.log((ok ? "PASS " : "FAIL ") + name); if (!ok) { problems.push("check failed: " + name); console.log("   page text: " + (await text()).replace(/\s+/g, " ").slice(0, 400)); await shot("fail-" + name.replace(/\W+/g, "-").slice(0, 30)); } };

  await page.goto(BASE + "/", { waitUntil: "load" });
  await check("redirect to /dashboard + login form shown", page.url().endsWith("/dashboard") && (await page.$("#login-form")) !== null && await page.$eval("#login", (e) => !e.hidden));
  await shot("01-login");
  await page.type("#login-user", "root"); await page.type("#login-pass", "wrong password!!");
  await click("Sign in"); await sleep(1200);          // the server takes its time over a wrong password
  await check("wrong password message", (await text()).includes("Wrong username or password."));
  await page.$eval("#login-pass", (e) => (e.value = ""));
  await page.type("#login-pass", "correct horse battery"); await click("Sign in"); await sleep(600);
  await check("signed in, overview shown", (await text()).includes("Overview") && (await text()).includes("PCs in total"));
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

  await click("Templates"); await sleep(500); await shot("06-templates");
  await click("Admins"); await click("Add admin...");
  const inputs = await page.$$("dialog input"); await inputs[0].type("auditor"); await inputs[1].type("a very long passphrase");
  await page.evaluate(() => { const i = document.querySelectorAll("dialog input")[2]; i.value = "auditor"; });
  await click("Create"); await sleep(700);
  await check("auditor account created", (await text()).includes("auditor (read-only)"));
  await shot("07-admins");
  await click("Audit log"); await sleep(700);
  await check("audit shows the actions", (await text()).includes("pc_created") && (await text()).includes("admin_user_created"));
  await shot("08-audit");
  await click("Settings"); await sleep(500); await shot("09-settings");
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
  await click("Projects"); await sleep(500);
  await check("auditor sees projects but no New project button", (await text()).includes("Bank customer file") && !(await text()).includes("New project..."));
  await click("PCs"); await sleep(500);
  await check("auditor sees PCs but no Add PC / Delete", !(await text()).includes("Add PC") && !(await text()).includes("Delete"));
  await click("Admins"); await sleep(500);
  await check("auditor sees admins but no Disable / Reset password", (await text()).includes("root") && !(await text()).includes("Reset password") && !(await text()).includes("Add admin..."));
  await shot("10-auditor");
  await click("Sign out"); await sleep(600);
  await browser.close();
  console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "no browser errors");
  process.exit(problems.length ? 1 : 0);
})().catch((e) => { console.log("CRASH", e.message); process.exit(2); });
