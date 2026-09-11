# 知译 · PaperBridge

基于 **Qt 6 / PySide6** 的 Windows 学术文献阅读软件。左侧显示原始 PDF，右侧显示 BabelDOC 生成的译文 PDF，支持同步翻页、同步滚动、缩放、文献目录和最近阅读。

## 开始使用

1. 双击 **启动知译.cmd**，或打开 `dist/PaperBridge/PaperBridge.exe`。
2. 点击 **翻译设置**，填入服务地址、模型名称和 API 密钥，再测试连接。支持兼容 `/chat/completions` 的服务。本地 Ollama 可填写 `http://localhost:11434/v1` 和已经下载的模型名称，密钥留空。
3. 点击 **导入 PDF** 或拖入文件。默认英语译为简体中文。
4. 建议先选择 **只翻译当前页**，或自定义页码 `1-2`，检查专业术语与公式后再翻译全文。
5. 完成后点击 **导出 PDF**，保存单语译文、左右并排的双语 PDF，或自动提取的术语表。

本机环境已安装。源码运行方式是 `.venv\Scripts\python.exe main.py`。在另一台电脑使用时需安装 Python 3.10–3.13，然后运行 **安装依赖.cmd**。可执行程序是本项目的桌面入口，翻译运行环境位于项目的 `.venv`，请保留整个文件夹；不要只复制 EXE。

## 排版和翻译策略

- 用 BabelDOC 的页面布局模型识别多栏正文、公式和图表，再在原页面结构内排版译文。
- 公式由引擎识别为受保护的对象或占位符；学术翻译提示明确要求保留公式、引用、数值、单位及标签。
- 内置提示要求先理解科学含义，再按目标语言的学术表达组织句子，保留否定、比较、限定条件和不确定性，不补写结论。
- 默认开启术语提取。可填写研究领域，也可导入 UTF-8 CSV 术语表，表头为 `source,target`。参见 `examples/glossary.csv`。
- 原文件不覆盖。每次翻译保存到独立目录；部分翻译保留所有页面，未选择的页继续显示原文，并明确标注“本页未翻译”。
- 保存前检查译文页数和页面尺寸是否与原文一致。

**不能保证任意 PDF 的格式和公式绝对不变，也不能保证任何模型的译文百分之百准确。** 译文长度不同会造成局部换行、字距或字号调整；复杂嵌套公式、特殊字体、旋转页、表格、图中文字和混合扫描页面仍可能识别失败。软件保留左侧原文用于核对。表格翻译默认关闭，可在设置中启用实验选项。扫描件需要先用外部 OCR 工具生成可提取文本层。

## 文件、密钥与网络

配置和阅读历史放在 `userdata`，生成的译文放在 `userdata/translations`。翻译缓存、模型和字体由 BabelDOC 放在当前 Windows 用户的 `.cache/babeldoc`。处理记录在界面中显示，翻译目录下的 `work` 包含排版和翻译追踪数据。

密钥默认只在本次软件会话中使用。勾选保存后，使用 Windows DPAPI 加密，绑定当前 Windows 用户。密钥通过子进程标准输入传递，不写入命令行、阅读历史和任务说明。

点击开始翻译后，正文和术语会发送到用户配置的服务，原 PDF 不直接上传。云端服务可能计费；本地模型同样需要足够的算力。首次准备模型、字体和分词资源需要网络，此后相关资源可使用本地缓存。

## 验证与维护

运行功能测试：`.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py -v`

运行端到端排版测试：`.venv\Scripts\python.exe -m tests.integration_pipeline`。该测试通过真实 BabelDOC 引擎处理合成论文，用本地固定测试响应替代云端模型；它检查 PDF 生成和排版保护，不代表真实模型的翻译质量，也不会发送用户文献。

打包：使用 PowerShell 执行 `build.ps1`。

如果模型下载失败，可重新运行 `.venv\Scripts\python.exe -m paperbridge.assets`。分词资源支持官方仓库备用下载地址，并按 BabelDOC 内置哈希验证完整性。请求失败时检查服务地址、密钥、模型名、额度和请求速度；翻译过程中可以取消。

技术资料：[Qt PDF 组件](https://doc.qt.io/qtforpython-6/PySide6/QtPdfWidgets/QPdfView.html)、[BabelDOC 项目与命令行说明](https://github.com/funstory-ai/BabelDOC)、[PyMuPDF 文档](https://pymupdf.readthedocs.io/en/latest/)。依赖版本固定在 `requirements.txt`。这些第三方组件保留各自许可证；进一步分发时应一并保留相应许可证和通知。
