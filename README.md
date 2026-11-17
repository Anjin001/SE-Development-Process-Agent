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

## 执行与模型路由（v2）

`se_agent/runner.py` 的 `execute_task(request)` 是 Worker 入口，接 `WorkerRequest`、回 `WorkerResult`，形状见 [v2 接口契约](docs/接口契约-v2.md) §5。默认按请求自动选链路：请求带 `base_commit` 且配置了目标仓库时走真实 worktree，否则走模拟链路。

| 环境变量 | 作用 |
|---|---|
| `SE_AGENT_MODE` | `mock` / `real` / `auto`，默认 `auto` |
| `SE_AGENT_REPO` | 真实执行的目标仓库根目录 |
| `SE_AGENT_EXECUTOR` | 设为 `autogen` 时改用 AutoGen 0.2 适配器 |
| `SE_AGENT_API_KEY` | 模型凭证，也支持 `SE_AGENT_BASE_URL` 指向自建网关 |

档位、角色映射和单次尝试费用上限在 `se_agent/model_config.json`，复杂角色先由高能力模型定边界、再交低成本模型实现。
