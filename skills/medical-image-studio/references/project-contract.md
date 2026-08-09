# API 统一版项目文件契约

`project.json` 是一套医疗视觉项目的状态文件。Agent 自动创建和更新，学员无需手填。

## 最小结构

```json
{
  "version": 3,
  "projectId": "short-id",
  "topic": "主题",
  "mode": "auto",
  "audience": "目标读者",
  "goal": "看完后希望理解或行动什么",
  "coreClaim": "整套只保留一个核心结论",
  "medical": {
    "reviewStatus": "review-required",
    "privacyChecked": true,
    "facts": [],
    "sources": [],
    "publishNote": "AI示意图，发布前请由医生或医学编辑复核"
  },
  "brand": {
    "name": "",
    "primaryColor": "",
    "accentColor": ""
  },
  "visualMaster": {
    "style": "patient-editorial",
    "seriesLabel": "PATIENT EDUCATION",
    "pageNumber": true,
    "locked": true
  },
  "deliverables": [
    {
      "platform": "xhs",
      "pages": [
        {
          "id": "01",
          "role": "hook",
          "title": "标题",
          "subtitle": "一句解释",
          "points": [],
          "visualPrompt": "主体、场景、构图、材质或光线"
        }
      ]
    }
  ]
}
```

完整模板见 `assets/project-template.json`，课堂示例见 `assets/examples/fever-education-series.json`。

## 平台标识

| platform | 参考画布 | API 比例 |
| --- | --- | --- |
| `xhs` | 1080×1440 | 3:4 |
| `douyin` | 1080×1920 | 9:16 |
| `wechat` | 2100×900 | 21:9 |
| `moments` | 1080×1080 | 1:1 |
| `ppt` | 1920×1080 | 16:9 |
| `consultation` | 1080×1440 | 3:4 |

服务商可能返回同比例的 1K、2K 或 4K 像素尺寸，不要求与参考画布像素完全相同。

## 页面角色

支持：`hook`、`misconception`、`mechanism`、`flow`、`checklist`、`comparison`、`data`、`doctor-ip`、`story`、`event`、`quote`、`action`。

每页使用 `assets/recipes/visual-recipes.json` 中的文字容量。标题或要点超量时拆页，不把大量中文塞进一张 API 图片。

## 字段约定

- `title`、`subtitle`、`points`、`labels`：必须逐字出现在图片中的中文。
- `visualPrompt`：只描述主体、场景、构图、材质、光线和负面要求。
- `facts`、`sources`：机制、数据和医学结论的事实底稿；不自动变成图片内文字。
- `reviewStatus`：默认 `review-required`，不得擅自改成已审核。
- `visualMaster.locked: true`：多页和多平台默认锁定视觉体系。
- 每页最终 `renderMode` 固定为 `api`，项目文件不再提供 HTML、layout 或 hybrid 选项。

## 当前素材边界

当前脚本只调用文生图接口，不上传本地参考图、logo、医生照片或患者照片。因此项目文件不包含 `imagePath` 或 `logoPath`。若用户提供附件，只能提取经过授权且不含隐私的文字化视觉描述；不得声称 API 使用了未上传的附件。
