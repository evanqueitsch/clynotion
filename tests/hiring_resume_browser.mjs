import { createRequire } from "node:module";

const root = process.env.PLAYWRIGHT_ROOT || "/tmp/pw-run";
const require = createRequire(root + "/package.json");
const { chromium } = require("playwright");
const base = process.env.APP_BASE || "http://127.0.0.1:8024";

const resume = [
  "Jordan Example",
  "Full legal name: Jordan Example",
  "First name: Jordan",
  "Personal email: jordan.example@example.com",
  "Credentials: MA, LPC",
  "Position: Outpatient Clinician",
].join("\n");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(err.message));
let release;
let held = false;
await page.route("**/hiring/resumes", async (route) => {
  held = true;
  await new Promise((resolve) => {
    release = resolve;
  });
  await route.continue();
});

function fail(msg) {
  throw new Error(msg + " pageerror=" + pageErrors.join(" | "));
}

await page.goto(base + "/", { waitUntil: "domcontentloaded" });
await page.click("#btn-login");
await page.waitForSelector("#home-panel:not(.hidden)");
const openHiring = page.getByRole("button", { name: "Open Hiring", exact: true });
await openHiring.click();
if (!(await page.locator("#hire-form").count())) await openHiring.click();
await page.waitForSelector("#hire-resume");

await page.fill("#hire-candidate_full_name", "Already Typed");
await page.setInputFiles("#hire-resume", {
  name: "jordan.txt",
  mimeType: "text/plain",
  buffer: Buffer.from(resume),
});
await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent.includes("Reading resume"));
if (!held) fail("upload did not start");
release();
await page.waitForFunction(() => (document.getElementById("hire-personal_email") || {}).value === "jordan.example@example.com");
if ((await page.locator("#hire-candidate_full_name").inputValue()) !== "Already Typed") fail("typed name was overwritten");
if ((await page.locator("#hire-candidate_first_name").inputValue()) !== "Jordan") fail("first name was not filled");
if (!(await page.locator(".resume-note").first().innerText()).includes("Filled from resume, please check.")) {
  fail("prefill was not marked");
}
await page.fill("#hire-candidate_first_name", "Jordan Edited");
await page.fill("#hire-hourly_rate", "40");
await page.fill("#hire-caseload_range", "25-30");
await page.fill("#hire-qsehra_monthly_amount", "100");
await page.fill("#hire-qsehra_wait_days", "60");
await page.fill("#hire-start_date", "01/05/2027");
await page.click("#hire-form button[type=submit]");
await page.waitForFunction(() => (document.getElementById("hiring-status") || {}).textContent === "Saved.");
if ((await page.locator(".due-row").count()) !== 1) fail("offer was not listed");
if ((await page.locator("#hire-candidate_first_name").inputValue()) !== "Jordan Edited") fail("edit was not saved");
if ((await page.locator("#hire-candidate_full_name").inputValue()) !== "Already Typed") fail("name changed after save");
const resumeButton = page.getByRole("button", { name: "Resume", exact: true });
if (!(await resumeButton.count())) fail("resume download is missing");
if (!(await page.locator("body").innerText()).includes("Drive not connected")) fail("drive status missing");
if (pageErrors.length) fail("page error");
console.log("PASS");
await browser.close();
