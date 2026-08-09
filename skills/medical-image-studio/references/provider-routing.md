# 服务商与参数

接口信息核验日期：2026-07-19。服务商可能调整模型、价格和参数；连续出现 400 或模型不可用时，以当前官方文档为准。

## 配置优先级

脚本按以下顺序读取，前者覆盖后者：

1. 命令行中的非敏感参数，如 `--provider`、`--model`；
2. 环境变量；
3. `~/.medical-image-studio/config.json`。

支持的环境变量：

```text
MEDICAL_IMAGE_PROVIDER=hiapi|laozhang
MEDICAL_IMAGE_API_KEY=...
MEDICAL_IMAGE_LAOZHANG_MODEL=gpt-image-2-vip|gpt-image-2
MEDICAL_IMAGE_LAOZHANG_CONTROLS=1|0
HIAPI_API_KEY=...
LAOZHANG_API_KEY=...
```

不要把 Key 写进 Skill 文件夹、课程压缩包、Git 仓库、提示词或聊天记录。

## HiAPI

- Base URL：`https://api.hiapi.ai`
- 创建任务：`POST /v1/tasks`
- 查询任务：`GET /v1/tasks/{taskId}`
- 默认模型：`gpt-image-2/text-to-image`
- 输入：`prompt`、`aspect_ratio`、`resolution`
- 结果是异步任务；脚本轮询成功后立即下载图片，避免临时 URL 过期。

限制：

- `aspect_ratio=auto` 只能配 `1K`；
- `aspect_ratio=1:1` 不要配 `4K`；
- 本 Skill API 统一版只封装文生图，不上传患者/医生参考图；每张最终图片都由当前选择的服务商返回。

文档与 Key：

- https://www.hiapi.ai/docs/zh/
- https://www.hiapi.ai/en/dashboard/api-keys

## 老张 API

- Base URL：`https://api.laozhang.ai/v1`
- 文生图：`POST /images/generations`
- 响应：`data[].b64_json` 或 `data[].url`

Token 组决定真实路由：

| Token 类型 | 请求模型 | 尺寸/质量 |
| --- | --- | --- |
| 默认组按次计费，推荐 | `gpt-image-2-vip` | 支持 `size`、`quality` |
| 默认组标准路由 | `gpt-image-2` | 不传 `size`、`quality` |
| `Sora2Official` / `GPTImage2 Enterprise` 用量计费 | `gpt-image-2` | 支持官方参数 |

配置程序会记录该 Key 是否支持尺寸/质量参数。遇到 400 时先检查 Token 创建时选的组，不要只改模型名碰运气。

文档与 Key：

- https://docs.laozhang.ai/en/api-capabilities/gpt-image-2
- https://api.laozhang.ai

## 尺寸映射

HiAPI 直接接收比例；老张接收像素尺寸，脚本自动映射：

| 比例 | 1K | 2K | 4K |
| --- | --- | --- | --- |
| 1:1 | 1024×1024 | 2048×2048 | 2048×2048 |
| 16:9 / 横版 | 1536×1024 | 2048×1152 | 3840×2160 |
| 9:16 / 竖版 | 1024×1536 | 1152×2048 | 2160×3840 |
| 4:3 | 1024×1024 | 2048×1536 | 3840×2160 |
| 3:4 | 1024×1536 | 1536×2048 | 2160×3840 |

若老张默认组标准路由不支持控制参数，实际输出尺寸由服务商决定。

## 上游借鉴与取舍

- `freestylefly/awesome-gpt-image-2` 提供结构化风格/场景模板思路；本 Skill 只借鉴“场景 → 模板 → 约束 → 验收”的方法，不打包其社区案例图片。
- `wuyoscar/GPT-Image2-Skill` 提供 GPT Image 2 生成/编辑 CLI 与提示词画廊思路；本 Skill 为课堂降低依赖，并新增双服务商路由与医疗边界。
- `HiAPIAI/hiapi-gpt-image-2-skill` 提供 HiAPI 异步任务契约参考。

API 统一版不包含 HTML、浏览器截图或本地图片合成；项目的每一页都会单独调用已配置的服务商。

三个仓库均为 MIT，但社区案例、第三方图片、人物肖像、商标和具体提示词来源仍需分别确认授权。本 Skill 的医疗模板为课程用途重新编写。
