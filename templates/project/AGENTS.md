# 本项目的剪辑与接手约定

先读取本目录的 `PROJECT_STATE.json`，再读取其 `profile_path` 指向的唯一个人约束文件。使用已安装的 `$jianying-amv-workflow` 进行制作与接手、`$video-media-review` 进行独立审查、`$video-workflow-learning` 记录经验与升级提案。个人约束与详细操作方法不复制到这里。

主agent统一写入项目状态，并且是唯一原生工程/UI写入者。并行任务必须明确自己的写入目录；素材与审查agent不得修改原生工程、状态、受保护媒体或其他任务文件。

最新保存的原生工程是UI修改后的编辑权威，旧装配计划冻结为设计参考。接手先核对当前版本、工程/导出指纹、依赖与失效证据，不运行旧计划覆盖原生修订。原生工程加密或不可读取时记录限制，不能把旧JSON当作最新状态。

结构位置：`assets/MEDIA_MANIFEST.json` 是素材登记；`design/EDIT_PLAN.json` 是设计计划；独立审查写入 `review/<版本>/`；交付写入 `delivery/<版本>/`。具体路径与受保护区域以PROJECT_STATE为准。

新项目分轮确认需求，已有授权内持续完成工作。内部小样由agent完成，用户审片节点为完整初版。实际UI操作加载当前安装的computer-use skill，优先确认可用的副屏，不在此复制工具协议。

没有真实听审确认保持待用户听审；ASR、波形、无削波或视频可播放不能代替正式定稿门槛。新导出或约束变化后重新核对受影响证据。

使用 `workflow_toolkit_path` 指向的统一工具，执行前查看help；当前阶段、待办与证据仅维护在PROJECT_STATE，手册引用该状态。
