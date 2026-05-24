# PageDoc-AI
Google Chrome sidebar, supports custom large model (LLM) API login.
# Ticai Markdown Pipeline

把文件夹中这批中文财经/概念股资料批量转换为 Markdown 的本地流水线设计与可执行原型。

## 目标

- 递归扫描 `D:\ticai`，保留原目录结构输出 Markdown。
- 按内容类型自动分流：图片/PDF/Office 交给 MinerU，TXT 和伪 `.xls` 文本直接转换。
- 支持断点续跑、失败重试、转换清单、失败日志。
- 默认不修改源文件。



## 推荐架构

```text
D:\ticai
  -> scan manifest
  -> classify
     -> text/txt/tsv-like xls -> builtin markdown table/text converter
     -> pdf/image/docx/pptx/xlsx -> MinerU local CLI or API adapter
     -> true legacy xls -> LibreOffice convert to xlsx -> MinerU
  -> normalize markdown
  -> outputs
     -> markdown/<relative path>.md
     -> artifacts/<relative path>/<mineru raw outputs>
     -> manifest.csv
     -> failures.csv
```

## 为什么这样设计

MinerU 适合作为主解析引擎，尤其是中文图片、PDF、Office 文档中的版面识别和 OCR。  
但你这批数据里有一些 `.xls` 实际是 GBK/ANSI 的制表符文本，直接调用 MinerU 或 Office 反而容易失败；因此软件需要先做文件头嗅探，再决定处理器。

## 安装 MinerU

建议先用单独的 Python 环境安装 MinerU，本项目只负责编排：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U "mineru[all]"
```

安装完成后确认命令可用：

```powershell
mineru --help
```

如果本地安装模型较慢或机器 GPU/内存不足，可以把本流水线的 `--mineru-mode api` 接到 MinerU API 服务；本原型预留了参数，但 API 鉴权字段需要你申请后再补齐。

## 运行

先做一次清单和轻量转换，跳过需要 MinerU 的文件：

```powershell
python .\src\ticai_md_pipeline.py --input D:\ticai --output D:\ticai_markdown --dry-run
```

正式转换。如果没有可用 GPU，建议显式使用 CPU 友好的 pipeline backend：

```powershell
python .\src\ticai_md_pipeline.py --input D:\ticai --output D:\ticai_markdown --mineru-mode local --mineru-backend pipeline
```

只处理失败文件：

```powershell
python .\src\ticai_md_pipeline.py --input D:\ticai --output D:\ticai_markdown --retry-failed
```

## 输出

- `D:\ticai_markdown\markdown\...`：最终 Markdown。
- `D:\ticai_markdown\artifacts\...`：MinerU 原始输出，便于追查 OCR 结果。
- `D:\ticai_markdown\manifest.csv`：每个文件的识别类型、状态、输出路径。
- `D:\ticai_markdown\failures.csv`：失败文件和错误信息。

## 实施建议

1. 先跑 `--dry-run`，确认分类是否合理。
2. 抽样 20 个图片、5 个 Excel、3 个 PDF 做质量检查。
3. 如果本地 MinerU 跑得慢，再申请 API 免费版，把 MinerU 调用从 local 切换到 api。
4. 对最终 Markdown 再做股票代码、公司名、概念标签的结构化抽取。
