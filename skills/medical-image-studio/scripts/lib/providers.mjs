import { mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

export const CONFIG_DIR = path.join(os.homedir(), ".medical-image-studio");
export const CONFIG_PATH = path.join(CONFIG_DIR, "config.json");

const DEFAULTS = {
  hiapi: {
    baseUrl: "https://api.hiapi.ai",
    model: "gpt-image-2/text-to-image",
  },
  laozhang: {
    baseUrl: "https://api.laozhang.ai/v1",
    model: "gpt-image-2-vip",
  },
};

export async function readSavedConfig() {
  try {
    return JSON.parse(await readFile(CONFIG_PATH, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return {};
    throw new Error(`配置文件无法读取：${CONFIG_PATH}`);
  }
}

export async function resolveConfig(overrides = {}) {
  const saved = await readSavedConfig();
  const provider = String(
    overrides.provider || process.env.MEDICAL_IMAGE_PROVIDER || saved.provider || "",
  ).toLowerCase();

  if (!provider && !overrides.allowMissing) {
    throw new Error("尚未选择生图服务商。请先运行 scripts/configure.mjs。 ");
  }
  if (provider && !["hiapi", "laozhang"].includes(provider)) {
    throw new Error(`不支持的服务商：${provider}`);
  }

  const providerSaved = saved.providers?.[provider] || {};
  const apiKey =
    overrides.apiKey ||
    process.env.MEDICAL_IMAGE_API_KEY ||
    (provider === "hiapi" ? process.env.HIAPI_API_KEY : process.env.LAOZHANG_API_KEY) ||
    providerSaved.apiKey ||
    "";

  const model =
    overrides.model ||
    (provider === "laozhang" ? process.env.MEDICAL_IMAGE_LAOZHANG_MODEL : "") ||
    providerSaved.model ||
    DEFAULTS[provider]?.model ||
    "";

  const controlsEnv = process.env.MEDICAL_IMAGE_LAOZHANG_CONTROLS;
  const supportsControls = provider !== "laozhang"
    ? true
    : controlsEnv !== undefined
      ? /^(1|true|yes)$/i.test(controlsEnv)
      : providerSaved.supportsControls ?? model === "gpt-image-2-vip";

  const baseUrl = String(
    overrides.baseUrl || providerSaved.baseUrl || DEFAULTS[provider]?.baseUrl || "",
  ).replace(/\/$/, "");

  if (!apiKey && !overrides.allowMissing) {
    throw new Error("尚未配置 API Key。请在本地终端运行 scripts/configure.mjs，不要把 Key 发到聊天里。");
  }

  return { provider, apiKey, model, baseUrl, supportsControls, configPath: CONFIG_PATH };
}

export function buildProviderRequest(config, options) {
  const prompt = String(options.prompt || "").trim();
  if (!prompt) throw new Error("缺少 --prompt。");

  const aspectRatio = normalizeRatio(options.aspectRatio || "3:4");
  const resolution = String(options.resolution || "2K").toUpperCase();
  const quality = String(options.quality || "high").toLowerCase();

  if (!["1K", "2K", "4K"].includes(resolution)) {
    throw new Error(`不支持的清晰度：${resolution}`);
  }
  if (!["low", "medium", "high", "auto"].includes(quality)) {
    throw new Error(`不支持的质量档：${quality}`);
  }

  if (config.provider === "hiapi") {
    if (config.model !== "gpt-image-2/text-to-image") {
      throw new Error("HiAPI 第一版只支持 gpt-image-2/text-to-image。");
    }
    if (aspectRatio === "auto" && resolution !== "1K") {
      throw new Error("HiAPI 的 aspect_ratio=auto 只能搭配 1K。");
    }
    if (aspectRatio === "1:1" && resolution === "4K") {
      throw new Error("HiAPI 的 1:1 不支持 4K，请改用 2K。");
    }
    return {
      url: `${config.baseUrl}/v1/tasks`,
      method: "POST",
      body: {
        model: config.model,
        input: { prompt, aspect_ratio: aspectRatio, resolution },
      },
      aspectRatio,
      resolution,
      quality: null,
    };
  }

  const body = { model: config.model, prompt };
  if (config.supportsControls) {
    body.size = sizeForLaoZhang(aspectRatio, resolution);
    body.quality = quality;
  }
  return {
    url: `${config.baseUrl}/images/generations`,
    method: "POST",
    body,
    aspectRatio,
    resolution,
    quality: config.supportsControls ? quality : null,
  };
}

export async function executeGeneration(config, options, fetchImpl = fetch) {
  const request = buildProviderRequest(config, options);
  if (config.provider === "hiapi") {
    return executeHiapi(config, request, options, fetchImpl);
  }
  return executeLaoZhang(config, request, options, fetchImpl);
}

async function executeHiapi(config, request, options, fetchImpl) {
  const created = await requestJson(request.url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify(request.body),
  }, fetchImpl);

  const taskId = created?.data?.taskId || created?.data?.task_id || created?.taskId;
  if (!taskId) throw new Error(`HiAPI 未返回 taskId：${compactJson(created)}`);

  const timeoutMs = Number(options.timeoutMs || 240_000);
  const pollMs = Number(options.pollMs || 3_000);
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    await sleep(pollMs);
    const statusResponse = await requestJson(
      `${config.baseUrl}/v1/tasks/${encodeURIComponent(taskId)}`,
      { headers: { Authorization: `Bearer ${config.apiKey}` } },
      fetchImpl,
    );
    const status = String(statusResponse?.data?.status || statusResponse?.status || "").toLowerCase();
    if (["success", "succeeded", "completed"].includes(status)) {
      const assets = extractAssets(statusResponse);
      if (!assets.length) throw new Error(`HiAPI 任务成功但没有图片输出。taskId=${taskId}`);
      return { taskId, assets, raw: statusResponse, request };
    }
    if (["fail", "failed", "error", "cancelled", "canceled"].includes(status)) {
      throw new Error(`HiAPI 生图失败。taskId=${taskId}，${failureSummary(statusResponse)}`);
    }
  }

  throw new Error(`HiAPI 查询超时，任务可能仍在运行。请保留 taskId=${taskId}，不要立即重复创建任务。`);
}

async function executeLaoZhang(config, request, _options, fetchImpl) {
  const response = await requestJson(request.url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request.body),
  }, fetchImpl);
  const assets = extractAssets(response);
  if (!assets.length) throw new Error(`老张 API 未返回可识别图片：${compactJson(response)}`);
  return { taskId: null, assets, raw: response, request };
}

