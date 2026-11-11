# ReviewLens — 电商评论洞察 Agent

输入一批商品评论，Agent 自动完成 **清洗 → 情感分析 → 竞品维度对比 → 报告生成**，关键结论经人工审核后输出。

> 定位：AI Agent 应用开发实习的作品集项目。目标不是"能跑"，是**能讲清每个设计为什么这么做**。

## 目录

```
reviewlens/
├── app/
│   ├── config.py        # 已就绪：环境变量 + LLM 工厂（端点已实测）
│   ├── graph.py         # 待写：State 定义 + 各节点（D2 开始）
│   └── main.py          # 待写：FastAPI + SSE（D2/D7）
├── data/
│   ├── reviews_raw.jsonl    # 210 条模拟评论（20 条重复，10 条无文本，含脏数据）
│   ├── reviews_gold.jsonl   # 同批数据的金标注：情感 / 维度 / 是否脏
│   └── skus.json            # 6 个 SKU，2 品类 × 2-3 品牌（对比子图用）
├── models/bge-small-zh-v1.5/ # 本地中文 embedding（ONNX，95MB，已验证）
├── scripts/
│   ├── gen_reviews.py    # 数据生成器（可重跑，随机种子固定）
│   └── embed_ref.py       # embedding 参考实现（验证过，D3 自己重写进 app/）
├── docs/D1-环境实测.md
├── .env                  # 本地密钥（已 gitignore）
└── requirements.txt      # 实际安装版本快照
```

## 哪些是脚手架，哪些必须自己写

| 文件 | 谁写 | 原因 |
|---|---|---|
| `app/config.py`、`scripts/*`、`data/*` | 脚手架 | 属于环境/数据准备，面试不问 |
| `app/graph.py` 及之后所有节点 | **你自己写** | 面试就追这里：reducer、子图、interrupt、流式 |
| `app/main.py` | **你自己写** | 同上 |
| README 的技术难点、数字 | **你自己写** | 数字必须是你实测的，别抄模板 |

## 环境（已实测，2026-11-11）

| 项 | 状态 |
|---|---|
| Python | 3.12.6（`D:\python`，项目 venv 在 `.venv/`） |
| langgraph | 1.2.20 / checkpoint-sqlite 3.1.3 / langchain-core 1.7.7 / langchain-deepseek 1.1.1 |
| LLM | DeepSeek-V4.1-Flash（OpenAI 兼容中转 `api.tokenjuice.ai`）；**官方 api.deepseek.com 的 key 本机没有** |
| 能力验证 | 普通对话 ✔ / Function Calling ✔ 0.9s / with_structured_output ✔ 1.2s |
| Embedding | 本地 `bge-small-zh-v1.5` ONNX，512 维，4 条 9ms；不用 torch |
| Chroma | 1.5.9 已装，但**默认 embedding 是英文 MiniLM 且从 S3 下 79MB 只有 36KB/s**，别用 |
| Docker | CLI 29.7.2 已装，**daemon 未启动**（D8 先开 Docker Desktop） |

跑起来：

```bash
cd D:\projects\reviewlens
.venv\Scripts\activate
python -c "from app.config import get_llm; print(get_llm().invoke('hi').content)"
```

## 进度

- [x] D1 环境 + 仓库 + 数据
- [ ] D2 单 Agent 最小图跑通 `/chat`
- [ ] D3 RAG 子图
- [ ] D4 三个工具 + ToolNode
- [ ] D5 编排器 + 子图并行
- [ ] D6 SQLite Checkpointer + HITL
- [ ] D7 SSE 流式 + 单页前端
- [ ] D8 Docker
- [ ] D9 README + 演示视频
- [ ] D10 简历 + 面试 10 问
