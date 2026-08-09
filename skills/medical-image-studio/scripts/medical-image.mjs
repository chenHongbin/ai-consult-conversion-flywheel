#!/usr/bin/env node
import process from "node:process";
import {
  CONFIG_PATH,
  buildProviderRequest,
  executeGeneration,
  maskKey,
  resolveConfig,
  saveAssets,
} from "./lib/providers.mjs";

function parseArgs(argv) {
  const args = {};
  const aliases = { "aspect-ratio": "aspectRatio", ratio: "aspectRatio", output: "outputDir", provider: "provider", model: "model", prompt: "prompt", resolution: "resolution", quality: "quality", "poll-ms": "pollMs", "timeout-ms": "timeoutMs" };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "--dry-run") args.dryRun = true;
    else if (token === "--doctor") args.doctor = true;
    else if (token === "--help" || token === "-h") args.help = true;
    else if (token.startsWith("--")) {
      const raw = token.slice(2);
      const [name, inline] = raw.split("=", 2);
      const key = aliases[name] || name;
      const value = inline ?? argv[++i];
      if (value === undefined || value.startsWith("--")) throw new Error(`参数 --${name} 缺少值。`);
      args[key] = value;
    } else {
      throw new Error(`无法识别的参数：${token}`);
    }
  }
  return args;
}

function printHelp() {
  console.log(`医疗内容生图 Skill

用法：
  node scripts/medical-image.mjs --doctor
  node scripts/medical-image.mjs --dry-run --prompt "..." --aspect-ratio 3:4 --resolution 2K
  node scripts/medical-image.mjs --prompt "..." --aspect-ratio 3:4 --resolution 2K --output ./医疗生图输出

参数：
  --prompt           最终生图提示词（必填）
  --aspect-ratio     auto/1:1/3:4/9:16/16:9 等，默认 3:4
  --resolution       1K/2K/4K，默认 2K
  --quality          low/medium/high/auto，默认 high
  --output           输出目录，默认 ./医疗生图输出
  --doctor           检查配置，不访问网络、不显示完整 Key
  --dry-run          只显示请求，不访问网络、不扣费
`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return printHelp();
  if (Number(process.versions.node.split(".", 1)[0]) < 18) {
    throw new Error(`需要 Node.js 18 或更高版本，当前为 ${process.versions.node}。`);
  }

  if (args.doctor) {
    const config = await resolveConfig({ ...args, allowMissing: true });
    console.log(JSON.stringify({
      ok: Boolean(config.provider && config.apiKey),
      node: process.versions.node,
      provider: config.provider || "未配置",
      model: config.model || "未配置",
      key: maskKey(config.apiKey),
      controls: config.provider === "laozhang" ? config.supportsControls : "provider-native",
      configPath: CONFIG_PATH,
      networkChecked: false,
    }, null, 2));
    if (!config.provider || !config.apiKey) process.exitCode = 2;
    return;
  }

  if (!args.prompt) throw new Error("缺少 --prompt。使用 --help 查看示例。");
  const config = await resolveConfig({ ...args, allowMissing: Boolean(args.dryRun) });
  const effectiveConfig = config.provider ? config : { ...config, provider: "hiapi", model: "gpt-image-2/text-to-image", baseUrl: "https://api.hiapi.ai", supportsControls: true };
  const request = buildProviderRequest(effectiveConfig, args);

  if (args.dryRun) {
    console.log(JSON.stringify({
      dryRun: true,
      provider: effectiveConfig.provider,
      model: effectiveConfig.model,
      url: request.url,
      body: request.body,
      note: "未访问网络，未产生费用，未显示 API Key。",
    }, null, 2));
    return;
  }

  const result = await executeGeneration(effectiveConfig, args);
  const files = await saveAssets(result.assets, { outputDir: args.outputDir, prompt: args.prompt });
  console.log(JSON.stringify({
    ok: true,
    provider: effectiveConfig.provider,
    model: effectiveConfig.model,
    taskId: result.taskId,
    aspectRatio: result.request.aspectRatio,
    resolution: result.request.resolution,
    files,
    review: "AI示意素材；发布前请完成医学事实、授权、广告表达和平台规则复核。",
  }, null, 2));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
