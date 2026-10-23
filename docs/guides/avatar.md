# 虚拟形象接入：Warudo 与 VRChat

本文说明怎样让 Amaidesu 驱动 Warudo 或 VRChat 里的虚拟形象：主播说话时表情跟着情绪变、嘴跟着声音动，AI 还能自己点头、换站姿、做动作。VTube Studio 接上就能用，不在本文范围；三个平台各自能做什么，见[虚拟形象架构](../architecture/avatar-platform.md)。

> 阅读前提：项目已完成[快速开始](../getting-started.md)的安装步骤，`config/` 下的配置文件已经生成。下文的 `config/avatar.toml` 指项目里的虚拟形象配置文件。

## Warudo

### 它是怎么连起来的

Amaidesu 通过 WebSocket（默认端口 19190）给 Warudo 发一条条"动作名 + 数值"，比如 `happy = 0.8`、`VowelA = 1.0`、`nod_once = 1`。Warudo 这边靠**蓝图**接收：蓝图是 Warudo 场景里的节点图，一个节点收到某个动作名，就去改角色的表情、播一段动画。

所以两边要对上两件事：

- **Amaidesu 发什么名字**：情绪用哪些名字、各占多少，写在 `config/avatar.toml` 的 `[platform.warudo.expression]` 段，下文叫**表情档案**；口型、眨眼、移眼、头部动作、站姿用的是固定名字。
- **蓝图收什么名字、收到后改模型上的哪个形状**：蓝图不用手画，用项目自带的脚本按同一份表情档案生成，名字自然一致。

### 一、在 Warudo 里准备角色

1. 把模型文件（`.vrm`）放进 Warudo 数据目录下的 `Characters` 文件夹。数据目录可以从 Warudo 菜单的"打开数据文件夹"（Open Data Folder）进入。
2. 在场景里选中角色，"来源"选刚放进去的模型；第一次打开 Warudo 时也可以跟着新手引导完成这一步。
3. **保存场景**。后面生成蓝图要从场景文件里读出这个角色，场景文件在数据目录的 `Scenes` 文件夹里，默认叫 `DefaultScene.json`。

### 二、确定用哪种表情档案

先弄清楚模型的表情做在哪里，再选对应的档案和蓝图写法：

| 模型情况 | 表情档案 | 蓝图写法 |
|---|---|---|
| VRM 1.0 模型，标准表情槽（happy、angry、aa、blink 等）都绑好了，VRoid 等工具导出的模型大多如此 | 默认档案，不用改 | `vrm` |
| VRM 0.x 模型 | 默认档案，不用改 | `vrm0` |
| 表情做在模型自定义的形状上，标准表情槽是空的（例如麦麦） | 按模型写一份档案（见下） | `shape` |

拿不准的话，先按 `vrm` 走完后面的步骤，用第五步的试发检查：脸上没变化，再换写法或改写档案。

**写一份自己的档案**：档案说明每种情绪用模型上的哪几个形状、各用多少（0~1）。以麦麦为例，档案在 `src/modules/avatar/platform/warudo/profiles/maimai.toml`，可以整段粘贴进 `config/avatar.toml`，替换原来的 `[platform.warudo.expression]` 段，再照着改成自己模型的形状名：

```toml
[platform.warudo.expression]
# 这些形状生效时暂停自动眨眼（如笑眼，眨眼会把它盖掉）
blink_blocking_keys = ["eye_happy_strong"]
# 说话时这些嘴形先让给口型，说完再恢复
mouth_keys = ["mouth_happy_strong", "mouth_sad_weak"]

[platform.warudo.expression.emotions]
neutral = {}
happy = { mouth_happy_strong = 1.0, eyebrow_happy_weak = 0.8 }
sad = { mouth_sad_weak = 1.0, eyebrow_sad_weak = 0.9 }
# ……其余情绪照此填写
```

- 情绪名只能用项目规定的 17 种（neutral、happy、sad、angry、surprised 等），写错名字启动时会直接报错并列出合法名单；没写的情绪按"无表情"处理。
- 眨眼用固定名 `eye_close`，情绪也可以用它（比如困倦时半闭眼）。
- 口型元音（`VowelA` 等）和移眼（`eye_shift_left` 等）是固定名，不能写进情绪里。

### 三、在 Amaidesu 里启用 Warudo

在 `config/avatar.toml` 的启用名单里加上 `warudo`：

```toml
[platform]
enabled = ["warudo"]          # 同时用 VTS 就写 ["vts", "warudo"]
```

Warudo 的地址端口默认是本机 19190，与 Warudo 默认设置一致，一般不用改。

### 四、生成并导入蓝图

在项目根目录运行（`--mode` 按第二步选，`--scene` 指向第一步保存的场景文件）：

```
uv run python scripts/warudo_blueprints.py --mode shape --scene "<Warudo 数据目录>/Scenes/DefaultScene.json"
```

脚本读取 `config/avatar.toml` 的表情档案，在 `data/warudo_blueprints/` 下生成四个蓝图：

| 蓝图 | 作用 |
|---|---|
| Amaidesu 表情 | 档案里的每个表情名一个入口 |
| Amaidesu 口型 | 元音 A/I/U/E/O |
| Amaidesu 眨眼与移眼 | 眨眼，左右上下移眼 |
| Amaidesu 头部动作与站姿 | 点头一次、点头两次、摇头、歪头、低头看向一边，放松站姿、思考站姿、恢复默认站姿；用 Warudo 自带动画，任何人形模型都能用 |

打开 Warudo 的蓝图页，用"导入蓝图"逐个导入这四个文件。导入成功后，蓝图列表里会出现以"Amaidesu"开头的四个蓝图。

