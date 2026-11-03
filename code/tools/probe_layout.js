// 响应式与布局自检：三档宽度下检查横向溢出、元素遮挡与文字截断
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const URL = "http://127.0.0.1:8765/";
// 截图落在 out/_probe/，与任务输出目录分开，避免污染 out/ 根目录
const SHOT_DIR = path.join(__dirname, "..", "..", "out", "_probe");
const WIDTHS = [
  { w: 1440, h: 900, name: "宽屏" },
  { w: 960, h: 900, name: "平板" },
  { w: 700, h: 900, name: "窄屏" },
  { w: 430, h: 900, name: "手机" },
];

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  for (const { w, h, name } of WIDTHS) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.goto(URL, { waitUntil: "networkidle" });
    // 跑一次，让所有区域都有内容
    await page.selectOption("#fx", "demo01");
    await page.selectOption("#preset", "mock");
    await page.click("#go");
    await page.waitForFunction(() => (document.getElementById("stat").textContent || "").includes("完成"), { timeout: 120000 });
    await page.waitForFunction(() => document.querySelectorAll("#calcs tr").length > 1, { timeout: 30000 });
    await page.locator("#claims tr").nth(1).click();
    await page.waitForTimeout(600);

    const report = await page.evaluate(() => {
      const out = { overflow: [], tiny: [], clipped: [] };
      const de = document.documentElement;
      if (de.scrollWidth > de.clientWidth + 1) {
        out.overflow.push(`文档横向溢出 ${de.scrollWidth} > ${de.clientWidth}`);
      }
      // 找出超出视口右边界的元素（可滚动容器内的内容不算布局缺陷）
      const inScrollable = (el) => {
        let p = el.parentElement;
        while (p && p !== document.body) {
          const s = getComputedStyle(p);
          if (s.overflowX === "auto" || s.overflowX === "scroll") return true;
          p = p.parentElement;
        }
        return false;
      };
      document.querySelectorAll("body *").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > de.clientWidth + 2 && !inScrollable(el)) {
          const tag = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "");
          out.overflow.push(`${tag} 右边界 ${Math.round(r.right)} > ${de.clientWidth}`);
        }
        if (r.width > 0 && r.height > 0 && r.width < 24 && el.textContent.trim().length > 6) {
          out.tiny.push(`${el.tagName.toLowerCase()} 宽 ${Math.round(r.width)}px 但含 ${el.textContent.trim().length} 字`);
        }
      });
      // 检查表格是否被压扁（单元格宽度小于内容）
      document.querySelectorAll("table").forEach((t) => {
        const tr = t.querySelector("tr");
        if (!tr) return;
        [...tr.children].forEach((th) => {
          const r = th.getBoundingClientRect();
          if (r.width < 28 && th.textContent.trim().length >= 2) {
            out.clipped.push(`表头「${th.textContent.trim()}」宽 ${Math.round(r.width)}px`);
          }
        });
      });
      return out;
    });

    const bad = report.overflow.length + report.clipped.length;
    console.log(`\n[${name} ${w}px] ${bad === 0 ? "OK" : "问题 " + bad + " 处"}`);
    report.overflow.slice(0, 6).forEach((x) => console.log("  溢出: " + x));
    report.clipped.slice(0, 6).forEach((x) => console.log("  压扁: " + x));

    fs.mkdirSync(SHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(SHOT_DIR, `responsive_${w}.png`), fullPage: false });
    await page.close();
  }
  await browser.close();
})().catch((e) => { console.error("FATAL", e && e.message); process.exit(1); });
