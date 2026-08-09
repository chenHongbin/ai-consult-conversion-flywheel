import { readFile } from "node:fs/promises";
import path from "node:path";

const STYLE_URL = new URL("../../assets/styles/medical-styles.json", import.meta.url);
const RECIPE_URL = new URL("../../assets/recipes/visual-recipes.json", import.meta.url);

export const PLATFORM_SPECS = {
  xhs: { label: "小红书", ratio: "3:4", width: 1080, height: 1440 },
  douyin: { label: "抖音 / 视频号", ratio: "9:16", width: 1080, height: 1920 },
  wechat: { label: "公众号头图", ratio: "21:9", width: 2100, height: 900 },
  moments: { label: "朋友圈", ratio: "1:1", width: 1080, height: 1080 },
  ppt: { label: "PPT / 横版讲解", ratio: "16:9", width: 1920, height: 1080 },
  consultation: { label: "咨询解释卡", ratio: "3:4", width: 1080, height: 1440 },
};

export async function loadRegistries() {
  const [styles, recipes] = await Promise.all([
    readJsonUrl(STYLE_URL),
    readJsonUrl(RECIPE_URL),
  ]);
  return { styles, recipes };
}

export async function loadProject(projectPath) {
  const absolutePath = path.resolve(projectPath);
  let parsed;
  try {
    parsed = JSON.parse(await readFile(absolutePath, "utf8"));
  } catch (error) {
    throw new Error(`无法读取项目文件 ${absolutePath}：${error?.message || String(error)}`);
  }
  return normalizeProject(parsed, { projectPath: absolutePath });
}

export function normalizeProject(input, options = {}) {
  const project = structuredClone(input || {});
  project.version = 3;
  project.topic = clean(project.topic || "医疗视觉项目");
  project.projectId = slugify(project.projectId || project.topic);
  project.mode = clean(project.mode || "auto").toLowerCase();
  project.audience = clean(project.audience);
  project.goal = clean(project.goal);
  project.coreClaim = clean(project.coreClaim);
  project.medical = {
    reviewStatus: clean(project.medical?.reviewStatus || "review-required"),
    privacyChecked: project.medical?.privacyChecked === true,
    facts: list(project.medical?.facts),
    sources: list(project.medical?.sources),
    publishNote: clean(project.medical?.publishNote || "AI示意图，发布前请由医生或医学编辑复核"),
  };
  project.brand = {
    name: clean(project.brand?.name),
    primaryColor: clean(project.brand?.primaryColor),
    accentColor: clean(project.brand?.accentColor),
  };
  project.visualMaster = {
    style: clean(project.visualMaster?.style || "patient-editorial"),
    seriesLabel: clean(project.visualMaster?.seriesLabel || "MEDICAL VISUAL"),
    pageNumber: project.visualMaster?.pageNumber !== false,
    locked: project.visualMaster?.locked !== false,
  };
  project.deliverables = Array.isArray(project.deliverables) && project.deliverables.length
    ? project.deliverables.map((item, index) => normalizeDeliverable(item, index, project, options))
    : [normalizeDeliverable({ platform: "xhs", pages: [{}] }, 0, project, options)];
  project.mode = project.mode === "auto" ? inferProjectMode(project) : project.mode;
  project._projectPath = options.projectPath || "";
  return project;
}

function normalizeDeliverable(input, deliverableIndex, project, options) {
  const platform = clean(input?.platform || "xhs").toLowerCase();
  const spec = PLATFORM_SPECS[platform] || {
    label: platform || "自定义平台",
    ratio: clean(input?.ratio || "3:4"),
    width: Number(input?.width || 1080),
    height: Number(input?.height || 1440),
  };
  const pages = Array.isArray(input?.pages) && input.pages.length ? input.pages : [{}];
  return {
    id: clean(input?.id || `${platform}-${deliverableIndex + 1}`),
    platform,
    platformLabel: spec.label,
    ratio: clean(input?.ratio || spec.ratio),
    width: Number(input?.width || spec.width),
    height: Number(input?.height || spec.height),
    pages: pages.map((page, index) => normalizePage(page, index, project, options)),
  };
}

function normalizePage(page, index, project, options) {
  const role = clean(page?.role || (index === 0 ? "hook" : "checklist")).toLowerCase();
  return {
    id: clean(page?.id || String(index + 1).padStart(2, "0")),
    role,
    title: clean(page?.title || project.coreClaim || project.topic),
    subtitle: clean(page?.subtitle),
    body: clean(page?.body),
    points: list(page?.points),
    visualPrompt: clean(page?.visualPrompt),
    labels: list(page?.labels),
  };
}

