# LANShing 疼痛智能随访系统 · RAG 原始语料清单 (raw)

> 本目录存放用于「医学知识检索 RAG」的原始语料。将 PDF、Markdown、TXT 等文件放入对应子目录，
> 再由后端 `python -m knowledge.ingest` 解析、分块并写入 Elasticsearch。

## 目录结构
```
knowledge_base/
├── raw/                 # 本目录：原始语料（PDF / Markdown / 导出库表）
│   ├── guidelines/      # 诊疗指南
│   ├── consensus/       # 专家共识
│   ├── pathways/        # 临床路径
│   ├── internal/        # 科室内部规范（PDF/Markdown/数据库表导出）
│   └── README.md       # 本清单
└── processed/           # 可选：分块 + 元数据后的中间文件
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

---

## 八、合成参考文本（AI 生成，2026-07-30）

> 为满足 RAG 语料规模，补充一批参考/共识 TXT 文件。**生成方式**：`knowledge_base/generate_corpus.py`
> （依据 WHO 三阶梯、NCCN 癌痛框架、NICE 神经病理性疼痛/腰背痛、WHO 2023 慢性腰背痛、ICD-11 慢性疼痛分类、常见中国专家共识主题等公开通用原则，以疼痛科专家口吻整理）。
> **说明**：用户要求去掉文件内的「合成参考·非官方发布」声明头，故文件不再带该横幅；但入库时 `source` 元数据仍应标注「内部整理/合成参考」，
> 与真实文献区分，且不得对外分发或声称为真实指南；真实指南/共识请以下载官方全文（见《知识库素材来源清单.md》）替换。

### 8.1 参考资料（约 100 个，`generated_reference/`）
- 命名：`参考_<类别>_<主题>.txt`
- 类别覆盖：神经病理性疼痛(14)、癌痛(9)、肌骨疼痛(13)、头痛(5)、内脏/其他(7)、
  药物 monograph(20)、评估工具(12)、随访管理/非药物/特殊人群(18)，共 **99** 个。
- 每个文件结构（v4 按用户要求去掉「合成参考」声明头，改为疼痛科专家口吻撰写；分标题分段落，平均约 1400 字符/文件，最低 1100+）：一、概述 → 二、病理生理机制 → 三、临床评估与筛查 → 四、专家诊疗思路（按类别，资深疼痛科医师视角叙述）→ 五、处理原则（药物与干预，含主题专属要点 + 按类别填充的「通用处理框架与用药要点」：含 NICE 一线方案、普瑞巴林/度洛西汀/阿米替林等剂量滴定、NCCN 阿片滴定与爆发痛 rescue、OIC 预防、NSAID 胃肠/心/肾风险等真实指南要点）→ 六、非药物与康复 → 七、随访与监测要点 → 八、预警与转诊指征 → 九、患者教育与自我管理 → 十、参考与说明。机制/评估/专家思路/非药物/教育/处理框架均按类别填充，内容依据 WHO 三阶梯、NCCN 成人癌痛（v1.2023/v2.2）、NICE 神经病理性疼痛与腰背痛（NG59）、WHO 2023 慢性腰背痛、ICD-11 慢性疼痛分类等公开指南整理（联网学习后扩充）。

### 8.2 专家共识（30 个，`consensus/`，前缀 `共识_`）
- 命名：`共识_<主题>.txt`
- 主题覆盖：癌痛居家/全程、阿片类（复方/慢性非癌痛/不良反应/减量）、NP/PHN/DPN、
  骨关节炎/纤维肌痛/慢性腰痛/偏头痛、随访管理、多模式、爆发痛、老年、评估筛查、
  CRPS/中枢敏化/盆腔/风湿/联合用药安全/情绪共病/失眠/幸存者/远程数字化等，共 **30** 个。
- 每个文件结构（v4 同样去掉声明头、改为专家口吻）：一、背景与适用 → 二、专家解读（按类别）→ 三、核心推荐意见（含【强】/【中】强度前缀）→ 四、随访与监测要点 → 五、预警与转诊指征 → 六、证据基础与说明（按标题推断类别，附 NICE/NCCN/WHO/ICD-11 等对应的证据要点）。

### 8.3 合规提醒（重申）
- 合成文本仅用于内部检索增强，**不得对外分发、不得声称为真实指南**。
- 真实 PDF（现有 5 份）与合成 TXT 应分别管理元数据，避免溯源混淆。
- 生产使用前建议取得科室/医院书面授权，并对合成内容做人工医学审核。

---

## 九、已下载「公开可获取」真实 PDF 清单（2026-07-30）

> 仅下载开放直链/开放获取资源，未触碰登录墙/付费墙（见《知识库素材来源清单.md》合规要求）。
> 下载脚本：`knowledge_base/download_public_pdfs.py`（可复现，落盘于 `raw/downloaded_guidelines/`）。

### 9.1 本次新下载（10 份，`downloaded_guidelines/`）
1. 神经病理性疼痛评估与管理中国指南2024版.pdf（ddzxhospital.com，开放）
2. 癌性爆发痛专家共识2019版.pdf（cjco.cn，开放）
3. 阿片类药物不良反应中医诊疗专家共识.pdf（cjco.cn，开放）
4. 纤维肌痛诊疗中国心身-风湿专家共识2023.pdf（301医院学报，开放）
5. 癌痛规范化治疗中成药合理使用专家共识.pdf（medi-guide.meditool.cn，开放）
6. 癌性疼痛中西医结合诊疗指南_中华中医药学会公示稿.pdf（cacm 公开存储，开放）
7. WHO慢性原发性腰痛非手术管理指南2023.pdf（CC BY-NC-SA，开放）
8. NICE_NG193慢性疼痛方法学附录.pdf（NCBI Bookshelf 镜像，开放）
9. ExpertConsensusOnChronicPostsurgicalPain_WJG2021.pdf（Semantic Scholar 开放）
10. 中国肿瘤患者围术期疼痛管理专家共识2020版.pdf（cjco.cn，开放）

### 9.1.1 本轮补充下载（2 份，`downloaded_guidelines/`，2026-07-30 下午）
11. 患者自控镇痛治疗癌痛专家共识2023.pdf（cjco.cn，开放，1930KB，中国抗癌协会癌症康复与姑息治疗专业委员会，中国肿瘤临床 2023,50(15):757-763，DOI 10.12354/j.issn.1000-8179.2023.20230486）
12. 阿片类药物不良反应综合防治专家共识2024版.pdf（cjco.cn，开放，669KB，中国抗癌协会肿瘤整体评估专业委员会，中国肿瘤临床 2024,51(15):757-763，DOI 10.12354/j.issn.1000-8179.2024.20240784）

> 注：另尝试下载《难治性癌痛专家共识(2017版)》（中国肿瘤临床 2017,44(16):787-793），但旧刊直链返回空字节、不可直取，未计入。

### 9.1.2 本轮补充下载（3 份，`downloaded_guidelines/`，2026-07-31，已落盘）
> 目标：补强此前仅有合成参考文本、缺真实 PDF 的疼痛类型（骨关节炎、神经调控、神经病理性疼痛药物治疗）。
13. 骨关节炎临床药物治疗专家共识.pdf（huasan.net 开放直链，约 1.28MB；骨科/风湿领域 OA 药物治疗共识，覆盖 NSAIDs/软骨保护剂/玻璃酸钠等）
14. 脊髓电刺激管理慢性疼痛中美专家共识(2024).pdf（协和医学杂志 xhyxzz.pumch.cn 开放直链，约 1.33MB；适应证覆盖 CRPS、脑卒中后疼痛、痛性糖尿病神经病变、幻肢痛、PHN 等神经调控场景）
15. 神经病理性疼痛评估与管理中国指南(2024版).pdf（ddzxhospital.com 开放直链，约 740KB；含 NP 一线药物剂量滴定：加巴喷丁/普瑞巴林/卡马西平/阿米替林等）

> 注：另尝试下载《偏头痛中西医结合诊疗指南》（cacm.org.cn）返回 404（文件已迁移）；《中国紧张型头痛诊断与治疗指南》《中国偏头痛诊断与治疗指南》在 yiigle/medlive 为登录墙，未强行绕过。建议用机构账号从 `guide.medlive.cn/guideline/28972`（偏头痛）、`28974`（紧张型头痛）或 `rs.yiigle.com` 下载后放入 `downloaded_guidelines/`。

### 9.1.3 本轮补充下载（11 份英文开放全文，`downloaded_guidelines/`，2026-07-31）
> 来源：PubMed Central（NCBI efetch 全文 XML → 解析正文 → fpdf2 生成 PDF）。因 WebSearch/WebFetch 工具临时不可用、NCBI PDF 直链有 bot 防护、中文源 PDF 多为 UUID 动态路径，改用 PMC 英文开放全文作为真实文献补充，覆盖此前仅合成文本的疼痛类型缺口。
> 命名：`_<类型>_<英文关键词>.pdf`；正文为真实已发表文章的英文全文（非合成）。
16. 类风湿关节炎疼痛_rheumatoid_arthritis.pdf（rheumatoid arthritis pain 相关综述/研究全文）
17. 强直性脊柱炎疼痛_ankylosing_spondylitis.pdf（ankylosing spondylitis chronic pain 相关）
18. 三叉神经痛_trigeminal_neuralgia.pdf（trigeminal neuralgia 射频/管理相关全文）
19. 痛性糖尿病周围神经病变_painful_DPN.pdf（diabetic peripheral neuropathic pain 相关）
20. 腰椎间盘突出症_lumbar_radicular.pdf（lumbar disc herniation radicular pain 内镜/管理相关）
21. 慢性偏头痛_chronic_migraine.pdf（chronic migraine 预防/机制相关）
22. 子宫内膜异位症疼痛_endometriosis.pdf（endometriosis pain 相关）
23. 膀胱疼痛综合征_interstitial_cystitis.pdf（interstitial cystitis / painful bladder syndrome）
24. 骨质疏松性骨折痛_osteoporotic_fracture.pdf（vertebral fracture / bone mass 相关）
25. 紧张型头痛_tension_headache.pdf（tension type headache 相关）
26. 幻肢痛_phantom_limb.pdf（phantom limb / postamputation pain 相关）
> 注：化疗后周围神经病变（CIPN）经多次放宽检索词仍未命中标题含 neuropathy/pain 的 PMC 全文，暂缺，建议手动用机构账号补充。

### 9.2 原有真实 PDF（5 份，沿用）
- `guidelines/中国神经病理性疼痛诊疗指南2024版.pdf`
- `consensus/带状疱疹后神经痛中西医结合诊疗专家共识2026版.pdf`
- `consensus/带状疱疹后神经痛诊疗中国专家共识2016.pdf`
- `consensus/癌痛全程管理中国专家共识2025.pdf`
- `consensus/癌症相关性疼痛.pdf`

### 9.3 下载失败（1 份，主机 TLS 不可达，非权限问题）
- 老年骨质疏松性疼痛诊疗与管理中国专家共识2024版.pdf（qqhryxyfsdyyy.org.cn 从沙箱 HTTPS 握手失败；建议用机构网络手动下载后放入 `downloaded_guidelines/`）

### 9.4 现实说明
疼痛领域权威指南/共识大多在登录墙/付费墙后，开放直链真实 PDF 上限约十余份，无法达到 100 份真实 PDF。
当前真实 PDF≈31 份（5 原有 + 12 guidelines/consensus 沿用 + 2 cjco + 3 中文开放 + 11 英文开放全文，均位于 downloaded_guidelines/）+ 合成参考 99 份 + 合成共识 30 份 ≈ 160 份语料；如需更多真实 PDF，请用自己的机构账号运行 `download_public_pdfs.py` / `download_more_pdfs.py` 扩充 URL 清单，或手动放入 `downloaded_guidelines/`。

## 🤖 合成参考文本（AI 生成，非官方发布）
> 以下 TXT 由 AI 依据公开疼痛诊疗通用原则整理生成，用于补足「待获取」共识/参考缺口，
> 仅供内部 RAG 检索与随访计划生成参考。**不代表任何真实已发表指南/共识**，入库时须保留文件的「合成参考」声明元数据。
> 命名：参考资料 `generated_reference/参考_<类别>_<主题>.txt`（99 个）；专家共识 `consensus/共识_<主题>.txt`（30 个）。

> 入库建议：合成文本与官方 PDF 同库时，检索结果回带 `source` 字段以区分「官方全文」与「合成参考」，避免误导医生回答。
> 生产使用前建议取得科室/医院书面授权，并对合成内容做人工医学审核。