export function extractAssets(response) {
  const candidates = [
    ...(Array.isArray(response?.data) ? response.data : []),
    ...(Array.isArray(response?.data?.output) ? response.data.output : []),
    ...(Array.isArray(response?.data?.outputs) ? response.data.outputs : []),
    ...(Array.isArray(response?.output) ? response.output : []),
    ...(Array.isArray(response?.outputs) ? response.outputs : []),
  ];
  const assets = [];
  for (const item of candidates) {
    if (!item) continue;
    if (typeof item === "string") {
      if (/^https?:\/\//.test(item)) assets.push({ kind: "url", value: item });
      else if (looksBase64(item)) assets.push({ kind: "base64", value: item, mimeType: "image/png" });
      continue;
    }
    const url = item.url || item.image_url || item.imageUrl;
    const encoded = item.b64_json || item.base64 || item.data;
    if (typeof url === "string" && /^https?:\/\//.test(url)) {
      assets.push({ kind: "url", value: url });
    } else if (typeof encoded === "string" && encoded.trim()) {
      assets.push({ kind: "base64", value: encoded, mimeType: item.mime_type || item.mimeType || "image/png" });
    }
  }
  return assets;
}

export async function saveAssets(assets, options = {}, fetchImpl = fetch) {
  const outputDir = path.resolve(options.outputDir || "医疗生图输出");
  await mkdir(outputDir, { recursive: true });
  const stamp = shanghaiTimestamp();
  const slug = slugify(options.prompt || "医疗生图");
  const saved = [];

  for (let index = 0; index < assets.length; index += 1) {
    const asset = assets[index];
    let bytes;
    let mimeType = asset.mimeType || "image/png";
    if (asset.kind === "url") {
      const response = await fetchImpl(asset.value);
      if (!response.ok) throw new Error(`图片下载失败 HTTP ${response.status}：${asset.value}`);
      bytes = Buffer.from(await response.arrayBuffer());
      mimeType = response.headers.get("content-type")?.split(";", 1)[0] || mimeTypeFromUrl(asset.value);
    } else {
      const value = asset.value.replace(/^data:[^;]+;base64,/, "");
      const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
      bytes = Buffer.from(padded, "base64");
    }
    const ext = extensionForMime(mimeType);
    const stem = options.fileStem ? slugify(options.fileStem) : `${stamp}-${slug}`;
    const filePath = path.join(outputDir, `${stem}-${index + 1}${ext}`);
    await writeFile(filePath, bytes);
    saved.push(path.resolve(filePath));
  }
  return saved;
}

export async function requestJson(url, init, fetchImpl = fetch) {
  let response;
  try {
    response = await fetchImpl(url, init);
  } catch (error) {
    throw new Error(`网络请求失败：${error?.message || String(error)}`);
  }
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    throw new Error(httpGuidance(response.status, body));
  }
  return body;
}

