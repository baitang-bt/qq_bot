# QQ 机器人

QQ 官方机器人（API v2）闲聊程序，附带 macOS 桌面控制台。电脑开着才在线；关机或执行停止脚本即下线。只做 QQ，不做微信。

默认主动连接 QQ WebSocket 网关，不必准备公网回调。Webhook 仅在需要时再开。

## 功能

- **私聊与群聊**：私聊直接回；群里 @ 机器人会回。未 @ 的群消息可按策略插话（见下文）。
- **看图**：普通照片走多模态闲聊。表情包按文件标识缓存描述，同一张不会反复调用视觉模型。
- **发表情**：把 PNG/JPG 放进本地表情库后，模型可在回复里带 `[[sticker:id]]`，机器人分片上传并发出图片。
- **语音**：使用官方事件里的语音转写当作用户说的话；没有转写就不回。
- **引用与分条**：用户引用消息时（`message_type=103`），从 `msg_elements` 或本地 `msg_idx` 缓存取出被引用正文，拼成 `[引用]…[消息]…` 交给模型。机器人自己发出的消息会记住响应里的 `ext_info.ref_idx`，才能识别「引用了我」。平台没有按 id 拉历史消息的接口；进程重启后、未见过的消息且事件没带正文时，引用可能为空。群内要缓存他人消息，需开启「获取群内全部消息」。模型可用单独一行的 `---` 拆成多条短回复。
- **连发合并**：同一用户短时间内连续发送的文字会合并成一条再回复；图文同条则立即处理。
- **人设与印象**：多套 txt 人设，同时只启用一套；每位用户一份印象。
- **回复策略**：开关、频率、关键词、未 @ 规则写在配置里，保存后下一条消息即生效。
- **桌面控制台**：启动/停止、看日志、改 API、人设、回复策略、印象和管理员。

## 使用前准备

