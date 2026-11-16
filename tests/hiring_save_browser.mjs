import { createRequire } from "node:module";

const root = process.env.PLAYWRIGHT_ROOT || "/tmp/pw-run";
const require = createRequire(root + "/package.json");
const { chromium } = require("playwright");
const base = process.env.APP_BASE || "http://127.0.0.1:8024";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const pageErrors = [];
const writes = [];
page.on("pageerror", (err) => pageErrors.push(err.message));
page.on("request", (req) => {
  const url = req.url();
  if ((req.method() === "POST" || req.method() === "PUT") && /\/hiring\/offers(\/[^/]+)?$/.test(url)) {
    writes.push(req.method() + " " + url.replace(base, ""));
  }
});

let hold = null;
let heldPosts = 0;
await page.route("**/hiring/offers", async (route) => {
  if (hold && route.request().method() === "POST") {
    heldPosts += 1;
    await hold;
  }
  await route.continue();
});

function fail(msg) {
  throw new Error(msg + " writes=" + JSON.stringify(writes) + " pageerror=" + pageErrors.join(" | "));
}

await page.goto(base + "/", { waitUntil: "domcontentloaded" });
await page.click("#btn-login");
await page.waitForSelector("#home-panel:not(.hidden)");
const openHiring = page.getByRole("button", { name: "Open Hiring", exact: true });
await openHiring.click();
if (!(await page.locator("#hire-form").count())) await openHiring.click();
await page.waitForSelector("#hire-form");

const email = "qa.resave." + Date.now() + "@example.com";
await page.fill("#hire-candidate_full_name", "Resave Candidate");
await page.fill("#hire-candidate_first_name", "Resave");
await page.fill("#hire-personal_email", email);
await page.fill("#hire-hourly_rate", "forty");
await page.fill("#hire-caseload_range", "25-30");
await page.fill("#hire-qsehra_monthly_amount", "100");
await page.fill("#hire-qsehra_wait_days", "60");
await page.fill("#hire-start_date", "01/05/2027");
await page.click("#hire-form button[type=submit]");
await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent.includes("Hourly rate must be a dollar amount."));
if (!(await page.locator("#hire-hourly_rate").evaluate((el) => el.classList.contains("field-error")))) {
  fail("hourly field was not marked");
}

let releaseHold;
hold = new Promise((resolve) => {
  releaseHold = resolve;
});
await page.evaluate(() => {
  const input = document.getElementById("hire-hourly_rate");
  input.focus();
  input.value = "40";
  const form = document.getElementById("hire-form");
  form.requestSubmit();
  try {
    form.requestSubmit();
  } catch (ignore) {
    /* A disabled submit button rejects the second request. */
  }
});
const started = Date.now();
while (heldPosts < 1 && Date.now() - started < 5000) {
  await new Promise((resolve) => setTimeout(resolve, 20));
}
await new Promise((resolve) => setTimeout(resolve, 250));
if (heldPosts !== 1) fail("double submit reached the server " + heldPosts + " times");
await page.waitForFunction(() => {
  const status = document.getElementById("hiring-status");
  return status && status.textContent === "Saving…";
});
const marked = await page.locator("#hire-hourly_rate.field-error").count();
if (marked) fail("field error was still marked while the save was in flight");
releaseHold();
hold = null;

await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent === "Saved.");
const editing = await page.locator("#hire-editing").inputValue();
if (!editing) fail("form was not switched to the saved offer");
if ((await page.locator("#hire-hourly_rate").inputValue()) !== "$40.00") fail("hourly rate was not kept as $40.00");
if ((await page.locator("#hire-qsehra_monthly_amount").inputValue()) !== "$100") fail("qsehra was not normalized");
if ((await page.locator(".due-row").count()) !== 1) fail("list did not show one offer");
if (!(await page.locator(".due-title").innerText()).includes("Resave Candidate")) fail("list row has the wrong name");

await page.click("#hire-form button[type=submit]");
await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent === "Saved.");
if ((await page.locator(".due-row").count()) !== 1) fail("second save changed the row count");
if ((await page.locator("#hire-editing").inputValue()) !== editing) fail("second save left the form");
const put = writes.filter((line) => line.startsWith("PUT /hiring/offers/" + editing));
if (put.length !== 1) fail("second save was not an update");
if (pageErrors.length) fail("page error");
console.log("PASS");
await browser.close();
