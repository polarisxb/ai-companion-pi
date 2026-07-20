# Pi 部署运行手册:从零到飞书聊天

Status: 覆盖 M3-M15 全部已交付能力 + 一句话关机
Last updated: 2026-07-20

这份手册把树莓派从空白系统带到"她按自己的作息醒来 + 你在飞书上随时找到她"。
每一步都是幂等的,可以中断后重来;每个激活动作都有对应的暂停/回滚命令
(见文末速查表)。命令默认在 `~/digital_life` 下执行,
`$COMPANION_HOME` 指向该目录。

## 0. 前置条件

- Raspberry Pi(推荐 Pi 5 / 8GB),Raspberry Pi OS 64-bit,Python 3.10+
- 一块外接 SSD,伴侣的家放在上面
- DeepSeek API key
- 一部装了飞书的手机

## 1. 系统与代码

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip ffmpeg
export COMPANION_HOME="$HOME/digital_life"
git clone git@github.com:polarisxb/ai-companion-pi.git "$COMPANION_HOME"
cd "$COMPANION_HOME"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

说明:`requirements.txt` 里的 `sentence-transformers` 在 ARM 上安装较慢,
只有 M12 语义检索需要;赶时间可以先注释掉它单独装其余依赖,之后再补。
`ffmpeg` 只有 M14 语音条需要。

验证代码健康:

```bash
.venv/bin/python -m pytest tests/ -q     # 全部通过才继续
```

## 2. 密钥(只在 Pi 上创建,不进 git)

```bash
mkdir -p .secrets && chmod 700 .secrets
printf 'DEEPSEEK_API_KEY=sk-你的key\n' > .secrets/deepseek.env
chmod 600 .secrets/deepseek.env
```

飞书的密钥在第 5 步创建应用后补:

```bash
printf 'FEISHU_APP_ID=cli_xxx\nFEISHU_APP_SECRET=xxx\n' > .secrets/feishu.env
chmod 600 .secrets/feishu.env
```

## 3. 人格(她是谁)

```bash
mkdir -p context
cp templates/context/who_is_companion.template.txt context/who_is_companion.txt
cp templates/context/who_is_human.template.txt     context/who_is_human.txt
cp templates/context/now.template.txt              context/now.txt
# 逐个填写 [ ] 并删掉注释行;写法原则见 docs/persona-setup.md
```

这一步决定她"AI 味"的浓淡,值得花二十分钟认真写。改动即时生效,
以后随时可以调。

## 4. 生命循环上线(她开始自己醒来)

按门禁顺序执行,每一步的输出 `ok: true` 再进行下一步:

```bash
# 4.1 非生成式体检:代码/清单/边界仍满足部署契约(不调用模型)
.venv/bin/python scripts/run_m6_preflight.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m4_post_change_guard.py --companion-home "$COMPANION_HOME"

# 4.2 手动真实唤醒一次(第一次真正调用 DeepSeek,确认链路通)
.venv/bin/python scripts/run_wake_cycle.py \
  --companion-home "$COMPANION_HOME" \
  --provider deepseek --memory-mode json --trigger pi-redeploy-manual

# 4.3 调度只读复验 -> 激活 cron(她获得自主作息)
.venv/bin/python scripts/run_m9_scheduler_revalidation.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m9_scheduler_activation.py --companion-home "$COMPANION_HOME" --enable
crontab -l   # 应看到一条带 digital-life-m9-scheduler-m9.3 标记的条目
```

默认节律:随机在场窗口、安静时间 00:00-08:00、每天最多 2 次自主唤醒、
输出仅限内部(日志/记忆/请求),不会打扰你。

## 5. 飞书通道(你能找到她)

**开放平台侧**(手机或电脑浏览器,约 15 分钟):

