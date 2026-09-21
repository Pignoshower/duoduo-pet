<div align="center">
  <img src="docs/preview.png" width="170" alt="多多">

  # 🐱 多多 · 桌面宠物猫

  **Windows 桌面上的 Q 版暹罗猫**<br>
  会自己走动、发呆、打哈欠、踩奶、扑镜头，也能聊天、找文件、整理文件、翻译剪贴板

  <br>

  <a href="../../releases/latest"><img src="https://img.shields.io/badge/Download-duoduo.exe-2EA043?style=for-the-badge&logo=windows&logoColor=white" alt="Download"></a>
  <a href="../../releases/latest"><img src="https://img.shields.io/badge/Download-frames__opt.zip-1F6FEB?style=for-the-badge&logo=files&logoColor=white" alt="Assets"></a>
  <a href="使用说明.md"><img src="https://img.shields.io/badge/Read-使用说明-8957E5?style=for-the-badge&logo=readthedocs&logoColor=white" alt="Docs"></a>

  <br>

  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.10-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PyQt6-6.x-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="PyQt6">
  <img src="https://img.shields.io/badge/Tests-438%20passed-2EA043?style=for-the-badge&logo=pytest&logoColor=white" alt="Tests">

  <br>

  <img src="https://img.shields.io/badge/动作-10%20个%20%2F%20452%20帧-FB8C00?style=for-the-badge" alt="Actions">
  <img src="https://img.shields.io/badge/情绪语音-8%20种-EC6CB9?style=for-the-badge" alt="Moods">
  <img src="https://img.shields.io/badge/安全-删除进回收站-2EA043?style=for-the-badge" alt="Safe">
</div>

<br>

---

<div align="center">

### 🚀 三步跑起来

| ① 下载 | ② 解压到同一个文件夹 | ③ 双击 |
|:---:|:---:|:---:|
| `duoduo.exe`<br>`frames_opt.zip` | `duoduo.exe` 与 `frames_opt/` 并列 | 运行 `duoduo.exe` |

</div>

首次运行会自动生成 `config.json`，填上 `api_key`（任意 OpenAI 兼容接口，默认 DeepSeek）重启就能聊天；不填也能用，只是不会闲聊。

> **exe 未做代码签名**，Windows SmartScreen 首次可能拦一下，点「更多信息 → 仍要运行」。它由仓库里的 `build_exe.py` 打包，可自行重新构建。
>
> **素材约 70MB**，没有放进仓库；启动时若 `frames_opt/` 缺失，程序会弹说明框告诉你缺什么、去哪里取。

<details>
<summary><b>⌨️ 想从源码运行</b></summary>

<br>

```powershell
git clone https://github.com/Pignoshower/duoduo-pet
cd duoduo-pet
# 先准备素材：下载 Release 里的 frames_opt.zip 解压到此处
pip install PyQt6
pip install edge-tts          # 可选，神经网络语音，需联网
copy config.example.json config.json
python 多多.py
```

打包：`python build_exe.py --clean`

</details>

---

## ✨ 它会做什么

<table>
<tr>
<td width="50%" valign="top">

<b>🐾 一只真的在桌面上活动的猫</b><br>
10 个动作、452 帧。左键拎起来、甩太快会晕、落地会压扁、松手靠边吸附、鼠标靠近会转头看你。

</td>
<td width="50%" valign="top">

<b>👀 眨眼与呼吸</b><br>
启动时自动定位眼睛位置，发呆时随机眨眼（偶尔连眨两下）；呼吸幅度与周期每轮随机，偶尔深吸一口气。程序控制，不占素材。

</td>
</tr>
<tr>
<td width="50%" valign="top">

<b>💬 会聊天的助手</b><br>
接任意 OpenAI 兼容接口。不填 key 也能用——时间、音量、截图、系统状态、找文件这些都是本地识别，不联网。

</td>
<td width="50%" valign="top">

<b>📂 找文件与打开方式</b><br>
`找文件 报告` → `打开第2个`；也能 `用记事本打开第1个` 或 `换个方式打开` 弹 Windows 的打开方式选择框。把文件拖到它身上会读内容总结，之后还能追问。

