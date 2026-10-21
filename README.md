# ShuisTool3 · 水の直播工具箱

一个面向 B 站直播间的**弹幕互动工具箱**：连接直播间弹幕流，提供排队、点歌、歌词、自动刷屏、自动答谢与 **AI 弹幕聊天**等互动能力。

> 服务端 Flask（Python）+ 前端 Vue 3（TypeScript），构建产物托管于 `backend/static`，单端口 5000 即开即用。

## 功能一览

| 页面 | 功能 |
| --- | --- |
| 总览 | 直播间连接状态、实时弹幕/歌词流、快捷入口 |
| 弹幕排队 | 观众发口令排队，支持自定义排队/取消口令、标题样式 |
| 点歌队列 | 弹幕点歌、手动添加、对接洛雪音乐拉取、切歌/清空 |
| 歌词样式 | 实时歌词展示样式配置（OBS 浏览器源） |
| 自动刷屏 | 定时循环发送弹幕、自动答谢（礼物/舰长）、自动回复关键词 |
| AI 聊天 | 弹幕触发 AI 回复：上下文记忆、流式输出、人设定制、用户黑名单、配置推荐 |

- **AI 弹幕聊天**：支持 @前缀 / 关键词 / 全量三种触发方式；A/B/C 三种上下文模式（单条 / 同用户小对话 / 全局浅·深上下文）；可接入本地小模型（llama.cpp / Ollama / LM Studio）或云端 API（OpenAI / DeepSeek / 通义千问 / Kimi）；内置限流、重复去重、敏感词过滤、用户黑名单与「配置推荐」一键套用。
- **弹幕驱动**：基于 `blivedm` 连接 B 站直播弹幕流，低延迟实时推送。
- **OBS 联动**：排队/点歌/歌词均可作为浏览器源直接嵌入 OBS。

## 目录结构

```
shuitool3/
├── backend/            # Flask 后端（入口 main.py，端口 5000）
│   ├── function/       # 业务模块：弹幕解析、发送、AI 引擎（ai_chat.py）等
│   ├── static/         # 前端构建产物（Vite build 输出，运行时托管）
│   └── requirements.txt
├── frontend/           # Vue 3 + TS 前端源码（npm run build 输出到 backend/static）
├── settings/           # 运行时配置目录（本地实际数据：cookies、AI 配置、队列等）
├── settings_template/  # 初始模板目录（首次启动时若 settings 不存在则自动复制初始化）
├── docs/               # 项目结构与运行文档
└── 设计文档.md
```

### settings 与 settings_template 的区别

- `settings/` 是程序**运行时的配置目录**：连接直播间所需的 cookies（登录凭据）、AI 配置、点歌队列、弹幕日志等都存放在这里，由程序自动读写，**包含个人数据**。
- `settings_template/` 是**初始模板**：程序首次启动时，如果 `settings` 目录不存在，会从 `settings_template` 复制一份作为初始配置。
- 仓库中提交的 `settings/` 与 `settings_template/` 均为**干净的初始模板**（不含任何个人配置与登录凭据）；你本地运行后产生的真实配置只在本地 `settings/` 中，不会被提交到仓库。

## 安装

### 环境要求

- Python 3.10+
- Node.js 18+（仅需自行构建前端时需要；直接用已构建产物则不需要）

### 后端

```bash
# 建议使用虚拟环境
python -m venv venv
venv\Scripts\activate        # Windows；macOS/Linux 为 source venv/bin/activate

pip install -r backend/requirements.txt
```

### 前端（可选）

仓库已包含构建产物（`backend/static`），直接运行后端即可使用；如需修改前端后重新构建：

```bash
cd frontend
npm install
npm run build     # 输出到 backend/static
```

## 使用教程

### 1. 启动

```bash
python backend/main.py
```

浏览器打开 <http://127.0.0.1:5000>。

### 2. 连接直播间

1. 打开「总览」页；
2. 在「直播码 / 连接」区域填入直播间相关信息，并**配置 B 站登录 Cookie**（`SESSDATA` 等，用于接收弹幕）；
3. 点击连接，状态变为「已连接」后即可开始互动。

> Cookie 仅保存在本地 `settings/cookies.json`，不会上传或外泄。

### 3. 使用各功能

- **弹幕排队 / 点歌**：观众在直播间发送配置好的口令（如「排队」「点歌 十年」）即自动入队。点歌功能需搭配LX Music使用，https://github.com/lyswhut/lx-music-desktop
- **自动刷屏 / 自动答谢**：在「自动刷屏」页配置定时弹幕内容、答谢话术（礼物 / 舰长 / 醒目留言）。
- **AI 弹幕聊天**：
  1. 打开「AI 聊天」页，开启「AI 自动回复」总开关；
  2. 选择服务商（本地 `llama.cpp` / `Ollama` 或云端 API）并填入 Base URL / 模型，点击「测试连通」；
  3. 选择触发方式（@前缀 / 关键词 / 全量）与上下文模式；
  4. 悬停右上角「配置推荐」按钮，可按场景一键套用推荐配置；
  5. 在「AI 行为」选择人设模板或自定义 System Prompt。

### 4. 接入 OBS

在各功能页复制「浏览器源」URL（如 `http://127.0.0.1:5000/#/display/queue`），添加到 OBS 浏览器源即可将排队/点歌/歌词显示到直播画面。

## AI 聊天快速参考

| 场景 | 推荐配置 |
| --- | --- |
| 本地小模型（1-2B，显存 <1.5G） | llama.cpp + 关闭思考模式 + max_tokens 80~200 + 上下文 B + 预算 1200~3000 |
| 云端 API 大模型 | DeepSeek / Qwen / Kimi + 开启思考 + max_tokens 200~400 + 预算 3000~6000 |
| 同用户小对话 | 上下文模式 B，会话 6~8 条，过期 5~10 分钟 |
| 弹幕刷屏防护 | 关键词触发 + 严格限流 + 触发概率 30%~60% |

详见页面内「配置推荐」面板。

## 常见问题

- **启动后访问不了 5000**：确认端口未被占用；打包版请确认 `_internal` 依赖 DLL 完整。
- **AI 回复为空 / 答非所问**：本地小模型请保持「思考模式 = 自动」（自动关闭思考，避免思考占满字数上限）；System Prompt 已自动注入当前日期，无需手动添加。
- **修改 System Prompt 不生效**：人设切换时按「当前人设」保存定制内容，切换人设不会丢失；保存有 800ms 防抖，修改后请稍候或失焦触发保存。

## License

GNU General Public License v3.0（GPL-3.0，见仓库根目录 `LICENSE`）

> 使用、分发或修改本项目及其衍生作品时，须以 GPL-3.0 开源并保留本声明。
