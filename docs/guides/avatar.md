# 虚拟形象接入：Warudo 与 VRChat

本文说明怎样让 Amaidesu 驱动 Warudo 或 VRChat 里的虚拟形象：形象自己眨眼、看来看去，主播说话时表情跟着情绪变、嘴跟着声音动，AI 还能自己点头、换站姿、做动作。VTube Studio 接上就能用，不在本文范围；三个平台各自能做什么，见[虚拟形象架构](../architecture/avatar-platform.md)。

> 阅读前提：项目已完成[快速开始](../getting-started.md)的安装步骤，`config/` 下的配置文件已经生成。下文的 `config/avatar.toml` 指项目里的虚拟形象配置文件。

## Warudo

### 它是怎么连起来的

两条通道：

- **面捕数据**（UDP，默认本机 49983 端口）：Amaidesu 的面捕合成器每秒 30 帧，按 iPhone 面捕 App **iFacialMocap** 的格式发出 ARKit 的 52 个表情系数和头部、眼球转角。Warudo 自带 iFacialMocap 接收器，把 Amaidesu 当成一部正在抓脸的 iPhone，表情、眨眼、眼神、点头、口型都从这里来。
- **蓝图通道**（WebSocket，默认 19190 端口）：Amaidesu 发一条条"动作名 + 数值"，比如 `nod_once = 1`、`marker_tears = 0.8`。Warudo 这边靠**蓝图**接收：蓝图是 Warudo 场景里的节点图，一个节点收到某个动作名，就去播一段动画或改一个形状。只用来做头部动作、站姿、抛鱼和风格化标记（脸红、眼泪这类漫符）。

模型怎么把 ARKit 系数落到自己的形状上，在 Warudo 的面捕设置里调，和真人用 iPhone 面捕时一样。

### 一、在 Warudo 里准备角色

1. 把模型文件（`.vrm`）放进 Warudo 数据目录下的 `Characters` 文件夹。数据目录可以从 Warudo 菜单的"打开数据文件夹"（Open Data Folder）进入。
2. 在场景里选中角色，"来源"选刚放进去的模型；第一次打开 Warudo 时也可以跟着新手引导完成这一步。
3. **保存场景**。后面生成蓝图要从场景文件里读出这个角色，场景文件在数据目录的 `Scenes` 文件夹里，默认叫 `DefaultScene.json`。

### 二、在 Warudo 里接上面捕

1. 在角色的动作捕捉设置里，面部捕捉选 **iFacialMocap**，Warudo 会在场景里加一个 iFacialMocap Receiver（接收器）。接收器配置页里的输入端口默认是 49983，与 Amaidesu 一致。
2. 面部捕捉设置里的形状映射（Configure BlendShapes Mapping）按模型选：模型带 ARKit 的 52 个形状（"完美同步"）选 ARKit；只有 VRM 标准表情的选 VRM。表情做在自定义形状上的模型（例如麦麦），给模型补上 ARKit 形状最省事，或者在这里逐项把 ARKit 系数接到模型的形状上。
3. 关掉 Warudo 自带的音频口型（用麦克风声音驱动嘴的功能）：嘴的开合已经在面捕数据里，两边会抢着控制嘴。
4. Amaidesu 启动后（见第五步）在接收器上点一次校准（Calibrate），校准时形象正看镜头。

头和眼的左右如果反了（Amaidesu 让她往弹幕区看，她看向另一边），勾上接收器的镜像（Mirrored Tracking）。

### 三、在 Amaidesu 里启用 Warudo

在 `config/avatar.toml` 的启用名单里加上 `warudo`：

```toml
[platform]
enabled = ["warudo"]          # 同时用 VTS 就写 ["vts", "warudo"]
```

面捕数据默认发往本机 49983 端口（`tracking_host` / `tracking_port`），蓝图通道默认本机 19190 端口，都与 Warudo 默认设置一致，一般不用改。

模型上画了漫符（脸红、眼泪、汗滴……）的，在 `[platform.warudo.markers]` 登记对应的形状名，哭、害羞、生气时会自动出现：

```toml
[platform.warudo.markers]
tears = "Tears"           # 标记名 = 模型上的形状名
blush = "cheek_red"
```

标记名只能用规定的几种（blush、tears、sweat、gloom、anger、shock、heart_eyes、star_eyes、shadow），写错时启动会报错并列出合法名单。

### 四、生成并导入蓝图

在项目根目录运行（`--scene` 指向第一步保存的场景文件）：

```
uv run python scripts/warudo_blueprints.py --scene "<Warudo 数据目录>/Scenes/DefaultScene.json"
```

脚本在 `data/warudo_blueprints/` 下生成：

