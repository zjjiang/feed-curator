## 1. 修复与验证

- [ ] 1.1 用户提供真实 `DEEPSEEK_API_KEY` 填入 `.env`（key 不进任何提交）
- [ ] 1.2 重启服务（launchd `com.feed-curator`），确认 analyze 周期开始产出：run_log 出现 `kind='analyze'` 记录
- [ ] 1.3 存量消化完成后，验证 `analysis` 表出现 `status='ok'` 行（补上 add-corpus-search 遗留的 1.3/1.4 遗愿）
- [ ] 1.4 抽查 5 篇判定质量（summary/keypoints 与原文相符），不合格记录到 change 备注供后续调优参考
## 2. 孤儿数据

- [x] 2.1 全量核对 88 行 ai 归属（非抽样）：83 条在题（VLA/世界模型/触觉/具身 Awesome 系等）；5 条跑题——companion-emergence（AI 伴侣）、massing（建筑 BIM）、awesome-rsi（递归自改进）、chek-ego-miner（移动挖矿）、N.E.K.O（猫娘陪伴）
- [x] 2.2 按 D3 处理完毕：跑题 5 条删除（含 N.E.K.O 补删，实删 5）；其余 84 条改 `assigned_by='manual'` 留存；库内 ai 归属清零，membership 总数 88→83
## 3. 收尾

- [x] 3.1 CLAUDE.md 测试数量勘误 150 → 451（PR #21 已一并提交）
- [x] 3.2 `uv run pytest` 451 passed 全绿;`openspec validate` 通过