export function assessCompleteness(project) {
  const fields = [
    ["audience", project.audience, "这套内容主要给谁看？"],
    ["goal", project.goal, "读者看完后最希望理解或做什么？"],
    ["coreClaim", project.coreClaim, "整套内容最想让读者记住哪一句话？"],
    ["deliverables", project.deliverables?.length, "最终要发布到哪个平台？"],
    ["privacy", project.medical?.privacyChecked, "素材是否已去除患者隐私信息？"],
  ];
  const missing = fields.filter(([, value]) => !value).map(([name]) => name);
  const questions = fields.filter(([, value]) => !value).map(([, , question]) => question);
  const score = Math.max(0, 100 - missing.length * 20);
  const level = score >= 80 ? "ready" : score >= 50 ? "ask-1-to-3" : "guided-brief";
  return { score, level, missing, questions: questions.slice(0, level === "guided-brief" ? 5 : 3) };
}

export function inferProjectMode(project) {
  const deliverables = project.deliverables || [];
  const pageCount = deliverables.reduce((sum, item) => sum + item.pages.length, 0);
  if (deliverables.length > 1) return "multiplatform";
  if (pageCount > 1) return "series";
  const page = deliverables[0]?.pages?.[0];
  if (["mechanism", "flow", "checklist", "comparison", "data"].includes(page?.role)) return "infographic";
  return "quick";
}

export function resolveRenderMode() { return "api"; }

export function buildVisualPrompt(project, deliverable, page, style, recipe = {}) {
  const exactText = [
    page.title ? `主标题必须逐字显示：“${page.title}”` : "",
    page.subtitle ? `副标题必须逐字显示：“${page.subtitle}”` : "",
    ...page.points.map((point, index) => `要点${index + 1}必须逐字显示：“${point}”`),
    ...page.labels.map((label, index) => `标签${index + 1}必须逐字显示：“${label}”`),
  ].filter(Boolean);
  const brand = [
    project.brand.name ? `品牌名称：${project.brand.name}。只显示名称文字，不虚构logo。` : "",
    project.brand.primaryColor ? `主色：${project.brand.primaryColor}。` : "",
    project.brand.accentColor ? `强调色：${project.brand.accentColor}。` : "",
  ].filter(Boolean).join("");
  const pageNumber = project.visualMaster.pageNumber
    ? `右下角显示页码“${page.id} / ${String(deliverable.pages.length).padStart(2, "0")}”。`
    : "不要显示页码。";
  const styleLabel = style?.label || project.visualMaster.style;
  return [
    `直接通过图像生成模型制作一张可作为最终成图使用的${deliverable.platformLabel}医疗视觉，不进行HTML或其他后期合成。`,
    `主题：${project.topic}。目标读者：${project.audience}。传播目标：${project.goal}。`,
    `全图只表达一个核心结论：${project.coreClaim || page.title}。`,
    `页面类型：${recipe.label || page.role}。画幅比例：${deliverable.ratio}。参考画布：${deliverable.width}×${deliverable.height}。`,
    `视觉体系：${styleLabel}。系列眉题：${project.visualMaster.seriesLabel}。${brand}`,
    exactText.length
      ? `图片内仅排以下中文信息，必须使用简体中文、逐字准确、清晰可读，不得改写、增字、漏字或生成乱码：\n${exactText.join("\n")}`
      : "图片内不额外添加正文。",
    pageNumber,
    page.visualPrompt ? `本页视觉补充要求：${page.visualPrompt}` : "",
    "构图要有明确视觉焦点、足够留白、高对比信息层级；不要把文字压在复杂纹理或人物五官上。",
    "医疗表达专业、克制、可信、非血腥；不生成诊断结果、处方、检查单、疗效承诺、患者证言、医院排名、假二维码或未提供的数据。",
    `底部以小号但可读文字显示：“${project.medical.publishNote}”。`,
    "这是教育性AI示意图，不冒充真实患者、真实医生、真实病例或真实诊疗证据。除指定文字外，不添加水印、无关英文或额外文案。",
  ].join("\n");
}

export function flattenPages(project) {
  const rows = [];
  for (const deliverable of project.deliverables) {
    for (const page of deliverable.pages) rows.push({ deliverable, page });
  }
  return rows;
}

export function visibleText(project, page) {
  return [project.topic, project.coreClaim, page.title, page.subtitle, page.body, ...page.points].filter(Boolean).join(" ");
}

function list(value) {
  return Array.isArray(value) ? value.map(clean).filter(Boolean) : value ? [clean(value)].filter(Boolean) : [];
}

function clean(value) {
  return value == null ? "" : String(value).trim();
}

function slugify(value) {
  const slug = clean(value).normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").toLowerCase();
  return (slug || "medical-visual").slice(0, 48);
}

async function readJsonUrl(url) {
  return JSON.parse(await readFile(url, "utf8"));
}
