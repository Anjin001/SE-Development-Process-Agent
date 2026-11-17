# SE-Development-Process-Agent

第五组可运行的并行开发骨架。使用 Python 3.10+ 标准库，浏览器直接访问，无需安装依赖或配置模型密钥。

```bash
python -m se_agent.server --port 8000
python -m unittest discover -s tests -v
```

用浏览器打开 http://127.0.0.1:8000。保留默认需求，点击「生成两份草案」→「确认融合方案」→「确认并启动」。推进两轮后，T2 进入人工审核，T3 独立完成。修改 T2 产物并批准，再推进一轮。T4 的产物会显示它读取了 T2@v2。也可以退回 T2，再重试。

- [六人并行开发对接文档](docs/对接文档.md)
- [现有 v1 接口样例](examples/)
- [下一轮 v2 联调契约](docs/接口契约-v2.md) · [v2 正常与异常样例](examples/v2/scenario.json)

当前是本地模拟。两份草案、评分与任务由模板生成，Worker 返回文本。状态存于进程内、重启会清空。并发上限控制一轮选取多少任务，模拟调用按顺序完成。这个版本用来确认交接和人工审核流程。

## HTML 可视化原型

[开发流程工作台原型](prototypes/se-development-process-agent-ui.html)（乔璐脉提供）。下载仓库后直接用浏览器打开该 HTML 文件。页面使用 Tailwind CDN 和 Google Fonts，完整样式需要联网加载。

该文件为独立交互演示原型，使用页面内的模拟数据，尚未接入后端 v2 API。正式工作台入口仍按上方启动方式访问。此原型供任务配置、依赖图、甘特图与人工审阅交互对照。

- [SQLite 领域记忆服务与接入说明](docs/SQLite领域记忆模块.md)

- [QLM / ZXQ 记忆统一与数据库迁移](docs/记忆统一整合.md)
