<div align="center">
  <img src="docs/preview.png" width="170" alt="多多">

  # 🐱 多多 · 桌面宠物猫

  **Windows 桌面上的 Q 版暹罗猫**<br>
  会自己走动、发呆、打哈欠、踩奶、扑镜头，也能聊天、找文件、翻译剪贴板、报系统状态

  <br>

  <a href="../../releases/latest"><img src="https://img.shields.io/badge/Download-duoduo.exe-2EA043?style=for-the-badge&logo=windows&logoColor=white" alt="Download exe"></a>
  <a href="../../releases/latest"><img src="https://img.shields.io/badge/Download-frames__opt.zip-1F6FEB?style=for-the-badge&logo=files&logoColor=white" alt="Download assets"></a>
  <a href="使用说明.md"><img src="https://img.shields.io/badge/Read-使用说明-8957E5?style=for-the-badge&logo=readthedocs&logoColor=white" alt="Docs"></a>

  <br>

  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.10-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PyQt6-6.x-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="PyQt6">
  <img src="https://img.shields.io/badge/LLM-OpenAI%20兼容-4B5563?style=for-the-badge" alt="LLM">
  <img src="https://img.shields.io/badge/Tests-344%20passed-2EA043?style=for-the-badge&logo=pytest&logoColor=white" alt="Tests">

  <br>

  <img src="https://img.shields.io/badge/语言-中文-DE3A3A?style=for-the-badge" alt="中文">
  <img src="https://img.shields.io/badge/动作-10%20个%20%2F%20452%20帧-FB8C00?style=for-the-badge" alt="Actions">
  <img src="https://img.shields.io/badge/情绪语音-8%20种-EC6CB9?style=for-the-badge" alt="Moods">
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

## 📰 更新

- `[2026.09]` 🗑️ 新增**文件整理**：删除（进回收站、可还原）、批量删除、清理自己的截图与剪贴板文本、移动文件——删除必须二次确认，且只删它自己找出来的文件
- `[2026.09]` 👀 **程序化眨眼与随机呼吸**：自动定位眼睛位置，发呆时随机眨眼；呼吸幅度与周期每轮随机，偶尔深吸一口气
- `[2026.09]` ⏰ **日程自然语言化**：`每天18:30叫我下班`、`每周一9点提醒我开例会`、`工作日9点打卡`，到点气泡 + 语音 + 托盘通知三重提醒，重复日程自动续期
- `[2026.09]` 🖥️ **全屏避让**：玩全屏游戏或看视频时自动躲起来，退出全屏自己回来
- `[2026.09]` 📋 **剪贴板历史**：记住最近 10 条，`用第2条翻译` 直接对某一条做处理（只存内存）
- `[2026.09]` 📦 v1.0 发布：单文件 exe + 452 帧素材包，clone 后三步可跑

---

## ✨ 它会做什么

<div align="center">
  <img src="docs/actions.png" width="760" alt="发呆 / 吃东西 / 打哈欠 / 踩奶">
  <br><sub>发呆 · 吃东西 · 打哈欠 · 踩奶（素材原帧）</sub>
</div>

**🐾 桌面上的行为**

- 左键拖拽拎起来，松手后靠边 40px 内吸附屏幕边缘；甩太快会晕
- 双击摸摸，好感度 +1，冒爱心
- 发呆时随机眨眼（2.5~6.5 秒一次，偶尔连眨两下）；呼吸幅度与周期每轮随机，偶尔深吸一口气 —— 这两项由代码控制，不占素材
- 玩全屏游戏或看视频时自动躲起来，退出全屏自己回来；主人手动藏起来的不会被强行拉出
- 同一时刻只允许一只猫，重复启动会把已有那只叫到前台

**📂 把文件拖到它身上**：文本类（txt / md / py / json / csv / log…）读内容并总结要点，之后可以直接追问细节；文件夹列出条目；音频交给系统播放。说 `忘掉这个文件` 清掉上下文。

**🖱 右键菜单**分四组：🐾 互动 · 📋 剪贴板 · 🔧 工具 · ⚙️ 设置。

<div align="center">
  <img src="docs/preview.gif" width="300" alt="发呆 → 走路 → 吃东西 → 打哈欠">
  <br><sub>发呆 → 走路 → 吃东西 → 打哈欠</sub>
</div>

---

## 💬 能跟它说什么

<details open>
<summary><b>常用指令</b></summary>

<br>

