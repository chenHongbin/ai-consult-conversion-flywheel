## 解决的问题

## 为什么属于公共 Core

## 数据、权限与兼容性影响

- [ ] 不包含真实患者资料、机构机密或凭证
- [ ] 没有扩大默认联网、外发、写入或删除权限
- [ ] Schema 变化包含兼容、迁移与回滚说明
- [ ] 第三方内容允许按 MIT License 再分发

## 验证

- [ ] `python3 -m unittest discover -s tests`
- [ ] `python3 scripts/release_check.py`
- [ ] 公共包构建和复核通过
