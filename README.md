# LANShing疼痛随访智能体

基于 **FastAPI + LangGraph 多 Agent + RAG** 的LANShing疼痛随访智能体。

---

## 快速开始（拉取代码与运行）

### 1. 拉取代码

```bash
git clone https://github.com/wuzhanbo1007/pain-follow-up-agent.git
cd pain-follow-up-agent
```

已有仓库时同步最新代码：`git pull`

### 2. 前置依赖

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | 3.10+ | 后端 |
| Node.js | 18+ | 前端 |
| MySQL | 5.7+/8.x | 业务数据（21 表，库名 `pain-followup`；首次启动自动建表并播种 50 名模拟患者） |
| Elasticsearch | 8.x | RAG 向量库（BM25 + kNN + RRF 混合检索） |

> LLM / ES / MySQL 未配置时，后端自动降级为关键词 + 模板模式，Demo 不中断。

### 3. 运行后端（端口 5000）

```bash
cd pain-followup-demo/backend

# 创建虚拟环境并安装依赖
python -m venv .venv
# Windows：.venv\Scripts\activate ；macOS/Linux：source .venv/bin/activate
pip install -r requirements.txt "unstructured[pdf]"

# 配置环境变量（编辑 LLM_API_KEY / DB / ES / Embedding 等）
cp .env.example .env

# 启动（首次启动会自动建表并播种模拟患者数据）
python app.py
```

### 4. 运行前端（端口 3000）

```bash
cd pain-followup-demo/frontend
npm install
npm run dev
```

### 5. 访问

- 前端 Demo 页面：http://localhost:3000
- 后端 API / WebSocket：http://localhost:5000

---

## 一、架构概述

```
A 号 Planner → B 号 Scheduler → C 号 Execution → D 号 Summarizer
   (计划生成)     (当日判定)      (ReACT 对话)      (内容总结)

入口: app.py → bootstrap → Socket.IO + FastAPI
```

| Agent | 文件 | 类型 | 职责 |
|---|---|---|---|
| A 号 | `agents/planner.py` | LangGraph 工作流 | 出院随访计划生成 + 医生 HITL 审阅 |
| B 号 | `services/daily_scheduler.py` | 规则判定函数 | 今日是否随访（纯规则引擎） |
| C 号 | `agents/execution.py` | ReACT 自主 Agent | 多轮随访对话，LLM 持工具自主解析+追问 |
| D 号 | `agents/summarizer.py` | LLM 分析函数 | 随访会话内容总结（摘要/风险/完成度） |

C 号是唯一真正自主的 Agent，A 号是工作流，B/D 号是 LLM 增强判定函数。

---

## 二、目录结构

