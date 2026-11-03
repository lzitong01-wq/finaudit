// 刷新恢复与失效作业处理自检
const { chromium } = require("playwright-core");
const path = require("path");

const URL = "http://127.0.0.1:8765/";
const MAT = path.join(__dirname, "..", "fixtures", "demo01", "materials");
const errors = [];

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (e) => errors.push("[pageerror] " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("[console] " + m.text()); });

  await page.goto(URL, { waitUntil: "networkidle" });

  console.log("1) 启动作业后立即刷新");
  await page.selectOption("#fx", "demo01");
  await page.selectOption("#preset", "mock");
  await page.click("#go");
  await page.waitForTimeout(250);
  const saved = await page.evaluate(() => sessionStorage.getItem("finaudit_job"));
  console.log("   sessionStorage 已记录作业:", saved || "（空）");

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("stat").textContent || "").includes("完成"),
    { timeout: 60000 }
  );
  const stat = await page.locator("#stat").textContent();
  const claims = await page.locator("#claims tr").count();
  const calcs = await page.locator("#calcs tr").count();
  console.log("   刷新后状态:", stat.trim(), "| 主张行:", claims, "| 计算行:", calcs);
  if (claims < 2) throw new Error("刷新后未恢复结果");
  if (calcs < 2) throw new Error("刷新后 D 区未恢复");

  console.log("2) 伪造失效作业应停止轮询");
  await page.evaluate(() => sessionStorage.setItem("finaudit_job", "Jdeadbeef"));
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("stat").textContent || "").includes("失效"),
    { timeout: 15000 }
  );
  console.log("   提示:", (await page.locator("#stat").textContent()).trim());
  const btn = page.locator("#go");
  if (await btn.isDisabled()) throw new Error("失效后运行按钮未恢复可用");
  const key = await page.evaluate(() => sessionStorage.getItem("finaudit_job"));
  if (key) throw new Error("失效后未清除 sessionStorage");

  console.log("3) 上传成功后应清空文件选择");
  await page.setInputFiles("#files", [
    path.join(MAT, "MAT-0001-研报.md"),
    path.join(MAT, "MAT-0002-年报.md"),
  ]);
  await page.selectOption("#report", "MAT-0001-研报.md");
  await page.click("#up");
  await page.waitForFunction(
    () => (document.getElementById("stat").textContent || "").includes("已登记"),
    { timeout: 30000 }
  );
  const fileCount = await page.evaluate(() => document.getElementById("files").files.length);
  const repOpt = await page.evaluate(() => document.getElementById("report").options.length);
  console.log("   剩余文件:", fileCount, "| 研报下拉项:", repOpt);
  if (fileCount !== 0) throw new Error("上传后未清空文件选择");
  if (repOpt !== 1) throw new Error("上传后研报下拉未重置");

  await browser.close();
  console.log("\n控制台错误:", errors.length);
  errors.forEach((e) => console.log("  " + e));
  console.log("全部通过");
})().catch((e) => { console.error("FAILED:", e.message); process.exit(1); });
