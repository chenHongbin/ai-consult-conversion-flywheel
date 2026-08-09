#!/usr/bin/env node
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import {
  buildProviderRequest,
  executeGeneration,
  resolveConfig,
  saveAssets,
} from "./lib/providers.mjs";
import {
  assessCompleteness,
  buildVisualPrompt,
  flattenPages,
  loadProject,
  loadRegistries,
} from "./lib/project.mjs";
import { runProjectQA } from "./lib/qa.mjs";

function parseArgs(argv) {
  const args = {};
  const valueFlags = new Set(["project", "output", "provider", "model", "resolution", "quality"]);
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--all") args.all = true;
    else if (token === "--pilot") args.pilot = true;
    else if (token === "--inspect") args.inspect = true;
    else if (token === "--dry-run") args.dryRun = true;
    else if (token === "--assume") args.assume = true;
    else if (token === "--help" || token === "-h") args.help = true;
    else if (token.startsWith("--")) {
      const name = token.slice(2);
      if (!valueFlags.has(name)) throw new Error(`无法识别的参数：${token}`);
      const value = argv[++index];
      if (!value || value.startsWith("--")) throw new Error(`${token} 缺少值。`);
      args[name] = value;
    } else throw new Error(`无法识别的参数：${token}`);
  }
  return args;
}

function printHelp() {
  console.log(`医疗内容生图 Skill｜API 统一版

用法：
  node scripts/run.mjs --project project.json --inspect
  node scripts/run.mjs --project project.json --pilot --dry-run
  node scripts/run.mjs --project project.json --pilot
  node scripts/run.mjs --project project.json --all

参数：
  --project <file>      project.json（必填）
  --inspect             只检查信息完整度和页面路由
  --dry-run             编译 API 请求但不联网、不扣费
  --pilot               多页项目只调用 API 生成第一张（默认）
  --all                 逐页调用 API 生成全部页面和平台版本
  --assume              信息不完整时按 project.json 中的安全假设继续
  --output <dir>        输出根目录，默认 ./医疗生图输出
  --provider <name>     临时覆盖 hiapi / laozhang
  --model <id>          临时覆盖模型
  --resolution <tier>   API 生图清晰度，默认 2K
  --quality <tier>      老张质量档，默认 high
`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return printHelp();
  if (!args.project) throw new Error("缺少 --project。先由 Agent 按 assets/project-template.json 创建项目文件。 ");

  const [project, registries] = await Promise.all([loadProject(args.project), loadRegistries()]);
  const completeness = assessCompleteness(project);
  const routing = buildRoutingSummary(project, registries);

  if (args.inspect) {
    console.log(JSON.stringify({ projectId: project.projectId, mode: project.mode, completeness, routing }, null, 2));
    return;
  }
  if (completeness.level === "guided-brief" && !args.assume) {
    throw new Error(`项目信息不足（${completeness.score}分）。请先补充：${completeness.questions.join("；")}。如要由 Agent 判断，使用 --assume。`);
  }

  const style = registries.styles[project.visualMaster.style];
  if (!style) throw new Error(`未知医疗视觉风格：${project.visualMaster.style}`);

  const root = path.resolve(args.output || "医疗生图输出", project.projectId);
  const imagesDir = path.join(root, "images");
  await Promise.all([mkdir(root, { recursive: true }), mkdir(imagesDir, { recursive: true })]);
  await writeFile(path.join(root, "project.normalized.json"), `${JSON.stringify(stripInternal(project), null, 2)}\n`, "utf8");

  const rows = flattenPages(project);
  const selected = args.all ? rows : rows.slice(0, 1);
  const resolved = await resolveConfig({
    provider: args.provider,
    model: args.model,
    allowMissing: Boolean(args.dryRun),
  });
  const apiConfig = resolved.provider ? resolved : {
    ...resolved,
    provider: "hiapi",
    model: "gpt-image-2/text-to-image",
    baseUrl: "https://api.hiapi.ai",
    supportsControls: true,
  };
  const outputs = [];

  for (const { deliverable, page } of selected) {
    const recipe = registries.recipes[page.role];
    if (!recipe) throw new Error(`未知页面角色：${page.role}`);

    const outputId = `${deliverable.platform}-${page.id}`;
    const prompt = buildVisualPrompt(project, deliverable, page, style, recipe);
    const generationOptions = {
      prompt,
      aspectRatio: deliverable.ratio,
      resolution: args.resolution || "2K",
      quality: args.quality || "high",
    };
    const request = buildProviderRequest(apiConfig, generationOptions);

    if (args.dryRun) {
      outputs.push(outputRecord({ outputId, deliverable, page, apiConfig, prompt, request }));
      continue;
    }

    const generation = await executeGeneration(apiConfig, generationOptions);
    const saved = await saveAssets(generation.assets, {
      outputDir: imagesDir,
      prompt,
      fileStem: outputId,
    });
    outputs.push(outputRecord({
      outputId,
      deliverable,
      page,
      apiConfig,
      prompt,
      request: generation.request,
      taskId: generation.taskId,
      assetPaths: saved,
    }));
  }

  const qa = await runProjectQA({ project, registries, outputs });
  await writeFile(path.join(root, "qa.json"), `${JSON.stringify(qa, null, 2)}\n`, "utf8");

  if (args.dryRun) {
    await writeFile(path.join(root, "requests.json"), `${JSON.stringify(outputs.map(({ prompt, request, ...item }) => ({ ...item, prompt, request })), null, 2)}\n`, "utf8");
  }

  const manifest = {
    version: 3,
    engine: "api-only",
    projectId: project.projectId,
    mode: project.mode,
    runMode: args.dryRun ? "dry-run" : args.all ? "all" : "pilot",
    generatedAt: shanghaiIso(),
    completeness,
    provider: apiConfig.provider,
    model: apiConfig.model,
    visualMaster: project.visualMaster,
    outputs,
    qa: qa.summary,
    nextAction: args.dryRun
      ? "review-request-then-run-pilot"
      : args.all || rows.length === 1
        ? "review-api-images-before-publish"
        : "confirm-pilot-then-run-all",
  };
  await writeFile(path.join(root, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({ ok: qa.ok, root, manifest: path.join(root, "manifest.json"), ...manifest }, null, 2));
}

function outputRecord({ outputId, deliverable, page, apiConfig, prompt, request, taskId = null, assetPaths = [] }) {
  return {
    id: outputId,
    platform: deliverable.platform,
    pageId: page.id,
    role: page.role,
    renderMode: "api",
    provider: apiConfig.provider,
    model: apiConfig.model,
    width: deliverable.width,
    height: deliverable.height,
    ratio: deliverable.ratio,
    prompt,
    request: { url: request.url, body: request.body },
    taskId,
    assetPaths,
    imagePath: assetPaths[0] || "",
  };
}

function buildRoutingSummary(project, registries) {
  return flattenPages(project).map(({ deliverable, page }) => ({
    id: `${deliverable.platform}-${page.id}`,
    platform: deliverable.platform,
    role: page.role,
    renderMode: "api",
    recipe: registries.recipes[page.role]?.label || "unknown",
  }));
}

function stripInternal(project) {
  const copy = structuredClone(project);
  delete copy._projectPath;
  return copy;
}

function shanghaiIso() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(new Date()).replace(" ", "T") + "+08:00";
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
