## 1. 修复与验证

- [ ] 1.1 用户提供真实 `DEEPSEEK_API_KEY` 填入 `.env`（key 不进任何提交）
- [ ] 1.2 重启服务（launchd `com.feed-curator`），确认 analyze 周期开始产出：run_log 出现 `kind='analyze'` 记录
- [ ] 1.3 存量消化完成后，验证 `analysis` 表出现 `status='ok'` 行（补上 add-corpus-search 遗留的 1.3/1.4 遗愿）
- [ ] 1.4 抽查 5 篇判定质量（summary/keypoints 与原文相符），不合格记录到 change 备注供后续调优参考
## 2. 孤儿数据

- [ ] 2.1 抽样 88 行 `assigned_by='ai'` membership 对应文档，确认归属是否合理
- [ ] 2.2 按 D3 结论处理（改 manual 留存或删除），处理结果记录在本文件
## 3. 收尾

- [ ] 3.1 CLAUDE.md 测试数量勘误 150 → 451（本 PR 一并提交）
- [ ] 3.2 `uv run pytest` 全绿 + `openspec validate fix-analysis-cycle` 通过