1. 在 [QQ 开放平台](https://q.qq.com) 创建机器人，拿到 **AppID** 和 **AppSecret**。接入方式选 **WebSocket**。
2. 准备一个 OpenAI 兼容接口的 Key，以及能看图的模型名。DeepSeek 识图和聊天使用官方 ID `deepseek-flash`。
3. 要听未 @ 的群聊：在手机 QQ 里打开该群的机器人设置，由群主或管理员开启 **「获取群内全部消息」**。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 填写：

| 变量 | 作用 |
| --- | --- |
| `QQ_APP_ID` / `QQ_APP_SECRET` | 开放平台凭证 |
| `QQ_ID` | 主人 QQ 号。群事件里没有 QQ 号，须先私聊 `/bind` 这个号码 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 对话与识图 |
| `VISION_MODEL` | 可留空，默认跟 `LLM_MODEL` |

不要把填了真实密钥的 `.env` 提交到 git。

## 启动与停止

```bash
./scripts/start-local.sh
./scripts/stop-local.sh
```

健康检查：`GET http://127.0.0.1:8080/health`。进程在跑时，开放平台后台应显示在线。

只有改用 Webhook 时才需要穿透：

```bash
START_NGROK=1 ./scripts/start-local.sh
```

回调地址形如 `https://xxxx.ngrok-free.dev/qq/webhook`。ngrok 地址变化后要在开放平台重新填写。本机当服务器的约束见 [SECURITY.md](SECURITY.md)。

## 桌面控制台（macOS）

在项目目录执行一次，桌面会生成 `QQ机器人管理.app`：

```bash
.venv/bin/pip install -r requirements.txt
./scripts/install-desktop-app.sh
```

双击打开原生窗口，页签如下：

| 页 | 做什么 |
| --- | --- |
| 运行 | 启动 / 停止、消息监控日志 |
| Bot 功能 | 群发言模式；打开表情包 / 人设 / 项目根 |
| API | 编辑 `.env` 里的 QQ 与模型配置（密钥打码；留空表示不改）。保存后要在「运行」页重启才生效 |
| 人设 | 多套 txt 人设：左侧选编辑，点「启用」才生效；右侧编单个 txt |
| 回复策略 | 编辑 `reply_policy.toml` |
| 印象 | 浏览、新建、导入用户印象 |
| 管理员 | 谁可以使用斜杠指令 |

人设和回复策略保存后，下一条消息即生效，一般不必重启。也可命令行打开：`./scripts/start-admin.sh`。

## 聊天里怎么用

- 私聊发文字、图片或语音，机器人按人设回复。
- 群里 **@ 机器人** 才会稳定触发。未 @ 时是否插话由 `reply_policy.toml` 决定。
- 工作日北京时间 9:00–12:00、14:00–18:00，默认不回复「既没 @、也没引用机器人」的群消息；@ 和引用不受影响。
- 同一会话有最短间隔和每分钟条数上限，避免刷屏。
- 带引用发送时，机器人能看到被引用的正文。
- 发普通照片会结合画面回复；重复发送同一张表情包会用缓存描述，不再重新识图。

## 主人指令

斜杠指令不经过闲聊模型，直接执行。私聊不用 @；群里必须 **@ 机器人**。

| 指令 | 作用 |
| --- | --- |
| `/bind 你的QQ号` | 私聊绑定。号码须与 `.env` 的 `QQ_ID` 相同，或已在控制台「管理员」里登记 |
| `/impression 昵称` | 把一句昵称或印象记到当前用户 |
| `/贴纸 facepalm` | 按 `stickers.toml` 的 id 发一张本地表情，用来确认上传是否正常 |

绑定之后，群里也可以 @ 机器人使用 `/impression` 等指令。控制台「管理员」里添加的 QQ 号，同样要对方私聊 `/bind` 一次才完成绑定。

## 本地表情

把 PNG / JPG / GIF 放到 `data/stickers/` 即可。文件名会变成 id（例如 `loading.jpg` → `[[sticker:loading]]`）。也可以在根目录 `stickers.toml` 登记描述和标签：

```toml
[[sticker]]
id = "facepalm"
file = "facepalm.png"
tags = ["捂脸", "无奈"]
description = "一只猫捂脸，无奈"
```

模型回复里写出 `[[sticker:facepalm]]` 时，机器人会发出对应图片。上传时按文件魔数原样发送（扩展名可以是 `.jpg`，内容仍可以是 GIF）。目录和清单路径可用 `STICKERS_DIR`、`STICKERS_INDEX_PATH` 覆盖。控制台「Bot 功能」页「打开表情包」会打开该文件夹。

收到的表情包默认会让模型判断是否值得留下（`STICKER_AUTO_LEARN`，默认开启）。值得留下时**原样**写入 `data/stickers/`（不转 PNG/JPEG），同一张图之后直接用本地描述。学习条数上限是 `STICKER_LEARN_MAX`（默认 200）。已经转成静图的旧文件不会自己变回动图，需要从表情包删掉后再让群里发一次重新学。

## 人设

清单在仓库根 `personas.toml`（`active` 指定当前启用的一套），正文在 `data/personas/<id>/*.txt`（与表情包一样不进 git）。每套文件夹可放多个 txt：

- `persona.txt` — 主口吻
- `anti_injection.txt` / `stay_on_prompt.txt` — 每行一条，组装进系统提示
- 其它 `.txt` 按文件名排序，以 `【文件名】` 作为块标题

控制台「人设」页：左侧点选只用于编辑，点「启用」才切换线上口吻（下一条消息生效）。首次若还没有文件夹，会把旧的 `bot_prompt.json` 拆进 `data/personas/0x01/`。路径可用 `PERSONAS_DIR`、`PERSONAS_INDEX_PATH` 覆盖。

每位用户的印象在 `data/impressions/<openid>.json`（官方接口不提供 QQ 号，用 openid；对方在聊天里写出 QQ 号时会记到 `qq` 字段）。

## 回复策略

改 `reply_policy.toml`，保存后下一条消息重读，不用重启。常用项：

- `enabled` / `c2c` / `group`：总开关，以及私聊、群聊是否回复
- `on_text` / `on_image` / `on_voice`：按消息类型开关
- `min_interval_seconds` / `max_per_session_per_minute`：同一会话的冷却和每分钟上限
- `require_keywords` / `skip_keywords`：必须包含或遇到就跳过的词；空列表表示不限制
- `speak_mode`：群发言模式。`auto` 自动（沿用未@规则与工作日高峰）；`all` 全部尝试回复；`mention_quote` 仅 @ 和引用机器人时回复。控制台「Bot 功能」页按钮循环切换，下一条消息生效。
- `group_unmentioned`：自动模式下是否处理未 @ 的群消息
- `bot_names`：话里出现这些名字时，可视为在叫机器人
- `unmentioned_off_peak_only`：自动模式下为 true 时，工作日白天拦截未 @ 且未引用机器人的群消息

## 测试

```bash
pytest
```

## 目录

- `app/qq/` — 网关、发消息、富媒体上传
- `app/llm/` — OpenAI 兼容对话
- `app/vision/` — 下载图片、识图、表情缓存
- `app/stickers/` — 本地表情清单与回复标记
- `app/memory/` — 短期对话记忆
- `app/admin/` — 桌面控制台与可选 HTTP 管理接口
- `app/personas/` — 多人设 txt 清单
- `app/impression/` — 用户印象
- `personas.toml` / `reply_policy.toml` / `stickers.toml` — 人设清单、回复策略、表情清单
- `bot_prompt.json` — 仅作首次迁入 `data/personas/` 的来源
