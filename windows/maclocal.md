这次增强旨在支持不会用命令行的人在window或者mac上运行一个本地的前端和后端，这有很显著的好处：
1. 局域网内访问terminal
2. 文件系统就在本地，安全，随时访问，可以使用各种其他工具协助编辑
3. 不需要服务器，不需要域名
4. 通过微信分发服务，天然相当于一个前端
5. 超简单部署和使用

那么有以下流程的改动：

1.启动
启动时，用户不会自己填入环境变量，我们需要有一个windows或mac原生的启动ui。
ui内，用户至少要填写claude或是oai的一个key。在侧面给予对应的申请链接。

以及tavily的key（搜索）

未填写oai的key将无法享受图片生成 视频生成 语音相关功能。

简单接口测试是否通顺，对话 搜索 图片生成 语音功能是否支持，并且提醒用户。

2. 识别用户的环境（或者打包我们的python和build好的前端进去？这个可能更合理，然后用户在对应目录的地方使用python和pip）
使用我们的原生前后端实现，全平台一个版本的代码。
这里你要看看我的方式是否合理

3. 识别可用port，我们的前后端分别占用，并且前端连到后端选择的端口

--如果有旧的jellyfish bot的前后端占用，要杀掉
退出时要杀掉占用

4. 用户选择一个workspace，所有的文件都会存在那个目录下的新目录中（data和users）【暂时不做】

5. setting general里新增 oai key 和 claude key 和tavily key的填写位置
对于admin和他的所有agent，优先使用他设置的key。
他没有设置 再看环境变量里是否设置了。
如果也没有，那么提醒用户设置，给出弹窗直接到setting里的general。

## 决策记录
- 启动器：Tauri (Rust + WebView)，分发为单 .exe / .dmg
- Python：Embedded Python，首次启动 pip install 依赖
- Express 层：保留，前后端走同一个路径
- Per-Admin Key：Admin 的所有 Agent/Subagent/Consumer Agent 都用该 Admin 的 key
- 多 Admin：每个 Admin 可以有不同的 key
- 存储：`users/{user_id}/api_keys.json`，AES-256-GCM 加密
- 旧实例检测：端口 + 进程名，弹确认框
- Workspace：暂不支持自定义，安装目录即 workspace

## 实施阶段

### Phase 1: Per-Admin API Key ✅ 已完成
- 后端加密存储 (encryption.py + user_api_keys.py)
- api_config.py 全链路支持 user_id 参数
- agent.py / consumer_agent.py / ai_tools.py / web_tools.py / tools.py / subagents.py / voice/router.py / scripts.py / models.py 全部适配
- settings_routes.py 新增 4 个 API 端点
- 前端 GeneralPage.tsx 新增 API Keys 设置卡片
- 前端 ApiKeyWarning.tsx 无 Key 弹窗提醒
- .cursorrules 更新

### Phase 2: 端口自动检测 + 启动脚本优化 ✅ 已完成
- launcher.py 跨平台启动器（旧实例检测 + 自动端口 + 双进程管理 + 干净退出）
- server.js 支持 FRONTEND_PORT / API_TARGET 环境变量
- start_local.sh (Mac/Linux) + start_local.bat (Windows) 快捷脚本
- start.sh (Docker) 支持 BACKEND_PORT / FRONTEND_PORT
- .cursorrules 更新
### Phase 3: Tauri 原生启动器 UI ✅ 已完成
- 项目目录 `tauri-launcher/`（Tauri v2 + Rust + WebView）
- `src-tauri/Cargo.toml`：tauri 2、tauri-plugin-shell/dialog/opener、reqwest、tokio、serde、open、libc(unix)
- `src-tauri/tauri.conf.json`：窗口 720×580、NSIS 安装器 (Windows)、macOS 最低 10.15
- `src-tauri/src/lib.rs`：8 个 Tauri Command
  - `detect_environment`：检测 Python/Node.js/项目文件
  - `load_env_config` / `save_env_config`：读写 .env（保留注释和未知键）
  - `test_api_key`：HTTP 测试 OpenAI/Anthropic/Tavily 连通性
  - `start_jellyfish`：调用 launcher.py 子进程（自动选端口 + --skip-check）
  - `stop_jellyfish`：SIGTERM (Unix) / kill (Windows) 停止
  - `get_status`：轮询进程 + 端口存活状态
  - `open_in_browser`：打开浏览器到前端地址
