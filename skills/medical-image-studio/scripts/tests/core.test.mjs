import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  buildProviderRequest,
  executeGeneration,
  extractAssets,
  saveAssets,
} from "../lib/providers.mjs";
import {
  assessCompleteness,
  buildVisualPrompt,
  flattenPages,
  loadProject,
  loadRegistries,
  resolveRenderMode,
} from "../lib/project.mjs";

const SKILL_ROOT = fileURLToPath(new URL("../../", import.meta.url));

test("课堂示例可推断系列模式且所有页面统一走 API", async () => {
  const project = await loadProject(path.join(SKILL_ROOT, "assets/examples/fever-education-series.json"));
  const registries = await loadRegistries();
  const pages = flattenPages(project);

  assert.equal(project.mode, "series");
  assert.deepEqual(assessCompleteness(project), { score: 100, level: "ready", missing: [], questions: [] });
  assert.equal(pages.length, 3);
  assert.equal(resolveRenderMode(project, pages[0].page, registries.recipes.hook), "api");
  assert.ok(pages.every(({ page }) => !("imagePath" in page)));
});

test("项目提示词包含逐字中文并明确不做 HTML 合成", async () => {
  const project = await loadProject(path.join(SKILL_ROOT, "assets/examples/fever-education-series.json"));
  const registries = await loadRegistries();
  const { deliverable, page } = flattenPages(project)[1];
  const prompt = buildVisualPrompt(project, deliverable, page, registries.styles[project.visualMaster.style], registries.recipes[page.role]);

  assert.match(prompt, /不进行HTML或其他后期合成/);
  assert.match(prompt, /主标题必须逐字显示：“一个数字，不等于全部风险”/);
  assert.match(prompt, /要点1必须逐字显示：“只反复量体温，忽略整体状态”/);
  assert.match(prompt, /除指定文字外，不添加水印、无关英文或额外文案/);
});

test("HiAPI 请求使用异步任务契约", () => {
  const request = buildProviderRequest({
    provider: "hiapi",
    baseUrl: "https://api.hiapi.ai",
    model: "gpt-image-2/text-to-image",
  }, {
    prompt: "无字医疗主视觉",
    aspectRatio: "3:4",
    resolution: "2K",
  });

  assert.equal(request.url, "https://api.hiapi.ai/v1/tasks");
  assert.deepEqual(request.body.input, {
    prompt: "无字医疗主视觉",
    aspect_ratio: "3:4",
    resolution: "2K",
  });
});

test("老张 VIP 路由带尺寸和质量参数", () => {
  const request = buildProviderRequest({
    provider: "laozhang",
    baseUrl: "https://api.laozhang.ai/v1",
    model: "gpt-image-2-vip",
    supportsControls: true,
  }, {
    prompt: "无字医疗主视觉",
    aspectRatio: "3:4",
    resolution: "2K",
    quality: "high",
  });

  assert.equal(request.url, "https://api.laozhang.ai/v1/images/generations");
  assert.equal(request.body.size, "1536x2048");
  assert.equal(request.body.quality, "high");
});

test("HiAPI 创建与轮询可在 mock 响应中完成", async () => {
  const calls = [];
  const responses = [
    jsonResponse({ data: { taskId: "task-demo" } }),
    jsonResponse({ data: { status: "completed", output: ["https://example.test/result.png"] } }),
  ];
  const fetchMock = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || "GET" });
    return responses.shift();
  };

  const result = await executeGeneration({
    provider: "hiapi",
    apiKey: "test-only",
    baseUrl: "https://api.hiapi.ai",
    model: "gpt-image-2/text-to-image",
  }, {
    prompt: "无字医疗主视觉",
    aspectRatio: "3:4",
    resolution: "2K",
    pollMs: 1,
    timeoutMs: 1000,
  }, fetchMock);

  assert.equal(result.taskId, "task-demo");
  assert.deepEqual(result.assets, [{ kind: "url", value: "https://example.test/result.png" }]);
  assert.deepEqual(calls.map((item) => item.method), ["POST", "GET"]);
});

test("base64 结果保存为稳定文件名", async () => {
  const tempDir = await mkdtemp(path.join(os.tmpdir(), "medical-image-provider-test-"));
  try {
    const assets = extractAssets({ data: [{ b64_json: Buffer.from("mock-image").toString("base64") }] });
    const saved = await saveAssets(assets, {
      outputDir: tempDir,
      prompt: "测试",
      fileStem: "xhs-01-raw",
    });
    assert.equal(path.basename(saved[0]), "xhs-01-raw-1.png");
    assert.equal((await readFile(saved[0])).toString(), "mock-image");
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
});

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() { return JSON.stringify(body); },
  };
}
