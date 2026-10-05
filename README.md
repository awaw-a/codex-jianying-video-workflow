# Codex × Windows 剪映：动漫／游戏音乐剪辑工作流

让新聊天能接手工程、审查结论有依据、用户反馈持续积累。主要效果保持剪映原生可编辑，技术检测与实际视觉/听审分别记录。

## 快速开始

1. 阅读[开始使用](开始使用.md)和[用户入门](手册/用户入门.md)。
2. 使用 `python -X utf8 安装与初始化.py install` 安装到当前用户的 `.agents/skills` 和 `.agents/video-workflow`。已有不同技能或配置会被保留，不强制覆盖。
3. 用 `python -X utf8 安装与初始化.py init-project --dest "新的项目目录"` 建立项目，从项目根打开Codex。
4. 在新聊天使用 `$jianying-amv-workflow`：先检查素材、音乐和环境，再分轮讨论脚本。制作过程中由agent内部验证，先修明显问题，再提交完整初版。

Python、FFmpeg/FFprobe、剪映及当前computer-use能力需按本机环境检测，参见[运行资料](skills/jianying-amv-workflow/references/runtime-and-authority.md)。`data/machine.example.json`只是空配置模板，机器配置不提交Git。

## 三个技能

| 技能 | 职责 |
| --- | --- |
| [jianying-amv-workflow](skills/jianying-amv-workflow/SKILL.md) | 需求、研究、音乐、选材、制作、接手与交付 |
| [video-media-review](skills/video-media-review/SKILL.md) | 独立审查素材和实际导出，不能修改工程 |
| [video-workflow-learning](skills/video-workflow-learning/SKILL.md) | 保存经验候选，未经确认不改长期规则 |

最新保存的原生工程代表编辑状态；旧EDL不能覆盖后来的原生修改。主agent统一写工程和状态，协作者只写自己的输出目录。声音没有真实听审时不得正式定稿。

## 配置、案例与验证

`data/user-profile.json`记录原作者已确认的个人剪辑要求，`approved`只表示原作者的确认。其他使用者应先审阅并按自己的选择形成配置，不能将作者偏好视为自己的授权。新项目保存带版本的派生快照，个人规则保持一个可编辑入口。

[《越过高塔》复盘](examples/越过高塔/复盘.md)逐项对应用户建议和修正机制；公开证据均为脱敏文字摘要，不包含工程、画面、音频、机器路径或可编辑项目状态。完整原始证据留在作者本机；历史摘要不能冒充新环境验收。

阅读[Agent实操](手册/Agent实操.md)、[接手与迁移](手册/接手与迁移.md)、[故障与证据](手册/故障与证据.md)、[规则与经验升级](手册/规则与经验升级.md)和[验证范围](验收报告.md)。

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 verification/test_install_and_music.py
```

真实解码测试需先设置 `VIDEO_WORKFLOW_TEST_FFMPEG`、`VIDEO_WORKFLOW_TEST_FFPROBE` 为本机工具路径；没有工具时相关测试会明确跳过。无解码错误、ASR、波形、缓存数量不能替代实像和真实听审。
