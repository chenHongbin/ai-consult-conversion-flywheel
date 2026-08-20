# 参与贡献

欢迎提交通用功能、兼容性修复、文档改进、安全规则和完全合成的测试案例。

## 开始之前

1. 先确认改动属于所有机构可复用的公共 Core；机构名称、价格、医生、成员、内部 SOP 和真实案例应留在本地工作区。
2. 不要提交患者录音、微信截图、聊天文本、姓名、电话、微信号、身份证、地址、病历号、头像、IMA 凭证或 API 密钥。
3. 外部来源的方法论、图片、模板和代码必须允许按 MIT License 再分发，并在 Pull Request 中说明来源与授权。
4. 涉及工作区 Schema 时，必须保持旧工作区可读取，或同时提供显式迁移和回滚说明。

## 本地验证

```text
python3 -m unittest discover -s tests
python3 scripts/release_check.py
python3 scripts/build_base_skill_package.py --output-dir dist
python3 scripts/verify_public_package.py dist/AI咨询转化飞轮_v$(cat VERSION).skill
```

Skill 结构还应使用宿主提供的 Skill validator 验证。测试材料必须是明显标记的合成数据，不能只是把真实资料改掉姓名后提交。

## Pull Request 要说明

- 用户问题和预期结果；
- 为什么它属于公共 Core，而不是机构本地配置；
- 影响的运行层和数据边界；
- 已运行的测试；
- 是否改变 Schema、权限、联网、外发或持久化行为。

贡献代码即表示你有权按本项目许可证提交该内容。维护者可以拒绝来源或授权不清、包含敏感数据、扩大默认权限或缺少迁移路径的变更。

## 客户反馈如何进入项目

无法访问 GitHub 的客户可以在 Skill 中说“我要反馈问题”，把本地生成的脱敏反馈卡交给安装包提供者。服务人员应先去重、分级并确认没有患者资料、机构身份或凭证，再代为建立 Issue；安全问题按 `SECURITY.md` 使用私密渠道，不建立公开 Issue。
