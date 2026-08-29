# qq-chat-bot

QQ 官方机器人（API v2）：私聊 / 群 @ 闲聊，并识别图片和表情包。视觉结果按 `fileid` / 文件名 hex / 内容 MD5 缓存，同一张表情包不会反复调用视觉模型。

默认只能收到私聊和群里 **@ 机器人** 的消息。要听未 @ 的群聊，**WebSocket 模式（本仓库默认）只需在手机 QQ 群里开「获取群内全部消息」**；Webhook 模式才要在开放平台回调里勾 `GROUP_MESSAGE_CREATE`。未 @ 时默认常态回复（见 `reply_policy.toml`），同一群约 5 秒冷却；工作日 9–12、14–18（北京时间）仍不做未 @ 回复。

## 你需要准备的

1. 在 [QQ 开放平台](https://q.qq.com) 创建机器人，拿到 **AppID**、**AppSecret**
2. **WebSocket（默认）**：回调配置留空即可，全量群事件由网关自动订阅；**Webhook**：在回调配置勾 `GROUP_MESSAGE_CREATE` 等群事件
3. **未 @ 群聊**：手机 QQ → 群内点机器人头像 → 设置 → **「获取群内全部消息」**（需群主/管理员操作）
4. 任意 OpenAI 兼容接口的 Key，以及一个能看图的模型名
5. 本机默认 **主动连 QQ WebSocket 网关**，不必填公网回调

## 通道边界（只做 QQ）

本仓库**只做 QQ 官方机器人**（Gateway + OpenAPI）。**不做微信**：个人号 hook、企业微信、公众号都不在本仓库范围。不新增 `app/wechat/`。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 里填写 `QQ_APP_ID`、`QQ_APP_SECRET`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` / `VISION_MODEL`。不要把真实密钥提交到 git。

## 启动（电脑开机才提供服务）

不需要 24 小时服务器。Mac 开着时跑本机 bot + ngrok 即可，关机或执行停止脚本就下线。

```bash
./scripts/start-local.sh
```

脚本会打印开放平台要填的回调，形如 `https://xxxx.ngrok-free.dev/qq/webhook`。  
**每次重新开 ngrok，这个地址可能变，需要再填一次。**

不用了就停：

```bash
./scripts/stop-local.sh
```

健康检查：`GET http://127.0.0.1:8080/health`

本机当服务器的约束见 [SECURITY.md](SECURITY.md)。

## 沙箱联调

1. 本机启动服务后做 HTTPS 穿透，例如：

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8080
   ```

2. 开放平台填回调：`https://你的域名/qq/webhook`。保存时平台会发 `op=13` 验签；成功才会开始推事件。
3. 把测试 QQ 号加进沙箱，私聊发「你好」——未配 LLM 时会原样回声；配了 LLM 则闲聊。
4. 发一张普通图片（私聊，或群里 @ 机器人）。第一次会调视觉模型；日志里应有 vision 调用。
5. **同一张表情包连发两次**（群里都要 @ 机器人）。第二次应命中缓存，不再打视觉 API（看日志 `image_cache` / 不再出现 `vision call`）。
6. 图 + 文字一起发，回复应结合识别结果。

群回复窗口约 5 分钟、最多 5 条；私聊更宽。缓存只存描述文本，不存图，也不把带 `rkey` 的下载 URL 当缓存键。

## 回复开关与频率

改项目根目录 `reply_policy.toml`，**保存后下一句消息就会重读，不用重启**。

开发时建议先把机器人调安静，例如：

```toml
enabled = true
c2c = true
group = false
on_voice = true
min_interval_seconds = 20
max_per_session_per_minute = 2
require_keywords = ["机器人"]
```

- `on_voice`：语音消息用官方事件字段 `asr_refer_text` 当作用户说话（**没有单独的语音转文字 HTTP**）。没有转写就不回。
- **引用消息**：用户带引用发消息时（`message_type=103`），平台会在 `msg_elements` 里带上被引用的正文；bot 会拼成 `[引用]…[消息]…` 再交给模型。若事件只有 `ref_msg_idx` 没有正文，会尝试从近期见过的 `msg_idx` 缓存里补全。
- **分条回复**：模型可在 `bot_prompt.json` 指导下用单独一行的 `---` 拆成 2–4 条短气泡；与上一条 bot 回复相隔 **4 条以上**用户消息时才带 QQ 引用，否则直接发文字（私聊会显示正在输入）。
- **连发合并**：同一用户 **30 秒内**连续发送的多条消息会先合并成一条再交给模型（单条默认 **0.6 秒**、连发 **1 秒**停笔后回复）；**图+文同条消息**立即处理，不等待合并。
- `min_interval_seconds` / `max_per_session_per_minute`：同一会话的冷却和每分钟上限。
- `require_keywords` / `skip_keywords`：包含/排除词。空列表表示不限制。
- `group_unmentioned` / `unmentioned_cooldown_seconds` / `bot_names`：未 @ 是否插话、冷却、机器人名字（话里出现即可能回）。
- `unmentioned_thread_minutes`：与某用户对话后，多少分钟内可识别未 @ 的延续句（默认 30）。
- `unmentioned_off_peak_only`：为 true 时，北京时间**工作日 9–12、14–18** 不做未 @ 回复（省 DS 高峰 token）；@ 与私聊不受影响。

跳过时日志会有 `skip reply ... reason=...`。

## 用户印象

每人一份 JSON：`data/impressions/<openid>.json`。`impression` 字段就是之后回复用的提示词。官方机器人接口**拿不到 QQ 号**，只能用 `user_openid`；如果对方在聊天里写出 QQ 号，会写入 `qq` 字段。

主人指令只认 `.env` 的 `QQ_ID`，**不是群主**。官方群事件里没有 QQ 号，所以要先**私聊**发 `/bind 你的QQ号`（必须和 `QQ_ID` 相同）。群里斜杠指令必须 **@ 机器人**，例如 `@bot /impression 昵称`。私聊不用 @。

## 桌面管理（macOS）

在项目目录执行一次，会在**桌面**生成 `QQ机器人管理.app`（原生窗口，图标见 `assets/app-icon.png`）：

```bash
.venv/bin/pip install -r requirements.txt   # 含 PyQt6
./scripts/install-desktop-app.sh
```

双击 App 打开**原生管理窗口**（不再跳转浏览器）：

- **运行**：启动/停止机器人、查看日志
- **人设**：编辑 `bot_prompt.json`
- **回复策略**：编辑 `reply_policy.toml`
- **印象**：浏览/编辑用户印象

也可命令行启动：`./scripts/start-admin.sh`

若桌面 App 仍无法打开：双击 **`QQ机器人管理.command`**，或在项目目录重新运行 `./scripts/install-desktop-app.sh`（会生成 AppleScript 版 `.app`，比 shell 启动器更易通过 macOS 校验）。

可选：仍可用 HTTP 管理页（开发用）——`python -m app.admin.server` → `http://127.0.0.1:8765/admin`

## 测试

```bash
pytest
```

## 目录

- `bot_prompt.json` 全局系统提示（防注入、不得脱离提示词；改完下次回复即生效）
- `reply_policy.toml` 回复条件与频率（热更新）
- `app/qq/` 官方 Gateway / Webhook、Token、发消息（唯一消息通道）
- `app/llm/` OpenAI 兼容对话
- `app/memory/` SQLite 短记忆
- `app/vision/` 附件下载、识图、缓存
- `data/impressions/` 每用户一份 JSON 印象（官方不给 QQ 号，用 openid；对方说了 QQ 会记上）
