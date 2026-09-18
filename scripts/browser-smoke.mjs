import puppeteer from "puppeteer-core";

const base = process.env.SMOKE_BASE || "http://127.0.0.1:4173";
const chrome = process.env.CHROME_PATH || "/usr/bin/google-chrome";
const routes = ["/", "/about/", "/services/", "/industries/", "/insights/"];
const problems = [];

const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
});

try {
  for (const route of routes) {
    const page = await browser.newPage();
    const routeProblems = [];

    page.on("pageerror", error => {
      routeProblems.push(`pageerror: ${error.stack || error.message}`);
    });

    page.on("console", msg => {
      if (msg.type() === "error") {
        routeProblems.push(`console: ${msg.text()}`);
      }
    });

    const isRequiredLocalResource = url => {
      try {
        const pathname = new URL(url).pathname;
        return (
          pathname.startsWith("/assets/") ||
          pathname.startsWith("/images/") ||
          pathname.startsWith("/fonts/") ||
          pathname.startsWith("/videos/") ||
          /\\.(?:js|mjs|css|json|wasm|webp|png|jpe?g|gif|svg|mp4|webm|woff2?|ttf|otf|glb|gltf|hdr)$/i.test(pathname)
        );
      } catch {
        return false;
      }
    };

    page.on("requestfailed", request => {
      const url = request.url();
      if (url.startsWith(base) && isRequiredLocalResource(url)) {
        routeProblems.push(
          `requestfailed: ${request.failure()?.errorText || "unknown"} ${url}`
        );
      }
    });

    page.on("response", response => {
      const url = response.url();
      if (
        url.startsWith(base) &&
        response.status() >= 400 &&
        isRequiredLocalResource(url)
      ) {
        routeProblems.push(`http ${response.status()}: ${url}`);
      }
    });

    await page.goto(base + route, {
      waitUntil: "networkidle2",
      timeout: 45000
    });

    await new Promise(resolve => setTimeout(resolve, 4000));

    const state = await page.evaluate(() => ({
      title: document.title,
      main: Boolean(document.querySelector("main, .main, .main-inner, [data-barba-namespace]")),
      bodyTextLength: (document.body?.innerText || "").trim().length,
      initHidden: document.querySelectorAll("[data-init-hidden]").length,
      moduleScripts: [...document.querySelectorAll('script[type="module"][src]')].map(s => s.getAttribute("src")),
      styleSheets: document.styleSheets.length,
      readyState: document.readyState,
      visibleInitHidden: [...document.querySelectorAll("[data-init-hidden]")].filter(el => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
      }).length
    }));

    console.log(JSON.stringify({ route, state, problems: routeProblems }, null, 2));

    if (!state.main) routeProblems.push("core page container missing");
    if (state.bodyTextLength < 100) routeProblems.push("page body is unexpectedly empty");
    if (state.styleSheets < 1) routeProblems.push("no stylesheet loaded");
    if (state.readyState !== "complete") routeProblems.push("document did not finish loading");

    if (routeProblems.length) {
      problems.push({ route, problems: [...new Set(routeProblems)] });
    }

    await page.close();
  }
} finally {
  await browser.close();
}

if (problems.length) {
  console.error("Browser smoke test failed:");
  console.error(JSON.stringify(problems, null, 2));
  process.exit(1);
}

console.log("Browser smoke test passed.");
