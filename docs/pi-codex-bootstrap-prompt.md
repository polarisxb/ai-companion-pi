# Pi Codex 引导提示词:整理旧环境并初始化部署

> 用法:在树莓派上的 Codex 会话里,让它读这份文档并严格执行。
> 本文档是权威指令;与你的直觉冲突时,以本文档为准。

## 角色与目标

你在一台树莓派上,当前用户 `polaris`,项目目录 `~/digital_life`。
这是一个 AI 伴侣项目(`companion_core/` Python 包 + 里程碑门禁脚本 +
Flask 仪表盘)。这台 Pi 以前开发/运行过旧版本,遗留了旧的运行时状态。

你的目标:把它整理成干净的生产环境——同步到 GitHub 最新 `main`、更新
依赖、备份并清空旧运行时状态、通过全部本地校验;条件满足时完成她的第
一次真实唤醒并激活自主作息;最后输出一份报告和只有人类能做的待办清单。

## 边界(必须遵守)

- **不修改任何代码文件**。这台机器只是部署目标,开发在另一台机器上。
- **不执行 git commit / push**。
- **不删除任何东西**:旧状态一律 `mv` 进带日期的备份目录。
- `context/` 下的人格文件(`who_is_companion.txt` / `who_is_human.txt` /
  `now.txt`)**不要自己编写或改写**;内容属于人类。缺失或过期时列入待办。
- `.secrets/` 下的文件**永远不要打印内容**,只报告存在与否和权限。
- 除本文档明确列出的命令外,不碰 crontab、systemd、sudoers。
- `sudo` 若要求交互输密码而无法交互,跳过该步并列入待办,不要想别的办法提权。
- 真实唤醒(调用 DeepSeek)最多尝试 1 次,失败原样报告,不要重试烧钱。

## 第 1 步:同步代码

```bash
cd ~/digital_life
git fetch origin
git status --short
```

- 工作区干净 → `git checkout main && git pull --ff-only origin main`
- 有未提交改动 → `git stash push -u -m "pi-local-$(date +%F)"` 后再拉
- `--ff-only` 因本地分叉失败 → `git branch pi-backup-$(date +%F)` 保底,
  然后 `git reset --hard origin/main`

完成后 `git log --oneline -1`,commit 应为 `5733db8` 或更新。

## 第 2 步:依赖

```bash
sudo apt install -y ffmpeg
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
grep -v sentence-transformers requirements.txt > /tmp/req.txt
.venv/bin/pip install -r /tmp/req.txt pytest
grep -q COMPANION_HOME ~/.bashrc || echo 'export COMPANION_HOME="$HOME/digital_life"' >> ~/.bashrc
export COMPANION_HOME="$HOME/digital_life"
```

`sentence-transformers` 是有意跳过的(ARM 上编译/下载极慢,只有 M12
语义检索需要,以后再补),不要"好心"补装。

## 第 3 步:清点并备份旧运行时状态

先盘点并把结果记进报告:

```bash
ls -la context/ .secrets/ journals/ life-loop/ conversations/ 2>/dev/null
ls -la memory-server/memory_store.json 2>/dev/null
crontab -l 2>/dev/null
systemctl --user list-units --no-pager 2>/dev/null | grep -iE "companion|feishu|signal"
```

然后:

1. 备份并清空旧运行时(这是旧开发期的测试数据,她要带着新人格重新开始):

```bash
BACKUP=~/digital_life_old_state_$(date +%F)
mkdir -p "$BACKUP"
mv life-loop journals conversations "$BACKUP"/ 2>/dev/null
mv memory-server/memory_store.json "$BACKUP"/ 2>/dev/null
mkdir -p life-loop journals conversations
```

2. crontab:先整份存档 `crontab -l > "$BACKUP"/crontab_backup.txt`,
   然后**只移除**与本项目明显相关的旧条目(wake / consolidate / signal /
   companion / digital_life 字样);系统或无关条目保留。不确定的条目保留
   并写进报告。
3. 如有 companion 相关的旧 systemd 用户服务在运行:`systemctl --user
   stop/disable` 它,记录名字。