| 蓝图 | 作用 |
|---|---|
| Amaidesu 头部动作与站姿 | 点头一次、点头两次、摇头、歪头、低头看向一边，放松站姿、思考站姿、恢复默认站姿；用 Warudo 自带动画，任何人形模型都能用 |
| Amaidesu 风格化标记 | 登记了风格化标记才生成：每个标记一个入口，把收到的值写到对应形状上。标记做成 VRM 自定义表情的，加 `--mode vrm` |

打开 Warudo 的蓝图页，用"导入蓝图"逐个导入。注意：

- **蓝图是按角色的内部编号找角色的。** 删过或重新添加过角色，编号就会变，旧蓝图会找不到角色、什么都不动。换了角色或换了场景，都要先保存场景，再带 `--scene` 重新生成、删掉旧的 Amaidesu 蓝图再导入。
- 改了风格化标记的登记，也要重新生成并导入。
- 以前版本生成过"Amaidesu 表情""Amaidesu 口型""Amaidesu 眨眼与移眼"三个蓝图的，删掉它们：脸已经改由面捕数据驱动。

### 五、试发检查与启动

保持 Warudo 打开，先不启动 Amaidesu，运行：

```
uv run python scripts/warudo_blueprints.py --probe ws://localhost:19190
```

脚本把头部动作、站姿、风格化标记各发一遍，每项停 1.5 秒（`--hold 3` 可以放慢），终端里会打印正在发哪一项，对着画面看就行。

然后启动 Amaidesu：面捕数据一发出，形象就开始眨眼、看来看去，说话时嘴跟着语音动、表情跟着情绪变；蓝图通道 Warudo 后开也没关系，Amaidesu 每隔几秒重连。主播用 `avatar_act` 做的动作里会多出 Warudo 的头部动作、站姿和抛鱼；想让她用自己做的蓝图动作，在 `[platform.warudo.action_catalog]` 里登记"动作名 = 说明"，蓝图里用"收到 WebSocket 动作"节点按这个名字接收（数据类型选整数）。

### 常见问题

| 现象 | 原因与处理 |
|---|---|
| 形象一动不动 | 角色的面部捕捉没选 iFacialMocap，或接收器端口不是 49983；Warudo 和 Amaidesu 不在同一台电脑时，`tracking_host` 改成 Warudo 那台的地址 |
| 头会动，表情不动 | 形状映射没对上：模型没有 ARKit 形状却选了 ARKit，改选 VRM 或补形状 |
| 嘴一直微张 | 对没有 ARKit 形状的模型套用了 ARKit 映射；在形状映射里把 `jawOpen` 等的阈值调高，或给模型补 ARKit 形状 |
| 头部动作、站姿不动 | 蓝图里的角色编号和场景对不上：保存场景，带 `--scene` 重新生成，删掉旧的 Amaidesu 蓝图后再导入 |
| 嘴的动作很乱 | Warudo 自带的音频口型没关，两边在同时控制嘴 |
| 抛鱼没反应 | 抛鱼依赖场景里另外做的蓝图和一个第三方插件，脚本不生成 |

## VRChat

VRChat 通过 OSC（一种基于 UDP 的消息协议）接收外部程序的消息：Amaidesu 发到本机 9000 端口。VRChat 的 OSC 能写的东西有限：

- **官方眼动输入**：任何形象都认，Amaidesu 用它让形象眨眼、看来看去；
- **形象作者自定义的参数**：每个形象各不相同，Amaidesu 用它写面捕参数（做了面捕的形象）、风格化标记和动作；
- **头顶聊天框**：与形象无关，Amaidesu 用它显示主播说的话；
- 口型、手势、头部转动这类内置部分都是只读的，外部程序改不了。

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

### 六、眼睛与表情

- **眼睛**：任何形象都会跟着 Amaidesu 眨眼、看镜头、瞥弹幕区。VRChat 超过 10 秒收不到眼动数据会交还自带的自动眨眼，Amaidesu 关掉后形象照常。
- **表情**：只有做了面捕参数的形象会动。VRChat 圈里给形象接面捕的通行做法是 VRCFaceTracking，参数名形如 `FT/v2/JawOpen`、`FT/v2/MouthSmileLeft`；用这类模板做的形象，主播说话时表情跟着情绪变、嘴角和眉毛都会动。参数名前缀不是 `FT/` 的，在 `[platform.vrchat]` 里改 `face_tracking_prefix`；不想写脸就设 `face_tracking_enabled = false`。
- **风格化标记**：形象作者另做了脸红、眼泪这类参数的，在 `[platform.vrchat.markers]` 登记，比如 `tears = "Tears"`（标记名 = 参数名，参数类型用浮点）。

想核对形象有哪些参数，看第四步说的参数清单文件。