| 说法 | 结果 |
|---|---|
| `现在几点` / `今天几号` | 时间与日期 |
| `25分钟后提醒我喝水` / `番茄钟` / `取消提醒` | 定时提醒，存盘，重启不丢 |
| `每天18:30叫我下班` | 每天定点提醒 |
| `每周一9点提醒我开例会` / `每周一三五 8点 提醒我锻炼` / `工作日9点提醒我打卡` | 每周或工作日提醒 |
| `明天9点叫我起床` / `今晚8点提醒我看剧` | 定点一次性提醒 |
| `找文件 pet_data` → `打开第1个` | 找文件并用默认程序打开 |
| `用记事本打开第1个` / `换个方式打开第1个` | 指定程序打开，或弹出 Windows 的「打开方式」选择框 |
| `声音大一点` / `静音` / `截个屏` / `系统状态` | 音量、截图存桌面、电量内存 CPU 网络 |
| `剪贴板历史` / `用第2条翻译` | 最近复制的 10 条，对其中某一条做翻译、总结、解释、润色、帮回复 |
| `开启看家模式` / `开启全屏避让` / `打开开机自启` | 离开自动去睡、久坐提醒休息；全屏时隐藏；开机自启 |
| `试听一下` / `情绪演示` | 试听音色、依次念八种情绪 |

</details>

---

## 🗂 文件整理（删除的都进回收站，可还原）

| 说法 | 结果 |
|---|---|
| `删掉第2个` / `删除第1个和第3个` | 列出待删清单，等你确认 |
| `把找到的都删了` | 最近找到的全部列入清单（仍需确认） |
| `确认删除` / `取消` | 执行（进回收站）/ 放弃 |
| `清理一下你的截图` | 清 `桌面/多多截图_*.png`，只清它自己截的 |
| `清理剪贴板文本` | 清 `桌面/剪贴板_*.txt` |
| `清空语音缓存` | 清 `%TEMP%\duoduo_tts` |
| `把第1个移动到桌面` | 移动到桌面 / 文档 / 下载 / 图片 / 临时目录，文件不会丢 |

<div align="center">
  <img src="https://img.shields.io/badge/⚠️_第一条-只删它自己找出来的文件-D1242F?style=for-the-badge" alt="只删自己找到的">
  <img src="https://img.shields.io/badge/⚠️_第二条-确认前一个字节都不动-D1242F?style=for-the-badge" alt="必须确认">
  <img src="https://img.shields.io/badge/⚠️_第三条-回收站可还原-2EA043?style=for-the-badge" alt="回收站">
</div>

<br>

写在代码里的硬约束：

- **只删它自己找出来的文件**（先 `找文件 XXX`，再说 `删掉第2个`），不接受任意路径；**大模型没有删除工具**，无法自作主张
- **必须二次确认**：说 `删掉第2个` 只会列出清单，确认前一个字节都不动
- **送回收站**（`SHFileOperation` + `FOF_ALLOWUNDO`），不是永久删除
- **系统目录与程序目录一律拒删**（`C:\Windows`、`Program Files`、`ProgramData`，以及多多自己的目录，避免误删素材与存档）
- 只删文件不删文件夹；单次上限 20 个；每次删除都记进 `%TEMP%\duoduo.log`

---

## 🔧 没命中指令的话，交给大模型

问题会带上当前时间与好感度发给模型。模型可以在回复末尾附 `[action: eat]` 让小猫做动作，或 `[tool: open bilibili]`、`[tool: volume down]`、`[tool: clipboard summary]` 让它执行工具（白名单，未知工具忽略）。工具执行完会把结果回喂给模型再问一轮，所以「帮我找找 pet_data 然后打开它」可以一次说完。

---

## 🔊 语音

引擎按可用性依次尝试：**edge 神经语音 → Windows OneCore → SAPI → PowerShell**。默认音色是 edge 的晓伊，OneCore 用系统装机音色（瑶瑶、慧慧）。八种情绪（开心、兴奋、撒娇、困倦、提醒、得意、委屈、平常）通过语速与音调实现，同一句话按当前状态换语气。

edge-tts 走 aiohttp，不读 Windows 的 IE 代理设置，程序会自己从注册表取代理传给它：实测同一端点直连约 11 秒、走代理约 1.2 秒；代理失效时退回直连。

---

## 🧰 工程

| 文件 | 内容 |
|---|---|
| `多多.py` | 窗口、绘制、动画状态机、行为、本地意图、工具派发 |
| `ai_assistant.py` | 大模型客户端、提示词、动作/工具/情绪标签解析、站点与文件查找 |
| `pet_tools.py` | 语音（多引擎 + 情绪）、日程解析、系统状态、音量、剪贴板历史、回收站删除 |
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
python test_assistant.py    # 158：大模型层（打桩，不联网）与集成
python test_tools.py        # 126：语音、日程、音量、快照、删除规则
```

</details>

---

<div align="center">
<sub>角色形象由 AI 生成视频经抠图、对齐、修帧得到。请勿直接商用。</sub>
</div>