```
pain-followup-demo/
├── backend/
│   ├── app.py                  # 入口：FastAPI + Socket.IO + bootstrap
│   ├── agents/                 # 真正的 Agent（仅自主型+工作流）
│   │   ├── planner.py          #   A 号 随访计划生成
│   │   ├── execution.py        #   C 号 随访执行（ReACT 自主 Agent）
│   │   ├── summarizer.py       #   D 号 会话内容总结
│   │   ├── orchestrator.py     #   编排器 B→C→D 串联
│   │   └── state.py            #   LangGraph 状态定义
│   ├── services/               # 业务服务（非 Agent）
│   │   ├── followup_service.py #   随访服务（Ws 驱动 / 自动编排）
│   │   ├── daily_scheduler.py  #   B 号 当日判定
│   │   └── doctor_review.py    #   医生人工审阅管线
│   ├── engine/                 # 领域引擎
│   │   ├── react_core.py       #   ReACT 处理引擎（LLM + 5 工具）
│   │   ├── risk_engine.py      #   风险评分引擎
│   │   ├── auto_reply.py       #   自动患者回复模拟
│   │   ├── followup_scheduler.py # 随访排程规则引擎
│   │   ├── tool_definitions.py #   Function-calling 工具定义
│   │   └── tool_executor.py    #   工具执行器
│   ├── db/                     # 持久化层（MySQL，会话/计划/审阅 DAO）
│   │   └── followup_db.py      #   会话/计划/审阅 DAO
│   ├── data/                   # 种子数据
│   │   ├── database.py         #   PatientDB + 数据库初始化
│   │   └── patients.py         #   患者数据定义
│   ├── prompts/                # LLM 提示词
│   │   ├── react_prompts.py    #   C 号 ReACT 护士/追问/滚动摘要提示词
│   │   ├── reply_parsing.py    #   患者回复结构化解析提示词
│   │   ├── plan_generation.py  #   A 号 计划生成提示词
│   │   ├── plan_system.py      #   A 号 强制 JSON 输出提示词
│   │   ├── review_analysis.py  #   D 号 会话总结提示词
│   │   ├── personalized_message.py # 个性化开场白/告别语
│   │   └── clarification_message.py # 模糊回复澄清
│   ├── llm/                    # LLM 封装层
│   │   ├── client.py           #   统一 chat 接口
│   │   ├── model.py            #   模型构建（bind_tools 等）
│   │   ├── message_generator.py #  个性化消息生成
│   │   └── parser.py           #   患者回复解析（NLP→结构化）
│   ├── routes/                 # HTTP + WebSocket 路由
│   │   ├── ws.py               #   Socket.IO 事件处理
│   │   ├── review.py           #   审阅 REST API
│   │   ├── plan.py             #   计划 REST API
│   │   └── patients.py         #   患者 REST API
│   ├── core/                   # 基础设施
│   │   ├── config.py           #   全局配置
│   │   ├── bootstrap.py        #   启动初始化
│   │   ├── container.py        #   DI 容器
│   │   ├── event_bus.py        #   事件总线
│   │   └── realtime.py         #   WebSocket 桥接
│   └── knowledge/              # RAG 知识库（ES）
│       ├── loader.py           #   PDF/文档加载（Unstructured 自动解析 + OCR）
│       ├── splitter.py         #   中文分块（按标题层级 + 800字递归切分）
│       ├── embeddings.py       #   Embedding 提供方（OpenAI 兼容 / 本地 bge-m3 / SiliconFlow 等）
│       ├── es_store.py         #   ES 向量库封装（混合检索）
│       ├── retriever.py        #   检索入口（指南/共识引用溯源）
│       ├── ingest.py           #   批量入库入口
│       └── config.py           #   RAG 配置
├── frontend/                   # Vue 3 + Pinia + Tailwind
│   └── src/
│       ├── pages/DemoPage.vue  #   主页面（三 Tab）
│       ├── pages/ChatPage.vue  #   微信聊天页
│       └── components/         #   审阅/对话/控制面板等
└── knowledge_base/             # 知识库语料（不入 git）
    └── raw/                    # 原始 PDF 文档
        ├── consensus/          #   专家共识
        ├── guidelines/         #   临床指南
        ├── pathways/           #   诊疗路径
        └── internal/           #   内部文档
```

---

## 三、C 号 ReACT Agent 的工具

LLM 在 `engine/react_core.py` 的 `run_tool_reflect` 中持 5 个 function-calling 工具，自主决定调用顺序：

| # | 工具名 | 做什么 | 何时调用 |
|---|---|---|---|
| 1 | `parse_patient_reply` | 从患者回复提取 NRS / 睡眠 / 用药 / 副作用 | **每轮必调**，收到任何患者回复首先调用 |
| 2 | `query_patient_history` | 查询近 7 天历史随访记录 | 需要了解疼痛趋势或补充背景时 |
| 3 | `calculate_risk_score` | 计算风险评分（0-20）和等级 | NRS 提取到后，了解风险趋势调整语气 |
| 4 | `escalate_alert` | 推医生预警通知 | 风险 ≥ 8 分或紧急情况 |
| 5 | `finalize_followup` | 结束随访，输出摘要结束语 | 四项信息全部收齐后，或患者明确结束 |

