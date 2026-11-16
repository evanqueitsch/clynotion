import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const root = process.env.JSDOM_ROOT || "/tmp/jsdom-run";
const require = createRequire(root + "/package.json");
const { JSDOM } = require("jsdom");

const htmlUrl = new URL("./hiring_form_resubmit.html", import.meta.url);
const html = readFileSync(htmlUrl, "utf8");
const dom = new JSDOM(html, {
  url: htmlUrl.href,
  runScripts: "dangerously",
  resources: "usable",
});

const title = await new Promise((resolve, reject) => {
  const started = Date.now();
  const timer = setInterval(() => {
    const current = dom.window.document.title || "";
    if (current.startsWith("PASS") || current.startsWith("FAIL")) {
      clearInterval(timer);
      resolve(current);
    } else if (Date.now() - started > 5000) {
      clearInterval(timer);
      const status = dom.window.document.getElementById("hiring-status");
      reject(new Error("timeout title=" + current + " status=" + (status ? status.textContent : "")));
    }
  }, 20);
});

if (!title.startsWith("PASS")) {
  throw new Error(title);
}
console.log(title);
