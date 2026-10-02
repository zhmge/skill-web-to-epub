# Web to EPUB

把网页文章或网页书直接转换为**内容保真**的 EPUB 3 电子书 —— 自适应排版、语义化目录、内嵌离线资源、内部链接与脚注校验。

> An agent skill that converts web pages or web books into content-preserving EPUB 3 files
> for offline reading, with a bundled deterministic packager and a local structural auditor.

## 功能

- **直接从源网页工作**：不要求先把页面存成离线 HTML
- **内容保真**：正文、标题层级、列表、表格、引用、代码空白、图片与图注、链接、脚注均不静默删除或改写；
  版式可以重排，措辞不动
- **离线可读**：图片、样式表与（需要时的）字体一并内嵌；外部引用链接可以保留，但成书不依赖网络
- **语义化目录**：源站导航不可用时，按语义标题或确认后的阅读顺序生成分层导航；导航项都指向真实存在的 XHTML 与锚点
- **只删行为，不删内容**：仅移除只提供行为（而非可读内容）的脚本，脚本生成的正文与链接会搬进 XHTML
- **确定性打包**：打包脚本从规范化源目录生成 container、OPF、导航文档与可选的 EPUB 2 兼容 NCX，
  `mimetype` 恒为 ZIP 首条且不压缩
- **本地审计**：审计脚本检查 ZIP 与包结构、manifest 与 spine、XML 合法性、内部文件与片段锚点、
  重复 ID、离线资源依赖、CSS 引用、注释引用配对，以及源文件与成书的逐字节比对

## 依赖

- **Python 3.10 及以上**，仅用标准库，无第三方依赖
- **输出格式**：可重排（reflowable）EPUB 3
- **官方 EPUBCheck**：可选。开工前会询问是否需要；需要且本机可用时执行并附完整结果。
  本地审计覆盖面有限，**不替代** EPUBCheck，未跑 EPUBCheck 时报告会明确标注这一口径

## 用法

把源页面链接与目标范围交给 agent，agent 读取 `SKILL.md` 后按工作流执行：确认影响结果的取舍 → 建立规范化源目录 → 打包 → 审计 → 交付。

源目录已按约定准备好时，两个脚本也可单独调用：

```bash
python scripts/epub_package.py SOURCE_DIR OUTPUT.epub [--no-ncx]
python scripts/epub_audit.py OUTPUT.epub [--source-dir SOURCE_DIR] [--report EPUB_AUDIT.txt] [--json]
```

源目录的布局与各字段含义见 `SKILL.md` 的工作流一节。审计脚本发现错误时退出码为 1，可直接用于流水线判定。

安装为 agent skill 时，整个目录放入 skills 目录即可；`SKILL.md` 是入口。

## 需要确认的环节

agent 在以下情形会先提问，不擅自假定：

- 章节顺序，以及侧栏、评论等非正文内容是否收录
- 注释与批注的归属、落点（源站若只用视觉坐标或空锚点定位，会说明映射依据而不猜测）
- 输出取可重排还是固定版式
- 源站的导航与非正文内容是否保留
- 是否执行官方 EPUBCheck

## 产物

一个 EPUB 3 文件，外加一份审计报告：包结构与 ZIP 条目、manifest 与 spine、XML 合法性、内部文件与片段锚点、重复 ID、离线资源依赖、CSS 引用、注释引用配对，以及源文件与成书的逐字节比对计数。报告区分「EPUBCheck 校验」与「本地结构与内容核对」两种口径，未跑 EPUBCheck 时不会声称成书通过了结构认证。

## 已知限制

- 可重排 EPUB 无法保证与网页**像素级一致**；需要固定尺寸时应在开工前说明
- 本地审计不等于 EPUBCheck 通过；`mimetype` 顺序与压缩方式、ZIP 条目安全等结构项由审计覆盖，
  但规范完整性与阅读系统兼容性仍需官方 EPUBCheck
- 注释仅以视觉坐标或空锚点定位时，归属需人工确认后再落笔
- 适合文章与网页书；把已有 epub 改排版、把字幕或视频整理成笔记、只做网页摘要、生成 PDF 或单纯保存网页 HTML 都不在本 skill 范围内

## 项目结构

```
.
├── SKILL.md              给 AI agent 的完整指令入口
├── scripts/
│   ├── epub_package.py   确定性打包：规范化源目录 → EPUB 3
│   └── epub_audit.py     本地结构与保真审计
├── README.md             本文件（给人看）
└── LICENSE               MIT
```

## License

[MIT](LICENSE) © 2026 zhmge
