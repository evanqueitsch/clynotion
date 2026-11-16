import { createRequire } from "node:module";

const root = process.env.PLAYWRIGHT_ROOT || "/tmp/pw-run";
const require = createRequire(root + "/package.json");
const { chromium } = require("playwright");
const base = process.env.APP_BASE || "http://127.0.0.1:8024";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const pageErrors = [];
const deletes = [];
const answers = [];
page.on("pageerror", (err) => pageErrors.push(err.message));
page.on("request", (req) => {
  if (req.method() === "POST" && req.url().includes("/delete")) deletes.push(req.url());
});
page.on("dialog", async (dialog) => {
  const next = answers.shift();
  if (next == null || next === "dismiss") await dialog.dismiss();
  else await dialog.accept(next);
});

function fail(msg) {
  throw new Error(msg + " deletes=" + deletes.length + " pageerror=" + pageErrors.join(" | "));
}

await page.goto(base + "/", { waitUntil: "domcontentloaded" });
await page.click("#btn-login");
await page.waitForSelector("#home-panel:not(.hidden)");
const openHiring = page.getByRole("button", { name: "Open Hiring", exact: true });
await openHiring.click();
if (!(await page.locator("#hire-form").count())) await openHiring.click();
await page.waitForSelector("#hire-certs");

await page.fill("#hire-candidate_full_name", "Jordan Example");
await page.fill("#hire-candidate_first_name", "Jordan");
await page.fill("#hire-certs", "MYCRED");
await page.fill("#hire-personal_email", "jordan.browser@example.com");
await page.fill("#hire-position_title", "Staff Clinician");
await page.fill("#hire-supervisor_name", "Alex Supervisor");
await page.fill("#hire-hourly_rate", "40");
await page.fill("#hire-licensed_rate", "55");
await page.fill("#hire-caseload_range", "25-30");
await page.fill("#hire-qsehra_monthly_amount", "100");
await page.fill("#hire-qsehra_wait_days", "60");
await page.fill("#hire-start_date", "01/05/2027");
await page.locator("#hire-client").uncheck();
await page.click("#hire-form button[type=submit]");
await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent === "Saved.");
if ((await page.locator("#hire-certs").inputValue()) !== "MYCRED") fail("credentials were not kept");
if ((await page.locator("#hire-position_title").inputValue()) !== "Staff Clinician") fail("position was not kept");
if ((await page.locator("#hire-licensed_rate").inputValue()) !== "$55.00") fail("licensed rate was not kept");
if (await page.locator("#hire-client").isChecked()) fail("direct client access was checked again");
if ((await page.locator(".due-row").count()) !== 1) fail("offer was not listed");

const remove = page.getByRole("button", { name: "Delete", exact: true });
await remove.click();
await page.waitForSelector("#hire-dialog");
if (!(await page.locator("#hire-dialog").innerText()).includes("Jordan Example")) fail("dialog did not name the candidate");
await page.getByRole("button", { name: "Cancel" }).click();
await page.waitForTimeout(200);
if (deletes.length !== 0) fail("cancel sent a delete");
if ((await page.locator(".due-row").count()) !== 1) fail("cancel removed the offer");

await remove.click();
await page.locator("#hire-dialog input").fill("   ");
await page.locator("#hire-dialog button.primary").click();
await page.waitForTimeout(200);
if (deletes.length !== 0) fail("empty name sent a delete");
if ((await page.locator(".due-row").count()) !== 1) fail("empty name removed the offer");

await remove.click();
await page.locator("#hire-dialog input").fill("Wrong Name");
await page.locator("#hire-dialog button.primary").click();
await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent.includes("name"));
if (deletes.length !== 1) fail("wrong name did not reach the server");
if ((await page.locator(".due-row").count()) !== 1) fail("wrong name removed the offer");

await remove.click();
await page.locator("#hire-dialog input").fill("Jordan Example");
await page.locator("#hire-dialog button.primary").click();
await page.waitForFunction(() => document.querySelectorAll(".due-row").length === 0);
if (deletes.length !== 2) fail("exact name was not sent");
if (pageErrors.length) fail("page error");
console.log("PASS");
await browser.close();