</td>
</tr>
<tr>
<td width="50%" valign="top">

<b>🗑 文件整理</b><br>
删除（进回收站可还原）、批量删除、清理它自己截的图和剪贴板文本、把文件移动到桌面或文档。每步都先列清单等你确认。

</td>
<td width="50%" valign="top">

<b>⚡ 敏感操作（确认后执行）</b><br>
`打开控制台`、`以管理员打开命令行`、`运行 ipconfig /all`。破坏性命令（format、diskpart、rm -rf、shutdown…）当场拒绝，不给确认机会。

</td>
</tr>
<tr>
<td width="50%" valign="top">

<b>⏰ 提醒与日程</b><br>
`25分钟后提醒我喝水`、`每天18:30叫我下班`、`每周一9点提醒我开例会`、`工作日9点打卡`。到点气泡 + 语音 + 托盘通知三重提醒，重复日程自动续期。

</td>
<td width="50%" valign="top">

<b>🌙 看家模式与安静模式</b><br>
离开 5 分钟它去睡、回来打招呼、连续用电脑 50 分钟提醒你休息。`晚上11点到早上7点别打扰我` 则整段时间不出声。

</td>
</tr>
<tr>
<td width="50%" valign="top">

<b>📋 剪贴板 AI</b><br>
翻译、总结、解释、润色、帮回复、念一遍、存到桌面；后台记住最近 10 条，`用第2条翻译` 直接处理其中一条（只存内存）。

</td>
<td width="50%" valign="top">

<b>🔊 情绪语音</b><br>
edge 神经语音 → Windows OneCore → SAPI → PowerShell 四级降级。八种情绪按状态切换语调，同一句话开心和困倦读起来不一样。

</td>
</tr>
<tr>
<td width="50%" valign="top">

<b>🖥 多屏与全屏避让</b><br>
拖到副屏就在副屏活动、按副屏边缘吸附；玩全屏游戏或看视频时自动躲起来，退出全屏自己回来（手动藏起来的不受影响）。

</td>
<td width="50%" valign="top">

<b>🧩 菜单与热键</b><br>
右键菜单分四组（互动 / 剪贴板 / 工具 / 设置）；`Ctrl+Alt+D` 聊天、`Ctrl+Alt+C` 翻译剪贴板、`Ctrl+Alt+Z` 总结剪贴板。重复启动只会把已有那只叫到前台。

</td>
</tr>
</table>

<div align="center">
  <img src="docs/preview.gif" width="300" alt="发呆 → 走路 → 吃东西 → 打哈欠">
  <br><sub>发呆 → 走路 → 吃东西 → 打哈欠（素材原帧）</sub>
</div>

---

## 💬 能跟它说什么

<details open>
<summary><b>常用</b></summary>

<br>

| 说法 | 结果 |
|---|---|
| `现在几点` / `今天几号` | 时间与日期 |
| `25分钟后提醒我喝水` / `番茄钟` / `取消提醒` | 定时提醒，存盘，重启不丢 |
| `每天18:30叫我下班` / `每周一9点提醒我开例会` / `工作日9点提醒我打卡` | 每天、每周、工作日提醒 |
| `明天9点叫我起床` / `今晚8点提醒我看剧` | 定点一次性提醒 |
| `找文件 pet_data` → `打开第1个` | 找文件并用默认程序打开 |
| `声音大一点` / `静音` / `截个屏` / `系统状态` | 音量、截图存桌面、电量内存 CPU 网络 |
| `剪贴板历史` / `用第2条翻译` | 最近复制的 10 条，对某一条做翻译、总结、解释、润色、帮回复 |
| `试听一下` / `情绪演示` | 试听音色、依次念八种情绪 |

</details>

<details>
<summary><b>文件整理（删除的都进回收站）</b></summary>

<br>

| 说法 | 结果 |
|---|---|
| `删掉第2个` / `删除第1个和第3个` | 列出待删清单，等你确认 |
| `删除 D:\test\a.txt` | 按路径删（带空格的路径不用加引号也认） |
| `把找到的都删了` | 最近找到的全部列清单（仍需确认） |
| `确认` / `取消` | 执行 / 放弃 |
| `清理一下你的截图` / `清理剪贴板文本` / `清空语音缓存` | 只清它自己生成的文件 |
| `把第1个移动到桌面` | 移动到桌面 / 文档 / 下载 / 图片 / 临时目录 |