- `dist/index.html`：自包含单页面启动器 UI
  - 水母品牌风格（深色 #0f0f17、粉 #E89FD9、紫 #8B7FD9、青 #5FC9E6）
  - 环境检测卡片（Python/Node/项目文件就绪状态）
  - API Keys 表单（Anthropic/OpenAI/Tavily + 测试按钮 + 申请链接）
  - 控制面板（启动/停止 + 后端/前端状态指示 + 浏览器打开）
  - 2s 轮询状态更新
  - mock invoke fallback 支持浏览器直接预览
- 构建：`cd tauri-launcher && npm install && npm run build`
- 产物：Windows .exe (NSIS)、macOS .dmg

### Phase 4: 打包分发 + 持续发版 ✅ 已完成
- `tauri-launcher/scripts/build.py` — 一键打包脚本
  - 下载嵌入式 Python (python-build-standalone 3.12.7) + Node.js 20.18.0
  - 暂存项目文件到 `bundle-resources/`（app/、frontend dist、config/、launcher.py、requirements.txt）
  - 预装 pip 依赖到嵌入式 Python
  - 调用 `npx tauri build` 生成安装包
  - 支持 `--version`、`--clean`、`--no-pip`、`--no-frontend`、`--stage-only`、`--target`
  - 下载缓存在 `.cache/`，重复构建免下载
- `tauri-launcher/scripts/version.py` — 版本管理
  - `show`：显示当前版本
  - `bump patch/minor/major`：自动递增
  - `set X.Y.Z`：显式设置
  - `tag [--push]`：创建 git tag
  - 同步更新 `tauri.conf.json`、`Cargo.toml`、`package.json`
