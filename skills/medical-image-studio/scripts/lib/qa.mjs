import { access, readFile } from "node:fs/promises";
import { visibleText } from "./project.mjs";

const RISK_PATTERNS = [
  [/根治|包治|保证治愈|百分之百|100%/i, "可能包含疗效或绝对化承诺"],
  [/第一|唯一|最好|顶级/i, "可能包含无法证实的绝对化或排名表达"],
  [/真实患者|治疗前后|术前术后/i, "可能被理解为真实患者或疗效对比"],
];

export async function runProjectQA({ project, registries, outputs }) {
  const checks = [];
  const style = registries.styles[project.visualMaster.style];
  checks.push(check("project.style", Boolean(style), style ? "统一视觉母版存在" : `未知视觉风格：${project.visualMaster.style}`, style ? "pass" : "fail"));
  checks.push(check("medical.privacy", project.medical.privacyChecked, project.medical.privacyChecked ? "已声明完成隐私预检" : "尚未声明患者隐私已检查", project.medical.privacyChecked ? "pass" : "fail"));
  checks.push(check("medical.review", Boolean(project.medical.reviewStatus), "已设置医学复核状态", project.medical.reviewStatus ? "pass" : "fail"));

  for (const deliverable of project.deliverables) {
    for (const page of deliverable.pages) {
      const recipe = registries.recipes[page.role];
      if (!recipe) {
        checks.push(check(`${deliverable.id}.${page.id}.recipe`, false, `未知页面角色：${page.role}`, "fail"));
        continue;
      }
      const titleLength = page.title.replace(/\s/g, "").length;
      checks.push(check(
        `${deliverable.id}.${page.id}.title`,
        titleLength <= recipe.titleMax,
        titleLength <= recipe.titleMax ? `标题 ${titleLength} 字，容量正常` : `标题 ${titleLength} 字，建议不超过 ${recipe.titleMax} 字`,
        titleLength <= recipe.titleMax ? "pass" : "warn",
      ));
      checks.push(check(
        `${deliverable.id}.${page.id}.points`,
        page.points.length <= recipe.pointsMax,
        page.points.length <= recipe.pointsMax ? `要点 ${page.points.length} 个，容量正常` : `要点 ${page.points.length} 个，当前 API 成图建议最多 ${recipe.pointsMax} 个`,
        page.points.length <= recipe.pointsMax ? "pass" : "warn",
      ));
      if (recipe.requiresMedicalSource) {
        checks.push(check(
          `${deliverable.id}.${page.id}.source`,
          project.medical.sources.length > 0,
          project.medical.sources.length ? "机制/数据页已记录来源" : "机制/数据页没有记录医学来源",
          project.medical.sources.length ? "pass" : "warn",
        ));
      }
      const text = visibleText(project, page);
      for (const [pattern, message] of RISK_PATTERNS) {
        if (pattern.test(text)) checks.push(check(`${deliverable.id}.${page.id}.risk`, false, message, "warn"));
      }
    }
  }

  for (const output of outputs) {
    if (!output.imagePath) continue;
    try {
      await access(output.imagePath);
      const dimensions = await readPngDimensions(output.imagePath);
      if (dimensions) {
        const actualRatio = dimensions.width / dimensions.height;
        const targetRatio = output.width / output.height;
        const ratioCorrect = Math.abs(actualRatio - targetRatio) / targetRatio <= 0.025;
        checks.push(check(
          `${output.id}.dimensions`,
          ratioCorrect,
          ratioCorrect
            ? `API 图片比例正确：${dimensions.width}×${dimensions.height}，目标比例 ${output.ratio}`
            : `API 图片尺寸 ${dimensions.width}×${dimensions.height}，与目标比例 ${output.ratio} 不一致`,
          ratioCorrect ? "pass" : "warn",
        ));
      } else {
        checks.push(check(`${output.id}.dimensions`, true, "图片存在；非PNG格式未执行像素头检查", "warn"));
      }
    } catch {
      checks.push(check(`${output.id}.file`, false, `输出文件不存在：${output.imagePath}`, "fail"));
    }
  }

  const summary = {
    pass: checks.filter((item) => item.status === "pass").length,
    warn: checks.filter((item) => item.status === "warn").length,
    fail: checks.filter((item) => item.status === "fail").length,
  };
  return { ok: summary.fail === 0, summary, checks };
}

async function readPngDimensions(filePath) {
  const buffer = await readFile(filePath);
  const signature = buffer.subarray(0, 8).toString("hex");
  if (signature !== "89504e470d0a1a0a" || buffer.length < 24) return null;
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function check(id, ok, message, status) {
  return { id, ok: Boolean(ok), status, message };
}
