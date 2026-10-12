## 1. 代码下线

- [x] 1.1 删除 `app/services/study_service.py`、`app/web/templates/study.html`、`tests/test_study.py`
- [x] 1.2 删 `app/main.py` 的 `GET /api/study/docs` 端点与 study_service 局部 import
- [x] 1.3 删 `app/web/pages.py` 的 `study_page` 路由
- [x] 1.4 删 `app/web/templates/layout.html` 的「学习台」导航链接

## 2. 保留面验证(TDD 回归)

- [x] 2.1 `tests/test_web.py` 的 `POST /api/docs/{id}/reading` 测试保留并通过
- [x] 2.2 全量 451 passed 全绿;覆盖 87%(≥80%);另补 .gitignore data/server.log

## 3. 文档与边界声明

- [x] 3.1 `CLAUDE.md`:目录布局删 study 两行;新增状态归属边界声明(奏折已阅/课程掌握归 deputy,原文已读/收藏归 feed-curator reading 表,状态跟着实体走、跨系统只取数不搬状态)

## 4. 归档与发布

- [x] 4.1 归档 change,主 specs 删除 `study-workbench/` 目录,`openspec validate` 通过、活跃 change 清零
- [x] 4.2 重启服务冒烟:`/study` 404、`/` 与 `/admin` 200、`POST /api/docs/{id}/reading` 可写(实测 {"ok":true})
- [x] 4.3 提交推分支开 PR,等用户 review 合并