</details>

<details>
<summary><b>敏感操作与安静模式</b></summary>

<br>

| 说法 | 结果 |
|---|---|
| `打开控制台` / `打开cmd` / `打开终端` / `打开命令提示符` | 确认后开出 cmd 窗口 |
| `打开 PowerShell` / `以管理员打开命令行` | PowerShell，管理员那条会再走一次 UAC |
| `运行 ipconfig /all` | 确认后执行，输出贴在气泡里（超 20 秒自动停） |
| `开启安静模式` / `别吵我` / `关闭安静模式` | 不出声、不主动搭话，但你问它照常冒泡 |
| `晚上11点到早上7点别打扰我` | 每天固定时段免打扰，支持跨零点 |
| `现在安静吗` | 回报安静模式状态与时段 |
| `开启看家模式` / `开启全屏避让` / `打开开机自启` | 离开去睡与久坐提醒；全屏时隐藏；开机自启 |

</details>

---

## 🔒 安全边界

这些不是"提示"，是写在代码里、有测试守着的硬约束：

- **删除一律送回收站**（`SHFileOperation` + `FOF_ALLOWUNDO`），没有永久删除这回事
- **确认前不动手**：列清单 → 你点头 → 才执行；`取消` 会把所有待确认项一起清掉
- **只删文件，不删文件夹**；单次上限 20 个
- **系统目录与程序目录拒删**：`C:\Windows`、`Program Files`、`ProgramData`，以及多多自己的目录（避免误删素材与存档）
- **破坏性命令当场拒绝**：format、diskpart、shutdown、`del /f /s`、`rd /s`、`rm -rf`、`reg delete`、`net user`、`bcdedit` 等 20 余条规则
- **大模型不能触发删除、控制台与运行命令**——这些只能由你明确说出口，模型最多提议、由你确认
- 每次删除与执行都记进 `%TEMP%\duoduo.log`

---

## 🔊 语音

引擎按可用性依次尝试：**edge 神经语音 → Windows OneCore → SAPI → PowerShell**。默认音色是 edge 的晓伊，OneCore 用系统装机音色（瑶瑶、慧慧）。八种情绪（开心、兴奋、撒娇、困倦、提醒、得意、委屈、平常）通过语速与音调实现。

edge-tts 走 aiohttp，不读 Windows 的 IE 代理设置，程序会自己从注册表取代理传给它：实测同一端点直连约 11 秒、走代理约 1.2 秒；代理失效时退回直连。

---

## 🧰 工程

| 文件 | 内容 |
|---|---|
| `多多.py` | 窗口、绘制、动画状态机、行为、本地意图、工具派发、敏感操作闸门 |
| `ai_assistant.py` | 大模型客户端、提示词、动作/工具/情绪标签解析、站点与文件查找 |
| `pet_tools.py` | 语音、日程、安静模式、系统状态、音量、剪贴板历史、回收站删除 |
| `app_health.py` | 日志、单实例锁、开机自启、配置自检、空闲检测、全屏检测 |
| `build_exe.py` | PyInstaller 打包，图标由素材首帧生成 |
| `pipeline.py`、`snapshot.py` | 素材流水线与快照回退，只在你要自己改素材时用到 |
| `使用说明.md` | 全部指令、配置项与排错表 |
| `docs/` | 上面的预览图与动图 |

<details>
<summary><b>✅ 测试（离屏运行，不需要显示器）</b></summary>

<br>

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python selftest.py          # 29：帧资源、动画状态机、转圈首尾一致
python test_app_smoke.py    # 31：应用级冒烟
python test_assistant.py    # 198：大模型层（打桩，不联网）、删除与控制台闸门、安静模式
python test_tools.py        # 180：语音、日程、音量、快照、删除规则、危险命令
```

</details>

---

<div align="center">
<sub>角色形象由 AI 生成视频经抠图、对齐、修帧得到。请勿直接商用。</sub>
</div>
