// 前端交互自检：逐按钮点击，收集控制台错误与失败请求
const { chromium } = require("playwright-core");
const path = require("path");

const URL = "http://127.0.0.1:8765/";
const errors = [];
const failed = [];

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("console", (m) => { if (m.type() === "error") errors.push("[console] " + m.text()); });
  page.on("pageerror", (e) => errors.push("[pageerror] " + e.message));
  page.on("response", (r) => { if (r.status() >= 400) failed.push(r.status() + " " + r.url()); });

  await page.goto(URL, { waitUntil: "networkidle" });

  const probe = async (label, fn) => {
    try { await fn(); console.log("OK   " + label); }
    catch (e) { console.log("FAIL " + label + " :: " + e.message); }
  };

  // 1. 初始状态
  await probe("初始 Agent 名册", async () => {
    const n = await page.locator("#agents article").count();
    if (n !== 8) throw new Error("角色卡数量 " + n + "，期望 8");
    const st = await page.locator("#agent-job-status").textContent();
    if (!st.includes("尚未")) throw new Error("作业状态异常: " + st);
  });

  await probe("初始外发说明", async () => {
    const t = await page.locator("#egressNote").textContent();
    if (!t || !t.length) throw new Error("egressNote 为空");
    if (!t.includes("离线占位")) throw new Error("初始预设说明错误: " + t);
  });

  await probe("初始 Key 输入禁用", async () => {
    const d = await page.locator("#key").isDisabled();
    if (!d) throw new Error("mock 预设下 API Key 未禁用");
  });

  // 2. 切换预设
  await probe("切到 modelscope", async () => {
    await page.selectOption("#preset", "modelscope");
    const t = await page.locator("#egressNote").textContent();
    if (!t.includes("modelscope.cn")) throw new Error("外发说明未更新: " + t);
    if (await page.locator("#key").isDisabled()) throw new Error("外部预设 Key 应可编辑");
  });

  await probe("未勾选外发时点运行应被拦截", async () => {
    let msg = "";
    page.once("dialog", async (d) => { msg = d.message(); await d.dismiss(); });
    await page.click("#go");
    await page.waitForTimeout(400);
    if (!msg.includes("确认外发")) throw new Error("未弹出外发确认提示，实际: " + msg);
  });

  await probe("切回 mock", async () => {
    await page.selectOption("#preset", "mock");
    const t = await page.locator("#egressNote").textContent();
    if (!t.includes("离线占位")) throw new Error("切回 mock 后说明未更新: " + t);
  });

  // 3. 运行
  await probe("运行 demo01", async () => {
    await page.selectOption("#fx", "demo01");
    await page.click("#go");
    await page.waitForFunction(() => (document.getElementById("stat").textContent || "").includes("完成"), { timeout: 120000 });
    await page.waitForFunction(() => document.querySelectorAll("#calcs tr").length > 1, { timeout: 30000 });
  });

  await probe("阶段轨道全部有状态", async () => {
    const n = await page.locator("#stage-track li").count();
    if (n !== 8) throw new Error("阶段数 " + n);
    const done = await page.locator("#stage-track li.done").count();
    if (done < 8) throw new Error("已完成阶段 " + done + " / 8");
  });

  await probe("计算表显示公式名", async () => {
    const txt = await page.locator("#calcs").textContent();
    if (!/毛利率|同比|资产负债率/.test(txt)) throw new Error("公式列未见中文名，实际片段: " + txt.slice(0, 160));
    if (/FRM-[A-Z]+-\d+/.test(txt)) throw new Error("公式列仍显示 FRM 编号");
  });

  await probe("审计摘要可读", async () => {
    const txt = await page.locator("#audit").textContent();
    if (txt.includes('\\"')) throw new Error("摘要含转义引号，仍是 JSON 串");
  });

  // 4. 证据与定位
  await probe("点主张显示证据", async () => {
    await page.locator("#claims tr").nth(1).click();
    await page.waitForTimeout(400);
    const n = await page.locator("#evidence .ev").count();
    if (n < 1) throw new Error("未显示证据卡");
  });

  await probe("定位原文", async () => {
    const btn = page.locator("#evidence button", { hasText: "定位原文" }).first();
    if (!(await btn.count())) throw new Error("无定位按钮");
    await btn.click();
    await page.waitForTimeout(900);
    const pre = await page.locator("#evidence pre").first().textContent();
    if (!pre || pre.length < 10) throw new Error("原文为空: " + pre);
  });

  // 5. 复核提交
  await probe("复核提交留痕", async () => {
    const sel = page.locator("#review select").first();
    await sel.selectOption({ index: 1 });
    await page.locator("#review button").first().click();
    await page.waitForTimeout(1200);
    const t = await page.locator("#rvstat").textContent();
    if (!t.includes("已留痕")) throw new Error("留痕提示异常: " + t);
  });

  // 6. 加载已完成任务
  await probe("加载已完成任务按钮", async () => {
    await page.selectOption("#task", { index: 0 });
    await page.click("text=加载已完成任务");
    await page.waitForTimeout(1500);
    const n = await page.locator("#claims tr").count();
    if (n < 2) throw new Error("加载后主张表行数 " + n);
  });

  // 6b. 上传流程与运行目标提示
  const MAT = path.join(__dirname, "..", "fixtures", "demo01", "materials");
  await probe("运行目标初始提示", async () => {
    const t = await page.locator("#runhint").textContent();
    if (!t.includes("demo01")) throw new Error("初始运行目标提示异常: " + t);
  });

  await probe("上传后切换运行目标并给出 mock 警告", async () => {
    await page.setInputFiles("#files", [
      path.join(MAT, "MAT-0001-研报.md"),
      path.join(MAT, "MAT-0002-年报.md"),
    ]);
    await page.selectOption("#report", "MAT-0001-研报.md");
    await page.click("#up");
    await page.waitForFunction(() => (document.getElementById("stat").textContent || "").includes("已登记"), { timeout: 30000 });
    const hint = await page.locator("#runhint").textContent();
    if (!hint.includes("上传材料")) throw new Error("运行目标未切到上传材料: " + hint);
    if (await page.locator("#mockWarn").isHidden()) throw new Error("mock 警告未显示");
  });

  await probe("mock 下点运行应拦截上传材料", async () => {
    let msg = "";
    page.once("dialog", async (d) => { msg = d.message(); await d.dismiss(); });
    await page.click("#go");
    await page.waitForTimeout(400);
    if (!msg.includes("没有金标准")) throw new Error("未拦截 mock 上传运行: " + msg);
  });

  await probe("改选夹具后清空上传目标", async () => {
    await page.selectOption("#fx", "demo01_pdf");
    const hint = await page.locator("#runhint").textContent();
    if (hint.includes("上传材料")) throw new Error("切换夹具后仍指向上传材料");
    if (!hint.includes("demo01_pdf")) throw new Error("提示未跟随夹具: " + hint);
  });

  await probe("mock 警告随预设切换隐藏", async () => {
    await page.selectOption("#preset", "modelscope");
    if (!(await page.locator("#mockWarn").isHidden())) throw new Error("切到外部预设后警告仍显示");
    await page.selectOption("#preset", "mock");
  });

  // 7. 下载链接
  await probe("交付下载全部可访问", async () => {
    const hrefs = await page.locator("#dl a").evaluateAll((as) => as.map((a) => a.href));
    if (!hrefs.length) throw new Error("无下载链接");
    for (const h of hrefs) {
      const r = await page.request.get(h);
      if (!r.ok()) throw new Error("下载失败 " + r.status() + " " + h);
    }
    console.log("     下载链接 " + hrefs.length + " 个全部 200");
  });

  await browser.close();
  console.log("\n=== 控制台错误 " + errors.length + " 条 ===");
  errors.forEach((e) => console.log("  " + e));
  console.log("=== HTTP 失败 " + failed.length + " 条 ===");
  failed.forEach((e) => console.log("  " + e));
})().catch((e) => { console.error("FATAL", e && e.message); process.exit(1); });