**护栏机制**：LLM 调用 `finalize_followup` 后系统检查四项（疼痛/睡眠/用药/副作用）是否都收集齐了——没齐就拦截，让 LLM 针对缺失项生成追问。硬上限 20 轮。

---

## 四、对话流程（手动演示）

```
前端 WebSocket "demo:simulate_reply"
  → routes/ws.py → FollowupService.on_patient_reply()
    → _react_reflect → engine/react_core.run_tool_reflect()
      → LLM 调用工具（parse → risk → 生成追问）
    → 护栏检查 should_end + missing_items
    → _send_question_text → WebSocket 推回前端显示
```

自动患者走 `agents/execution.py` LangGraph 循环，复用同一 ReACT 引擎。

---

## 五、常用命令与环境变量

> 首次拉取与运行请见文首「快速开始（拉取代码与运行）」。

### 后续常用命令

```bash
# 后端运行
python app.py

# 新增包
pip install 包名

# 导入 PDF 到知识库
python -m knowledge.ingest

# 查看向量库状态
python -m knowledge.ingest --status
```

### 环境变量（.env）

```ini
LLM_API_KEY=sk-xxxx          # DeepSeek / Qwen 等 OpenAI 兼容 Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
DB_HOST=127.0.0.1            # MySQL 地址
ES_HOST=http://127.0.0.1:9200 # Elasticsearch 地址
DEMO_TODAY=2026-07-29        # 可选：固定演示日期
```

未配置 Key 时自动降级为关键词/模板匹配，Demo 不中断。

---

## 六、RAG 知识库

### 文档加载流程

```
knowledge_base/raw/*.pdf / .txt
       │
       ▼
unstructured.partition.auto.partition()
   ├── 自动识别文件类型（PDF / TXT / MD / DOCX）
   ├── 文本型 PDF → pdfminer 提取文字
   ├── 扫描型 PDF → OCR 识别（chi_sim+eng）
   ├── 表格自动检测
   └── 元素分类（Title / NarrativeText / ListItem / Table）
       │
       ▼
splitter.py → 按中文标题层级切分 → 800 字/块，重叠 100 字
       │
       ▼
embeddings.py → OpenAI 兼容接口（Gitee AI 云端 bge-m3）→ 1024 维向量
       │
       ▼
es_store.py → Elasticsearch
```

### 向量库

| 后端 | 说明 |
|------|------|
| Elasticsearch 8.x | 唯一后端，支持 BM25+kNN+RRF 混合检索 |

### 当前语料

`knowledge_base/raw/` 下包含 18+ 个疼痛领域中文指南/共识 PDF（带状疱疹后神经痛、癌痛、神经病理性疼痛等），按 `consensus/` 和 `guidelines/` 子目录分类。

---

## 七、技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Socket.IO (ASGI) |
| LLM | OpenAI 兼容接口（DeepSeek / Qwen），via LangChain |
| Agent 编排 | LangGraph StateGraph |
| Function Calling | ChatOpenAI.bind_tools() |
| 文档解析 | Unstructured（自动识别格式 + OCR） |
| 向量库 | Elasticsearch（BAAI/bge-m3 embedding） |
| Embedding | OpenAI 兼容接口（当前 .env 指向 Gitee AI 云端 bge-m3，1024 维）；代码支持本地 bge-m3 / SiliconFlow / 阿里百炼 |
| Reranker | Gitee AI 云端 bce-reranker-base_v1（本地 bge-reranker-large 可选） |
| 数据库 | MySQL（21 表，库名 pain-followup） |
| 包管理 | uv |
| 前端 | Vue 3 + Pinia + Tailwind CSS + Vite |
| 实时通信 | Socket.IO (server + client) |

---

> 演示数据均为算法模拟生成，不含真实患者隐私信息。
