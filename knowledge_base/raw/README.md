# PainSmart6.0 随访知识库 · 语料清单 (raw)

> 本目录存放用于「医学知识检索 RAG」的原始语料。建议先在此 `raw/` 存放原始文件，
> 由入库脚本（P0）解析后写入 `../processed/` 与向量库（Chroma）。

## 目录结构
```
knowledge_base/
├── raw/                 # 本目录：原始语料（PDF / Markdown / 导出库表）
│   ├── guidelines/      # 诊疗指南
│   ├── consensus/       # 专家共识
│   ├── pathways/        # 临床路径
│   ├── internal/        # 科室内部规范（PDF/Markdown/数据库表导出）
│   └── README.md       # 本清单
└── processed/           # （待建）分块 + 元数据后的入库文件
```

## ✅ 已下载（可直接用于 RAG）
| 文件名 | 类别 | 页数 | 来源 URL | 状态 |
|---|---|---|---|---|
| `guidelines/中国神经病理性疼痛诊疗指南2024版.pdf` | 指南 | 25 | medtion-image.medtion.com | 文本型，已验证可解析 |
| `consensus/癌痛全程管理中国专家共识2025.pdf` | 共识 | 15 | bookcafe.yuntsg.com | 文本型，已验证可解析 |
| `consensus/癌症相关性疼痛.pdf`（癌症相关性疼痛评估中国专家共识） | 共识 | 6 | bookcafe.yuntsg.com | 文本型，已验证可解析 |
| `consensus/带状疱疹后神经痛中西医结合诊疗专家共识2026版.pdf` | 共识 | 14 | medtion-image.medtion.com | 文本型，已验证可解析 |
| `consensus/带状疱疹后神经痛诊疗中国专家共识2016.pdf` | 共识 | 7 | img.medsci.cn | 文本型，已验证可解析 |

## ⏳ 待获取（需登录 / 机构权限，请自行下载后放入对应目录）
> 以下来源多为版权所有，网页需登录或机构订阅方能下载全文；不建议用脚本绕过付费墙。

### 慢性疼痛诊疗指南
- 中国慢性疼痛诊疗指南（2025版）— 医脉通 `guide.medlive.cn` / MedSci `medsci.cn/guideline`
- 非阿片类镇痛药治疗慢性疼痛病中国指南 — 中华医学杂志 `rs.yiigle.com/cmaid/1476382`
- 中国慢性癌症相关性疼痛诊疗指南（2024版）— 中华疼痛学杂志 `rs.yiigle.com/cmaid/1529020`

### 阿片类药物管理专家共识
- 成人癌痛患者阿片类药物居家管理专家共识（2025版）— 丁香园·用药助手 `drugs.dxy.cn`
- 复方阿片类镇痛药临床应用中国专家共识 — 百度文库 / 中国医师协会疼痛医师分会发布

### 癌痛 / 神经病理性疼痛临床路径
- NCCN 成人癌痛中文版（2025.v1 / 2024.v2）— **官方正版** `nccn.medlive.cn`（勿用第三方镜像）
- 国家卫健委临床路径官方平台 `nhc.gov.cn/zwgk/lclj`（含带状疱疹等疼痛相关病种）

### 科室内部随访规范（内部资产，不外寻）
- 现有 PDF / Word：直接放入 `internal/`
- 现有 Markdown：直接放入 `internal/`
- 数据库表（SQLite）：用导出脚本（见需求文档 v3.0 附录）SELECT 为 Markdown 后放入 `internal/`

## ⚠️ 版权与合规
- 上述指南 / 共识 / 临床路径多为版权所有（© 中华医学会、© NCCN 等）。
- **允许**：内部 RAG 检索增强、医生辅助审阅时引用出处。
- **禁止**：对外分发、公网部署、商业转售、去除版权信息。
- 入库时每个 chunk 务必保留 `source / title / year / 条款编号` 元数据，便于溯源与医生回答引用。
- 若用于生产环境，建议取得科室 / 医院对内部知识库使用的书面授权。
