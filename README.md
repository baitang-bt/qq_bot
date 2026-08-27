# qq-chat-bot

QQ 官方机器人（API v2）：私聊 / 群 @ 闲聊，并识别图片和表情包。视觉结果按 `fileid` / 文件名 hex / 内容 MD5 缓存，同一张表情包不会反复调用视觉模型。

默认只能收到私聊和群里 **@ 机器人** 的消息。要听未 @ 的群聊，须在开放平台打开「接收所有消息」，此时会推 `GROUP_MESSAGE_CREATE`。未 @ 时只在被点名或像提问时才回，且同一群约 5 秒冷却。

## 你需要准备的

1. 在 [QQ 开放平台](https://q.qq.com) 创建机器人，拿到 **AppID**、**AppSecret**
2. 管理端订阅：`C2C_MESSAGE_CREATE`、`GROUP_AT_MESSAGE_CREATE`；未 @ 群聊还要勾 `GROUP_MESSAGE_CREATE` 并打开「接收所有消息」
3. 任意 OpenAI 兼容接口的 Key，以及一个能看图的模型名
4. 本机默认 **主动连 QQ WebSocket 网关**，不必填公网回调。开放平台里 HTTPS 回调校验失败就先留空，不要再填 ngrok。

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
- `min_interval_seconds` / `max_per_session_per_minute`：同一会话的冷却和每分钟上限。
- `require_keywords` / `skip_keywords`：包含/排除词。空列表表示不限制。
- `group_unmentioned` / `unmentioned_cooldown_seconds` / `bot_names`：未 @ 是否插话、冷却、点名用的名字。

跳过时日志会有 `skip reply ... reason=...`。

## 用户印象

每人一份 JSON：`data/impressions/<openid>.json`。`impression` 字段就是之后回复用的提示词。官方机器人接口**拿不到 QQ 号**，只能用 `user_openid`；如果对方在聊天里写出 QQ 号，会写入 `qq` 字段。

主人指令只认 `.env` 的 `QQ_ID`，**不是群主**。官方群事件里没有 QQ 号，所以要先**私聊**发 `/bind 你的QQ号`（必须和 `QQ_ID` 相同）。群里斜杠指令必须 **@ 机器人**，例如 `@bot /impression 昵称`。私聊不用 @。

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