注意：

- **蓝图是按角色的内部编号找角色的。** 删过或重新添加过角色，编号就会变，旧蓝图会找不到角色、什么都不动。换了角色或换了场景，都要先保存场景，再带 `--scene` 重新生成、删掉旧的 Amaidesu 蓝图再导入。
- 改了表情档案、换了模型，也要重新生成并导入。
- 场景里如果还留着别的蓝图在接收同样的名字（比如以前手画的表情蓝图），先删掉，不然两边会一起动。

### 五、试发检查

先不启动 Amaidesu，保持 Warudo 打开，运行：

```
uv run python scripts/warudo_blueprints.py --probe ws://localhost:19190
```

脚本会按顺序把所有情绪、口型、眨眼移眼、头部动作、站姿各发一遍，每项停 1.5 秒（`--hold 3` 可以放慢），终端里会打印正在发哪一项，对着画面看就行。模型上本来就没有的表情槽（比如某些模型没做移眼）不动是正常的。

### 六、启动 Amaidesu

启动后 Amaidesu 会自动连上 Warudo（Warudo 后开也没关系，它会每隔几秒重连）。连上后：

- 主播每说一句话，表情按这句话的情绪自动切换；
- 说话时嘴跟着语音动。这时要关掉 Warudo 自带的音频口型（用麦克风声音驱动嘴的功能），不然两边会抢着控制嘴；
- AI 能调用 `warudo_set_expression`（设置表情）、`warudo_trigger_preset_action`（点头、换站姿等）等工具；WebUI 的工具页能看到 `warudo_*` 工具就说明已经连上。

想让 AI 用自己做的蓝图动作，在 `config/avatar.toml` 的 `[platform.warudo.action_catalog]` 里登记"动作名 = 说明"，蓝图里用"收到 WebSocket 动作"节点按这个名字接收（数据类型选整数）。

### 常见问题

| 现象 | 原因与处理 |
|---|---|
| 导入后什么都不动 | 多半是蓝图里的角色编号和场景对不上：保存场景，带 `--scene` 重新生成，删掉旧的 Amaidesu 蓝图后再导入 |
| 头部动作会动，表情不动 | 档案或写法不对：标准表情槽是空的模型要用 `shape` 写法和自己的档案；VRM 0.x 模型要用 `vrm0` |
| 不确定模型认哪些表情名 | Warudo 日志（`%USERPROFILE%\AppData\LocalLow\HakuyaLabs\Warudo\Player.log`）里出现"Available VRM blendshape clips"这一行时，后面列的就是模型能用的表情名 |
| 嘴的动作很乱 | Warudo 自带的音频口型没关，两边在同时控制嘴 |
| AI 设置视线没反应 | 视线需要场景里另外摆"看镜头 / 看弹幕 / 看手机"的目标物和对应蓝图，脚本不生成；抛鱼同理，还依赖一个第三方插件 |

## VRChat

VRChat 通过 OSC（一种基于 UDP 的消息协议）接收外部程序的消息：Amaidesu 发到本机 9000 端口。VRChat 的 OSC 能写的东西有限：

- **形象作者自定义的参数**：每个形象各不相同，Amaidesu 用它触发动作；
- **头顶聊天框**：与形象无关，Amaidesu 用它显示主播说的话；
- 口型、手势这类内置参数都是只读的，外部程序改不了。

### 一、打开 VRChat 的 OSC

在 VRChat 里打开动作菜单（Action Menu），进入"选项 → OSC"，把 OSC 设为启用。

### 二、在 Amaidesu 里启用 VRChat

```toml
[platform]
enabled = ["vrchat"]
```

地址端口默认是本机 9000，与 VRChat 默认设置一致。

### 三、头顶聊天框

默认开启：主播说的每句话会同步显示在形象头顶的聊天框里，和字幕显示的是同一句。VRChat 限制单条 144 字，超出会截断；两条之间至少隔约 1.6 秒，说得太快时只显示最新的一句。不想要的话，在 `[platform.vrchat]` 里设 `chatbox_enabled = false`。

### 四、动作

默认动作表对应 VRChat SDK 默认模板里的 `VRCEmote` 参数：挥手、鼓掌、指向、欢呼、跳舞、后空翻、悲伤、倒地。其中第 7、8 项（悲伤、倒地）的取值是按菜单顺序推定的，用的时候请对照一下。触发后默认 3 秒把参数写回 0，鼓掌、跳舞这类会循环的动作靠这一步停下。

如果你的形象没有 `VRCEmote`，或者动作做在别的参数上：

1. 在 VRChat 里换上这个形象，VRChat 会把它的参数清单写到本机 `%LOCALAPPDATA%Low\VRChat\VRChat\OSC\<用户ID>\Avatars\<形象ID>.json`；
2. 在清单里找到控制动作的参数名和取值；
3. 在 `[platform.vrchat]` 里改 `emote_parameter`（参数名）和 `emotes`（动作名 = 取值）。

### 五、口型

VRChat 会按麦克风收到的声音自己算口型，任何形象都适用，Amaidesu 不需要额外配置。做法是让语音进到 VRChat 的麦克风：

1. 装一个虚拟声卡（例如 VB-CABLE）；
2. Amaidesu 的语音从系统默认输出设备播出，把系统默认输出设为虚拟声卡的输入端；
3. VRChat 的麦克风选虚拟声卡的输出端；
4. 自己还想听到声音的话，在系统声音设置里对虚拟声卡开启"侦听此设备"。

### 六、表情

VRChat 的表情参数每个形象都不一样，目前 Amaidesu 不驱动 VRChat 的表情；AI 调用设置表情时会如实返回"未应用"。