- `tauri.conf.json` — 新增 `bundle.resources` 配置，映射 bundle-resources/** 到安装目录
- `lib.rs` — 9 个 Tauri Command（新增 `install_pip_deps`）
  - 资源感知路径：优先检测 Tauri resource 目录（打包模式），fallback 开发模式
  - `find_bundled_python` / `find_bundled_node`：先查 resources/python、resources/node，再查系统
  - `start_jellyfish` 设置 `JELLYFISH_PYTHON` / `JELLYFISH_NODE` 环境变量
  - `detect_environment` 返回 `python_bundled` / `node_bundled` / `first_run` 字段
- `launcher.py` — 新增 `_resolve_python()` / `_resolve_node()`，读取 `JELLYFISH_PYTHON` / `JELLYFISH_NODE` 环境变量
- `.github/workflows/release.yml` — CI/CD 自动构建
  - 触发：push tag `v*` 或手动 workflow_dispatch
  - 三平台矩阵：macOS-arm64 (macos-14)、macOS-x64 (macos-13)、Windows-x64 (windows-latest)
  - Rust + cargo cache、runtime download cache
  - 产物上传 + 自动创建 GitHub Release (draft)
- `dist/index.html` — UI 增强
  - 环境检测显示「内置/系统」标签
  - 首次运行提示横幅
- `tauri-launcher/.gitignore` — 排除 bundle-resources/、.cache/、node_modules/、target/

### Phase 5: 启动 Debug 修复 ✅ 已完成
本次修复了打包后 App 启动时遇到的 4 个问题：

1. **`withGlobalTauri: true` 缺失**（根因）
   - 现象：点击「启动 JellyfishBot」完全没有反应
   - 原因：Tauri v2 默认不暴露 `window.__TAURI__`，JS 代码走了 `mockInvoke`（模拟函数），根本没调用 Rust 后端
   - 修复：`tauri.conf.json` 的 `app` 节点新增 `"withGlobalTauri": true`

2. **Node 路径未转绝对路径**
   - 现象：`FileNotFoundError: No such file or directory: './node/bin/node'`
   - 原因：`launcher.py` 的 `_resolve_node()` 返回相对路径，但 `start_frontend()` 将 `cwd` 切换到 `frontend/` 子目录后相对路径失效
   - 修复：`_resolve_python()` 和 `_resolve_node()` 返回 `os.path.abspath(custom)`

3. **`server.js` CommonJS vs ESM 冲突**
   - 现象：`ReferenceError: require is not defined in ES module scope`
   - 原因：`package.json` 设置了 `"type": "module"`（Vite 需要），但 `server.js` 使用 `require()` 语法
   - 修复：`server.js` 改写为 ESM 语法（`import from`），同时补 `express` 和 `http-proxy-middleware` 到 `dependencies`

4. **Express 5 通配符语法变更**
   - 现象：`PathError: Missing parameter name at index 1: *`
   - 原因：`npm install express` 安装了 v5.2.1，不再支持 `app.get('*', ...)`
   - 修复：改为 `app.get('/{*path}', ...)`

### Gatekeeper 签名说明
- Tauri 构建后的 .app 需要 ad-hoc 签名才能在 macOS 上正常运行
- 修改 Resources 内文件后需重新签名：`codesign --force --deep --sign - /Applications/JellyfishBot.app`
- 生产发布需 Apple Developer Certificate 签名 + 公证（notarize）

### 发版流程
```bash
cd tauri-launcher

# 1. 更新版本号
python scripts/version.py bump patch    # 1.0.0 → 1.0.1

# 2. 本地构建测试
python scripts/build.py

# 3. 提交 + 打 tag
git add -A && git commit -m "release: v1.0.1"
python scripts/version.py tag --push

# 4. GitHub Actions 自动构建 macOS + Windows 安装包
# 5. 在 GitHub Releases 审核 draft → 发布
```

### Phase 6: 启动器 UI 重设计 + 超管功能 ✅ 已完成
- UI 重设计：左侧窄导航（72px）+ 3 页切换，品牌视觉对齐主应用（`--jf-*` 变量 + JetBrains Mono）
- Page 1 启动器：环境检测 3 列 pill + 控制区启停 + API Keys 6 字段表单
- Page 2 注册码管理：表格 CRUD + 生成（`JFBOT-XXXX-XXXX-XXXX`）+ 复制 + 删除
- Page 3 账户管理：统计摘要 4 卡 + 用户表格 + 重置密码（sha256 兼容 Python）+ 删除（二次确认）
- Rust 后端新增 7 个 Tauri 命令 + `sha2`/`hex`/`rand`/`chrono` 依赖
- Cargo.toml 依赖新增：sha2 0.10, hex 0.4, rand 0.8, chrono 0.4

### 快速迭代（开发调试）
修改代码后不想完整 build.py 重新打包时：
```bash
# 仅重编译 Tauri 二进制（~50s）
cd tauri-launcher && npx tauri build

# 安装到 /Applications
rm -rf /Applications/JellyfishBot.app
cp -r src-tauri/target/release/bundle/macos/JellyfishBot.app /Applications/

# 同步最新的源码文件到 App（无需重编译 Rust）
cp ../../launcher.py /Applications/JellyfishBot.app/Contents/Resources/
cp ../../frontend/server.js /Applications/JellyfishBot.app/Contents/Resources/frontend/

# 如果改了 node 依赖
rm -rf /Applications/JellyfishBot.app/Contents/Resources/frontend/node_modules
cp -r ../../frontend/node_modules /Applications/JellyfishBot.app/Contents/Resources/frontend/

# 重新签名（改了 Resources 后必须）
codesign --force --deep --sign - /Applications/JellyfishBot.app
```

### 什么改动需要重编译 Rust？什么不需要？
| 改动类型 | 是否需 `npx tauri build` |
|---------|------------------------|
| `lib.rs`（Rust 命令逻辑） | ✅ 必须 |
| `tauri.conf.json`（窗口/配置） | ✅ 必须 |
| `dist/index.html`（启动器 UI） | ✅ 必须（编译时内嵌） |
| `launcher.py`（Python 启动器） | ❌ 直接覆盖 Resources |
| `server.js`（Express 服务） | ❌ 直接覆盖 Resources |
| `app/**`（后端 Python） | ❌ 直接覆盖 Resources |
| `frontend/dist/**`（前端构建产物） | ❌ 直接覆盖 Resources |

### 包大小
- DMG 安装包：**~247 MB**
- 解压后 .app：**~998 MB**
- 组成：Python 运行时 615 MB + Node.js 160 MB + 前端 212 MB + 后端代码 <1 MB
