# QQ Memory Studio

一个**独立的 Windows 桌面 GUI 程序**，用于管理本机 QQ 长期记忆库。它复用 `qq_memory`
的流水线与检索模块（关键词检索、语义检索），但**不依赖、不安装进 AstrBot**，也不会修改
8081 实例或 `qq_memory` 里的现有文件。

## 功能

- **主窗口（1 级）**
  - 状态面板（只读）：当前 QQ 号（自动探测、多账号下拉）、向量检索 API（base_url / model /
    key 是否已配 + 连通性状态灯）、记忆库大小（messages.db 体积、消息条数、会话数、向量条数、
    vecs.f16 体积）、上次增量备份时间 & 下次计划时间。
  - 基础设置（保存到 `config.json`）：QQ 数据目录、QQ 号、记忆库目录、SQLCipher 密钥、
    embedding base_url/api_key/model、AI base_url/api_key/model、是否启用自动召回。
  - 操作按钮：测试 embedding 连接 / 立即增量同步 / 全量重建 / 取消同步 / **选择记忆库目录…** /
    打开记忆库目录 / 打开配置 / **自动抓取密钥…**。
  - **同步前置校验**：点同步前先检查「密钥」与「nt_msg.db 是否定位得到」，不通过时**只在状态区给
    中文原因 + 下一步动作**，不弹模态框、不进入同步；「全量重建」只在校验通过后弹一个确认框。
    因此不会出现「主窗口被禁用像卡死」或「连弹两个框、第二个注定失败」。
  - 底部运行日志区：实时输出同步进度与检索信息。同步用独立子进程执行（可取消、崩溃隔离），
    日志会打印 `已启动同步子进程 PID=xxxx` 以说明这一行为。
  - **首次运行引导**：当记忆库尚未生成时，状态区给「未找到记忆库：<路径>」+ 三步建议，
    并用中性的「messages.db：尚未生成」说明，而不是把它报成“读取异常”；真的读不到
    （损坏 / 权限 / 被占用）才显示可行动的错误信息。检索窗口、AI 助手、会话导出窗口同样口径。
  - **首启/空库**：日志只说「未找到记忆库：<路径>」，不叠加「读取异常」；
    真异常（损坏 / 权限 / 被占用）仍原样显示。
- **2 级窗口 A · 向量数据库浏览与手动检索**：关键词 / 语义两种模式；过滤（会话名、发送人、
  日期范围、只看我发的）；结果表（时间｜会话｜发送人｜来源｜相关度｜内容），双击看全文、
  复制正文、导出 CSV、显示向量库概览（条数 / 维度 / 文件大小）。
- **2 级窗口 B · AI 检索助手**：自然语言问答，AI 通过调用 `search_memory` 工具检索记忆库并
  作答，右侧列出引用的原始消息（点击可在窗口 A 中定位）。窗口顶部可临时覆盖模型 / 接口。

## 目录结构

```
qq_memory_studio/
  app.py                入口（python app.py 启动 GUI；--studio-run-sync / --studio-run-extract-key 为打包后内部子命令）
  ui/main_window.py     主窗口
  ui/search_window.py   检索窗口
  ui/ai_window.py       AI 助手窗口
  ui/workers.py         后台线程（统计 / 语义检索 / 同步 / AI）
  core/config.py        配置读写（首次运行自动生成 config.json）
  core/memory.py        messages.db 只读查询 / 统计 / state.json
  core/semantic.py      vecs.f16 + vectors.db 语义检索（复用 search_semantic 逻辑）
  core/sync.py          调 pipeline 跑增量 / 全量同步（子进程，可取消）
  core/ai.py            OpenAI 兼容接口的对话 + 工具调用
  core/qq.py            QQ 数据目录/账号探测、备份时间、计划任务读取
  pipeline/             随包提供的干净版流水线（来自 astrbot_plugin_qq_memory-1.1.0.zip）
  scripts/make_previews.py  离屏生成界面预览
  previews/             预览图（main.png / search.png / ai.png）
  build_exe.ps1         PyInstaller 一键打包
  scripts/run_build.ps1 打包包装（固定解释器 + 短路径产物目录，规避 MAX_PATH）
  requirements.txt      运行期依赖
```

## 安装与运行

```powershell
# 建议直接用 AstrBot 自带的 venv（已装好 numpy / sqlcipher3 / protobuf）：
$py = "<实例目录>\venv\Scripts\python.exe"
& $py -m pip install -r .\requirements.txt      # 主要就是 PySide6
& $py .\app.py
```

首次运行会在 `qq_memory_studio\config.json` 自动生成配置，默认：

- 记忆库目录 = `<workspace>\qq_memory\memory`（相对 studio 目录推导，不硬编码绝对路径）
- embedding / AI 默认值会尝试从本机 AstrBot 的 `cmd_config.json` 借用（只拷默认值）。

## 界面预览

`previews/` 下的三张 PNG 由 `scripts/make_previews.py` 用离屏（offscreen）方式真实渲染：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
& $py .\scripts\make_previews.py
```

## 打包 exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# 或单文件：
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -OneFile
```

脚本会自动安装 PyInstaller，并以 `--noconsole` 打包，`--add-data` 带上整条 `pipeline`
（同步与密钥抓取都是在子进程里以 `runpy` 方式启动的），并声明 `sqlcipher3 / numpy / protobuf / msgdb`
等 hidden-import。产物在 `dist\QQMemoryStudio\`。

> **冻结后的中文编码**（本版已修）：`--noconsole`(pythonw) 下 `PYTHONUTF8` / `PYTHONIOENCODING`
> 不生效，子进程会按系统 ANSI 代码页(cp936)写 stdout。因此入口脚本与 pipeline 第一个动作就是
> 显式 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，父进程（`core/sync.py`）
> 另有一层“严格 UTF-8 → ANSI 兜底”的解码保护（能识别“GBK 字节恰好是合法 UTF-8”的假成功）。
>
> **子进程入口**：冻结后 `sys.executable` 就是本 exe，**不要**用 `exe -u <script.py>` 调内部脚本，
> 否则会再启动一个完整 GUI；必须走 `--studio-run-sync` / `--studio-run-extract-key` 内部子命令。

## 已知限制

- **同步**需要 SQLCipher 密钥 + 已登录的 QQ 与 `nt_msg.db`；未配置时会先在状态区给出可行动提示
  （填写 `paths.db_key`，或用设置区「自动抓取密钥…」，需管理员 + QQ 已登录），不会弹阻塞框。
- 密钥不做命令行传递（argv 对同机任意进程可见），只走环境变量 `QQMEM_DB_KEY`；`pipeline/sync.py`
  仍保留 `--key` 供调试。
- **AI 助手**需要 OpenAI 兼容、且支持 function calling 的对话模型；未配 AI key 时窗口显示清晰
  提示，不会崩溃。当前本机 `cmd_config.json` 里的聊天 provider 未直接暴露 api_base/api_key，
  故 AI 默认值为占位，请自行填写。
- **语义检索**依赖 embedding key（本机已从 cmd_config 借用 `BAAI/bge-m3`，可用）；若换机后未配
  则退回关键词检索并在界面提示。
- 计划任务「下次时间」仅识别名字含 `qq_memory / qqmem / 记忆` 的任务；检查不到显示「未知」。
