---
name: medical-image-studio
description: 把一句医疗业务需求编译成单图、系列图或多平台视觉，并统一通过学员本地配置的老张 API 或 HiAPI Key 生成最终图片。Use when the user asks to 生图、医疗科普卡、患者教育卡、医生IP封面、短视频封面、小红书配图、公众号头图、咨询解释卡、活动海报、机制图、信息图、一题多图、多平台版本，或检查/配置本 Skill 的服务商与 Key。所有页面都调用 API，不使用 HTML、浏览器截图或本地排版合成；不用于诊断、治疗决策或医疗广告合规背书。
---

# 医疗内容生图 Skill｜API 统一版

学员只说业务话。Agent 负责理解医疗场景、创建项目、限制文字容量、编译逐页提示词、先生成一张确认图、批量延展和发布前提醒；脚本负责老张/HiAPI 路由、请求、轮询、下载、项目状态和 QA。

## 不可变规则

- 每一张最终图片都必须由已配置的老张 API 或 HiAPI 生成。
- 不使用 HTML、CSS、Chrome 截图、Canvas 或本地排版工具合成最终图片。
- 多页项目默认只调用 API 生成第一张；用户确认或明确要求后再生成全部页面。
- 图片内中文可能被图像模型写错。提示词必须要求逐字准确，交付前仍需人工目检；脚本不宣称已完成 OCR 校对。
- 当前服务商层只实现文生图，不上传本地参考图、logo、医生照片或患者照片。需要某种视觉特征时，用文字描述，不假装已使用附件。

## 首次配置

需要 Node.js 18 或更高版本，不需要 `npm install`、Python或浏览器。

解析本文件所在 Skill 目录，使用绝对路径运行：

```bash
node <skill-dir>/scripts/medical-image.mjs --doctor
```

未配置时，让用户在自己的本地终端运行：

```bash
node <skill-dir>/scripts/configure.mjs
```

Key 保存在 `~/.medical-image-studio/config.json`。不要让用户把 Key 发到聊天里，不要把 Key 放进命令行、项目文件、提示词、日志或课程压缩包。服务商与参数见 [服务商与参数](references/provider-routing.md)。

## 工作流

### 1. 把一句话编译成项目

提取：

- 受众：患者、家属、医生、医院运营人员或大众；
- 目标：停留、解释、收藏、医生 IP、活动传播或咨询承接；
- 核心结论：整套内容只锁定一个；
- 平台、比例和页数；
- 每页短标题、副标题和少量要点；
- 品牌名称、颜色和视觉描述；
- 医学事实来源、隐私状态和发布前复核状态。

按 [项目文件契约](references/project-contract.md) 创建 `project.json`。不要让学员手填；Agent 复制 `assets/project-template.json` 后自动填写。

### 2. 自适应追问

先运行：

```bash
node <skill-dir>/scripts/run.mjs --project <project.json> --inspect
```

- `ready`：直接编译首张；
- `ask-1-to-3`：只问最影响结果的 1–3 个问题；
- `guided-brief`：补齐受众、目标、核心结论、平台和隐私状态。

用户明确说“直接做”时可使用安全假设和 `--assume`。不得假设医学数据、疗效、医生资质、患者身份或素材授权。

### 3. 选择内容路由

读取 [视觉路由](references/visual-routing.md)，判断项目为：

- `quick`：单图；
- `series`：同平台多页；
- `multiplatform`：同一结论跨平台；
- `infographic`：机制、流程、清单、对比或数据图。

页面可使用 `hook`、`misconception`、`mechanism`、`flow`、`checklist`、`comparison`、`data`、`doctor-ip`、`story`、`event`、`quote`、`action`。这些只决定提示词结构，所有页面的 `renderMode` 都是 `api`。

### 4. 检查医疗边界

每次生成前读取 [医疗内容安全边界](references/compliance.md)。

