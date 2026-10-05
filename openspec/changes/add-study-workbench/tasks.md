# Tasks: add-study-workbench

## 1. 载荷 service

- [ ] 1.1 `app/services/study_service.py`:`collect_study_docs(db, days) -> list[dict]`——窗口过滤、最新 ok analysis 去重(MAX(id) GROUP BY)、领域名/管道名映射、reading 状态合并;星级降序、时间降序
- [ ] 1.2 单测:窗口边界、排序、多条 ok 判定去重、未判定文档保留(stars=0)、领域/管道名映射、reading 初值

## 2. API 与页面路由

- [ ] 2.1 `app/main.py`:`GET /api/study/docs?days=N`(Query 校验 1–30,默认 7),调 study_service,返回 `{days, count, docs}`
- [ ] 2.2 `app/web/pages.py`:`GET /study` 渲染 `study.html`(传入领域列表与默认 days)
- [ ] 2.3 API 测试:payload 形状、days 越界 422、与 reading API 的联动(标已读后载荷反映)

## 3. 前端模板

- [ ] 3.1 `app/web/templates/study.html`:独立整页模板,内联 CSS/JS;`fetch('/api/study/docs')` 载入;卡片流(键盘 `←→`/`jk` 翻篇、翻过即已读、`f` 收藏、进度条、位置持久化)与列表(详情浮层)双模式;共用过滤器(星级/领域/类型/未读/收藏/搜索)
- [ ] 3.2 `layout.html` 导航加「学习台」链接
- [ ] 3.3 页面测试:`/study` 200 且含工作台标识;模板渲染不依赖外网资源

## 4. 收尾

- [ ] 4.1 `uv run pytest` 全绿,覆盖率 ≥ 80%
- [ ] 4.2 本机起服务人工验收双模式动线(分支环境下 :9004 冒烟)