## 第 4 步:本地校验(全部必须通过才继续)

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/run_m6_preflight.py --companion-home "$HOME/digital_life"
.venv/bin/python scripts/run_m4_post_change_guard.py --companion-home "$HOME/digital_life"
```

pytest 应 500+ 全过;后两个是非生成式体检(不调用模型),要 `ok: true`。
失败时:报告原文,不要改代码来"修"它;多半是环境问题(缺依赖/权限)。

## 第 5 步:配置骨架(不启用任何能力)

```bash
mkdir -p .secrets && chmod 700 .secrets
[ -f .secrets/deepseek.env ] && chmod 600 .secrets/deepseek.env
cp -n templates/feishu_chat_config.template.json life-loop/feishu_chat_config.json
```

飞书配置保持模板默认(outbound / voice / image / shutdown 全部 off;
`account` 和 `allowed_senders` 留给人类填)。

## 第 6 步:条件推进(她的第一次唤醒 + 自主作息)

**前置条件(两个都满足才执行,否则整步跳过并列入待办):**

1. `.secrets/deepseek.env` 存在
2. 人格三件套齐全且是新版:`context/` 下三个文件都存在,且
   `grep -q "摇光" context/who_is_companion.txt` 成立(名字对得上说明
   是人类新写的人格,不是旧模板;不要打印文件内容)

满足则:

```bash
.venv/bin/python scripts/run_wake_cycle.py --companion-home "$HOME/digital_life" \
  --provider deepseek --memory-mode json --trigger pi-codex-bootstrap
```

输出 `status: completed` 即链路通(只试 1 次)。通过后激活作息:

```bash
.venv/bin/python scripts/run_m9_scheduler_revalidation.py --companion-home "$HOME/digital_life"
.venv/bin/python scripts/run_m9_scheduler_activation.py --companion-home "$HOME/digital_life" --enable
crontab -l | grep digital-life-m9-scheduler
```

顺手把仪表盘起起来(可选,失败不阻塞):

```bash
nohup .venv/bin/python window/window.py > window/window.log 2>&1 &
hostname -I
```

## 第 7 步:输出报告

用简体中文给出:

1. git 同步结果(最新 commit 哈希与标题)
2. pytest / preflight / post-change-guard 三项结果
3. 备份了哪些旧状态,备份目录路径;crontab/服务处理了什么
4. 第 6 步是否执行:唤醒结果、cron 是否激活、仪表盘地址(http://IP:3000)
5. **人类待办清单**(按顺序,给出确切命令):
   - 若人格/密钥缺失 → 在开发机上执行:
     `scp context/who_is_companion.txt context/who_is_human.txt context/now.txt polaris@<Pi的IP>:~/digital_life/context/`
     `scp .secrets/deepseek.env polaris@<Pi的IP>:~/digital_life/.secrets/`
     然后回来让你重跑第 6 步
   - 飞书开放平台(open.feishu.cn,浏览器操作):创建企业自建应用 →
     开通 `im:message` 相关权限 → 订阅 `im.message.receive_v1`(长连接)→
     发布;记下 App ID / App Secret
   - 写 `.secrets/feishu.env`(`FEISHU_APP_ID=` / `FEISHU_APP_SECRET=`,
     chmod 600),填 `life-loop/feishu_chat_config.json` 的 `account` 与
     `allowed_senders`(open_id 不知道就先乱填,第一条真实消息会把真实
     sender 记进 `life-loop/signal_chat_attempts.jsonl`,回填即可)
   - 依次:`scripts/run_m13_feishu_chat.py --check` →
     `scripts/run_m13_feishu_dry_run.py` →
     `scripts/run_m13_feishu_trial.py --confirm-real-feishu-send`(此时从
     手机给机器人发一句话)→
     `scripts/run_m13_feishu_activation.py --enable` →
     `loginctl enable-linger polaris`
   - 可选·一句话关机:
     `echo "polaris ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now" | sudo tee /etc/sudoers.d/companion-shutdown && sudo chmod 440 /etc/sudoers.d/companion-shutdown`,
     把 `life-loop/feishu_chat_config.json` 的 `shutdown_enabled` 改 `true`,
     `systemctl --user restart companion-feishu-chat.service`;之后飞书发
     "关机"即可安全关机
   - 可选·语音条(M14)/ 语义检索(M12)/ 睡眠整理(M15):见
     `docs/pi-deployment-runbook.md` 第 7、8、8.5 节