1. [open.feishu.cn](https://open.feishu.cn) → 创建企业自建应用
2. 权限管理 → 开通 `im:message`(接收与发送单聊消息相关权限)
3. 事件与回调 → 订阅 `im.message.receive_v1` → 订阅方式选**长连接**
   (保存时要求本地已有长连接在线,可先跳过,跑 `--check` 通过后再回来保存)
4. 版本管理 → 发布应用;在飞书 App 里找到这个机器人发一句话
5. 记下 App ID / App Secret;你的 open_id 可以在开放平台 API 调试台查,
   或先随便配一个值,第一次收到消息时账本 `signal_chat_attempts.jsonl`
   会记录真实的 sender open_id,回填即可

**Pi 侧**:

```bash
.venv/bin/pip install lark-oapi
# 写入第 2 步的 .secrets/feishu.env
cp templates/feishu_chat_config.template.json life-loop/feishu_chat_config.json
# 编辑:account 填 App ID,allowed_senders/outbound_recipient 填你的 open_id

# 5.1 只读就绪诊断(配置/密钥/SDK/上游冻结)
.venv/bin/python scripts/run_m13_feishu_chat.py --companion-home "$COMPANION_HOME" --check

# 5.2 门禁证据 + 监督试验:先从手机给机器人发一句话,然后
.venv/bin/python scripts/run_m13_feishu_dry_run.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m13_feishu_trial.py --companion-home "$COMPANION_HOME" \
  --confirm-real-feishu-send        # 手机应收到她的回复

# 5.3 激活常驻服务(systemd 用户服务,开机自启)
.venv/bin/python scripts/run_m13_feishu_activation.py --companion-home "$COMPANION_HOME" --enable
loginctl enable-linger "$USER"      # 让用户服务不随登出停止

# 5.4 聊几天后跑观察与冻结
.venv/bin/python scripts/run_m13_feishu_observation.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m13_feishu_freeze.py --companion-home "$COMPANION_HOME"
```

## 6. 她的主动消息(M11,可选)

编辑 `life-loop/feishu_chat_config.json`:`"outbound_enabled": true`。
她醒来时若真有想说的话(唤醒输出的 SIGNAL 段),会经过安静时间/每日预算
(默认 2 条)/过期丢弃(6 小时)的策略后送达你的飞书。之后:

```bash
.venv/bin/python scripts/run_m11_outbound_dry_run.py --companion-home "$COMPANION_HOME"
# 等一个产生了 SIGNAL 段的唤醒之后:
.venv/bin/python scripts/run_m11_outbound_trial.py --companion-home "$COMPANION_HOME" --confirm-real-signal-send
.venv/bin/python scripts/run_m11_outbound_observation.py --companion-home "$COMPANION_HOME"
```

## 7. 按意思回忆(M12,可选,推荐)

```bash
# ARM 上 sentence-transformers 首次安装+下载模型较慢,耐心
cp templates/semantic_retrieval_config.template.json life-loop/semantic_retrieval_config.json
# 编辑:"enabled": true(backend 保持 sentence-transformers)

.venv/bin/python scripts/run_m12_semantic_readiness.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m12_semantic_backfill.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m12_semantic_observation.py --companion-home "$COMPANION_HOME"
```

记忆变多后定期(或每次大量新记忆后)重跑 backfill 同步索引;
删除 `life-loop/semantic_index.json` 即完全回滚到词面检索。

## 8. 语音条与图片(M14,可选)

```bash
# 安装 Piper 与中文语音模型(本地离线 TTS)
.venv/bin/pip install piper-tts
mkdir -p ~/piper-voices && cd ~/piper-voices
# 下载 zh_CN-huayan-medium.onnx 与同名 .json(HuggingFace rhasspy/piper-voices)
cd "$COMPANION_HOME"
```

编辑 `life-loop/feishu_chat_config.json`:

```json
"voice_replies": "companion_choice",
"tts_command": "piper --model /home/pi/piper-voices/zh_CN-huayan-medium.onnx --output_file {output}",
"image_attachments_enabled": true
```

`companion_choice` = 她自己决定哪句话用语音说;想每条都听就改 `always`。

```bash
.venv/bin/python scripts/run_m14_feishu_media_dry_run.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m14_feishu_media_trial.py --companion-home "$COMPANION_HOME" \
  --confirm-real-feishu-send --image creations/art/任意一张.png
# 手机应收到一条语音条(和一张图);之后重启聊天服务使配置生效:
systemctl --user restart companion-feishu-chat.service
```

## 8.5 睡眠整理(M15,可选)

像人脑在睡眠中整理记忆:每隔一段时间,她自己回顾记忆,把相关碎片凝成
摘要、让琐碎的沉入归档、给看走眼的重要度重新打分。断电安全:整理要么
完整发生要么完全没发生;Pi 不常开也没关系,欠下的整理会在下次开机时补上。

```bash
cp templates/consolidation_config.template.json life-loop/consolidation_config.json
# 编辑:"enabled": true(其余保持默认:每 7 天且新记忆 >= 20 条才整理)

.venv/bin/python scripts/run_m15_consolidation_dry_run.py --companion-home "$COMPANION_HOME"
# 先看她会怎么整理(只生成计划,不动记忆):
.venv/bin/python scripts/run_m15_consolidation.py --companion-home "$COMPANION_HOME" \
  --plan-only --ignore-due
# 计划没问题就正式跑一轮:
.venv/bin/python scripts/run_m15_consolidation.py --companion-home "$COMPANION_HOME" \
  --confirm-consolidation
# 之后交给 cron 每天凌晨检查欠账(未到期时自动跳过,不调用模型):
crontab -l | { cat; echo "30 4 * * * cd /home/pi/digital_life && .venv/bin/python scripts/run_m15_consolidation.py --confirm-consolidation >> life-loop/consolidation_cron.log 2>&1"; } | crontab -
```

关机错过凌晨没关系:欠账按"距上次整理的天数"计算,下一次到点检查
(或手动跑一次)就会补上——迟到,不丢失。

后悔某次整理?整体回滚,原始记忆一条不少地回来:

```bash
.venv/bin/python scripts/run_m15_consolidation.py --companion-home "$COMPANION_HOME" --check
# 从 state 里拿到 last_plan_id,然后:
.venv/bin/python scripts/run_m15_consolidation.py --companion-home "$COMPANION_HOME" \
  --rollback <plan_id>
```

## 8.6 一句话关机(可选,很实用)

想拔电源前不用再 SSH 登进去:直接在飞书发一句"关机",她道个晚安,
然后 Pi 自己安全关机,你等灯灭了拔电源就行。

安全边界(重要):关机是**操作员命令,走代码直通道,永不经过模型**——
她自己决定不了关机,也不会被聊天内容"骗"去关机。只有你(白名单里的
open_id)发的、**整条消息正好等于**触发词才会执行;"帮我看看关机脚本"
这种普通对话照常交给她回复。

聊天服务是 systemd **用户**服务,默认没有关机权限,给它开一条精确的
免密 sudo:

```bash
# 换成你的用户名(树莓派默认 pi);用 visudo 写入
echo "$USER ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now" | sudo tee /etc/sudoers.d/companion-shutdown
sudo chmod 440 /etc/sudoers.d/companion-shutdown
# 验证路径与免密是否生效(会立刻关机,测试时想清楚):
# which shutdown   # 确认是 /usr/sbin/shutdown
```

编辑 `life-loop/feishu_chat_config.json`:

```json
"shutdown_enabled": true,
"shutdown_command": "sudo shutdown -h now",
"shutdown_triggers": ["关机", "shutdown", "睡吧"],
"shutdown_ack_message": "好，我先去休息了。你也早点睡，需要我的时候再叫醒我。"
```

```bash
systemctl --user restart companion-feishu-chat.service
# 然后飞书发"关机";收到晚安后,几秒钟内 Pi 会断电
```

关机失败(比如 sudo 没配好)时,她会再发一条"关机没执行成功"提醒你,
机器不会掉——去 `life-loop/signal_chat_attempts.jsonl` 看 `control` 字段。
不想要这个功能就把 `shutdown_enabled` 改回 `false`。

## 9. 仪表盘(手机装成 App)

```bash
# 用既有启动脚本或直接:
nohup .venv/bin/python window/window.py > window/window.log 2>&1 &
```

手机浏览器打开 `http://<Pi局域网IP>:3000` → 添加到主屏幕。
`/life` 页可以看到每个里程碑的证据和运行状态;`/memory-review`
处理偶发的记忆人工复核。

## 10. 验证清单

- [ ] `crontab -l` 有 M9 调度条目;`life-loop/wake_events.jsonl` 在增长
- [ ] 飞书发消息,几十秒内收到回复;`systemctl --user status companion-feishu-chat`
- [ ] `/life` 页 M9/M13 区块全绿
- [ ] (可选项开启后)语音条可播放、图片可见、她偶尔主动发消息
- [ ] `journals/` 里她的日志读起来像"她",不像报告——不像就回去改人格文件

## 暂停 / 回滚速查表

| 能力 | 暂停 | 回滚/停用 |
|---|---|---|
| 自主唤醒(M9) | `touch life-loop/scheduler_pause.flag` | `run_m9_scheduler_activation.py --disable` |
| 飞书聊天(M13) | `touch life-loop/signal_chat_pause.flag` | `run_m13_feishu_activation.py --disable` |
| 主动消息(M11) | `touch life-loop/signal_outbound_pause.flag` | 配置 `outbound_enabled: false` |
| 语义检索(M12) | — | 配置 `enabled: false` 或删除 `life-loop/semantic_index.json` |
| 语音/图片(M14) | — | 配置 `voice_replies: "off"`、`image_attachments_enabled: false` |
| 睡眠整理(M15) | 配置 `enabled: false` | `run_m15_consolidation.py --rollback <plan_id>`(逐次整体撤销) |
| 一句话关机 | 配置 `shutdown_enabled: false` | 删除 `/etc/sudoers.d/companion-shutdown` |
| 全部 Signal/飞书消息 | `touch life-loop/signal_chat_pause.flag`(主开关,两个通道都停) | — |

## 迁移与备份

运行时状态(记忆、日志、账本、状态文件)全部 gitignore,只属于这台 Pi。
换机器时按 `docs/m6-pi-migration-checklist.md` 的保留清单手动搬运,
不要用开发机的运行时状态覆盖 Pi 的。M6.5 的恢复演练脚本
(`run_m6_recovery_drill.py`)可用于备份验证。