- 不把未脱敏患者资料写进提示词；
- 不伪造病例、检查单、处方、疗效对比、医生身份或患者证言；
- 机制、用药、指标、疾病结论和数据缺少可靠来源时，只做通用教育示意并标记专业复核；
- API 生成成功不等于医学正确、文字正确或广告合规。

### 5. 控制图片内文字

图像模型处理长中文不稳定，因此：

- 主标题优先 14 字以内；
- 副标题只保留一句；
- 每页要点通常不超过 3 条，每条尽量 16 字以内；
- 机制、清单、对比和数据过多时拆页；
- 必须逐字出现的文字分别写入 `title`、`subtitle`、`points`；
- `visualPrompt` 只补充主体、场景、构图、材质和光线，不重复正文。

页面容量规则在 `assets/recipes/visual-recipes.json`，六套医疗风格在 `assets/styles/medical-styles.json`。

### 6. 先做不扣费预检

```bash
node <skill-dir>/scripts/run.mjs \
  --project <project.json> \
  --pilot \
  --dry-run
```

输出 `requests.json`，可检查服务商、模型、比例和逐页提示词；不联网、不调用 API、不扣费。

### 7. 调用 API 生成首张

用户已经要求生成时，执行：

```bash
node <skill-dir>/scripts/run.mjs \
  --project <project.json> \
  --pilot
```

确认首张后，或用户明确要求整套直出时：

```bash
node <skill-dir>/scripts/run.mjs \
  --project <project.json> \
  --all
```

每一页都会单独调用当前已配置的服务商。可用参数：

- `--output <dir>`：输出根目录；
- `--provider hiapi|laozhang`：临时覆盖服务商；
- `--model <id>`：临时覆盖模型；
- `--resolution 1K|2K|4K`：默认 `2K`；
- `--quality low|medium|high|auto`：老张默认 `high`；
- `--assume`：按项目中的安全假设继续。

输出：

```text
<project-id>/
├── project.normalized.json
├── manifest.json
├── qa.json
├── requests.json        # 仅 dry-run
└── images/              # API 返回图片
```

### 8. 目检并交付

检查 `qa.json` 和每张 API 图片：

- 标题、副标题、数字和要点是否逐字正确；
- 是否出现乱码、多余英文、多余水印或意外文字；
- 一页是否只有一个主要结论；
- 系列颜色、质感和主体是否一致；
- 医学结构、事实和数据是否与来源一致；
- 是否含隐私、虚构疗效、误导性真人或越界诊断；
- 图片比例是否符合平台。

发现文字错误时，缩短文字、加强逐字约束后针对该页重新调用 API。不要用本地 HTML 叠字“修好”后冒充 API 原生结果。

## 单张快速入口

不需要项目管理时：

```bash
node <skill-dir>/scripts/medical-image.mjs \
  --dry-run \
  --prompt "最终提示词" \
  --aspect-ratio 3:4 \
  --resolution 2K
```

确认后去掉 `--dry-run`。提示词结构见 [医疗场景与提示词模板](references/medical-scenes.md)。

## 失败处理

- 未配置 Key：运行 `configure.mjs`；不在聊天中收集 Key。
- 401/403：停止重试，检查 Key 和权限。
- 402、余额/额度不足：提示检查服务商余额。
- 429：等待后再试，不并发轰炸。
- 400：核对服务商、模型、比例、清晰度和老张 Token 组。
- HiAPI 超时：保留 `taskId`，不要立刻重复创建任务扣费。
- 文字生成错误：缩短文案并只重做问题页。
- 安全策略拒绝：改成教育性、非诊断、非疗效承诺表达，不绕过策略。

## 交付格式

返回图片本地绝对路径、服务商、模型、平台比例、核心结论和 QA 结果，并明确提醒“发布前仍需核对图片内文字、医学事实、授权和广告表达”。

本 Skill 负责调用 API 生产医疗视觉和风险提示，不替代医生判断、医学编辑审核、法务审核或平台广告审核。