export function sizeForLaoZhang(ratio, resolution) {
  const normalized = normalizeRatio(ratio);
  const portrait = ["9:16", "3:4", "2:3", "4:5", "1:2", "1:3", "9:21"].includes(normalized);
  const landscape = ["16:9", "4:3", "3:2", "5:4", "2:1", "3:1", "21:9"].includes(normalized);
  if (normalized === "1:1" || normalized === "auto") {
    return resolution === "1K" ? "1024x1024" : "2048x2048";
  }
  if (resolution === "4K") return portrait ? "2160x3840" : "3840x2160";
  if (resolution === "1K") return portrait ? "1024x1536" : "1536x1024";
  if (normalized === "3:4" || normalized === "4:5") return "1536x2048";
  if (normalized === "4:3" || normalized === "5:4") return "2048x1536";
  return portrait ? "1152x2048" : landscape ? "2048x1152" : "2048x2048";
}

export function normalizeRatio(value) {
  const ratio = String(value).trim().toLowerCase();
  const aliases = { square: "1:1", portrait: "3:4", landscape: "16:9", vertical: "9:16", auto: "auto" };
  const normalized = aliases[ratio] || ratio;
  const supported = ["auto", "1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4", "5:4", "4:5", "2:1", "1:2", "3:1", "1:3", "21:9", "9:21"];
  if (!supported.includes(normalized)) throw new Error(`不支持的比例：${value}`);
  return normalized;
}

export function maskKey(value) {
  const text = String(value || "");
  if (!text) return "未配置";
  if (text.length <= 8) return "已配置（已隐藏）";
  return `${text.slice(0, 3)}…${text.slice(-4)}`;
}

function httpGuidance(status, body) {
  const summary = failureSummary(body);
  if (status === 401 || status === 403) return `API Key 无效或无权限（HTTP ${status}）：${summary}`;
  if (status === 402 || /balance|credit|quota|余额|额度|insufficient/i.test(summary)) {
    return `账户余额或额度不足（HTTP ${status}）：${summary}`;
  }
  if (status === 429) return `请求过快或被限流（HTTP 429）：${summary}`;
  if (status === 400) return `请求参数或模型路由不匹配（HTTP 400）：${summary}`;
  return `服务商请求失败（HTTP ${status}）：${summary}`;
}

function failureSummary(body) {
  return String(
    body?.data?.error?.message ||
    body?.data?.message ||
    body?.error?.message ||
    body?.message ||
    body?.raw ||
    compactJson(body),
  ).slice(0, 500);
}

function compactJson(value) {
  try { return JSON.stringify(value).slice(0, 500); } catch { return String(value).slice(0, 500); }
}

function looksBase64(value) {
  return typeof value === "string" && value.length > 64 && /^[A-Za-z0-9+/=]+$/.test(value);
}

function extensionForMime(mimeType) {
  if (/jpeg|jpg/i.test(mimeType)) return ".jpg";
  if (/webp/i.test(mimeType)) return ".webp";
  return ".png";
}

function mimeTypeFromUrl(url) {
  const pathname = new URL(url).pathname.toLowerCase();
  if (pathname.endsWith(".jpg") || pathname.endsWith(".jpeg")) return "image/jpeg";
  if (pathname.endsWith(".webp")) return "image/webp";
  return "image/png";
}

function shanghaiTimestamp() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}${get("month")}${get("day")}-${get("hour")}${get("minute")}${get("second")}`;
}

function slugify(value) {
  const cleaned = String(value).normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");
  return (cleaned || "医疗生图").slice(0, 28);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
