# 项目最小字段契约

模板位于包内 `templates/project`。保留空数组及null表示未确定，初始化后填真实值；空字段不能被验收工具当作通过或默认帧率。字段示例是结构，不是某部作品的规格。

## PROJECT_STATE.json

- `schema_version`、`project_id`、`project_name`：项目身份。工具及审核记录关联同一个id。
- `profile_path`、`workflow_toolkit_path`：运行数据和统一工具位置。
- `phase`：`planning | editing | review | final`。此文件是唯一当前阶段入口。
- `constraints`：`revision`、`profile_revision`、`project_overrides`。默认约束读取profile；项目覆盖有意图与用户授权依据，变动递增项目revision。
- `clocks.timeline`：实际 `fps`、`unit: frame`、`end_exclusive: true`。fps是有理数字符串，未确定为null；不要给任何新项目套上既有作品帧率。
- `paths`：媒体登记、设计计划、审核目录与交付目录的项目相对路径。
- `artifacts`：每项 `id / role / path / sha256 / size_bytes / authority / encrypted`。当前原生用`role: native_project`、`authority: current_native`，当前实际导出用`role: export_current`。原生path可以是目录（SHA为目录树指纹），或content文件（SHA为文件指纹，另给`project_directory`完整目录）；可补`files`单文件指纹及`cache_paths`。历史备份用`role: native_backup`，历史EDL用`role: historical_edl`、`authority: design_reference`，不能有两个原生权威。最新原生与导出分别由 `latest_native_artifact_id`、`latest_export_artifact_id` 引用。
- `evidence`：每项 `id / kind / status / constraints_revision / scope / fingerprints`，范围有asset ids、artifact ids和半开目标区间。`verified`是对该范围与该指纹的结论，不是全片总验收。
- `editing_owner`、`known_issues`、`pending_user_decisions`、`next_actions`：写入者和接手信息。不要另外建可与这里冲突的进度表。

证据类型应区分：`human_listening`、数值音频检查、ASR、像素审查、解码、媒体边界、原生重开与迁移。人工听审需`actor: user`、`verification_method: user_listening`、真实确认记录`user_confirmation_ref`、当前导出指纹和当前约束revision；不得伪造。正式定稿还需`native_export_binding`证据同时绑定当前原生与导出SHA，方法为`native_export_observation`或`user_export_confirmation`，并清除未解决的blocker/major。ASR或无削波不满足听审门槛；绑定未知时维持review。

## assets/MEDIA_MANIFEST.json

每个素材一项，`id / path / frozen` 标记引用身份；`source` 给原源路径、源clock id、半开in/out、单位、实际fps、`canonical_scene_id`及aliases；`proxy` 给实际fps、解码帧数与safe帧区间；`boundary` 给审查状态、内部切点和方法。

音频、字体、缓存、图片等按 `media_type` 区分。音频补充字幕/ASR/上下文/波形证据引用和听审状态；字体保留许可证；缓存登记对应原生工程与依赖。缺少证据时写pending/candidate，不能用文件名代替语义确认。

依赖登记在state或manifest的 `dependencies`，每项 `id / path / kind / frozen / sha256`，选择一个权威位置不双写。无法完整解析加密工程时，可保守收集依赖并标 `dependency_status: conservative_superset`；它不证明全部依赖正在使用，也不证明移机成功。

## design/EDIT_PLAN.json

`plan_status`区分草拟、可装配与冻结历史；`authority: design_reference`固定说明不是UI实时状态。每个shot提供 `id / asset_id / proxy_in_frame / proxy_out_frame_exclusive / target_in_frame / target_out_frame_exclusive / occurrence_id / design_intent`。

有意replay/freeze/reverse/loop在shot中写 `reuse_declaration`：`source_identity`引用asset_id，`target_interval`给timeline/frame的in/out并与shot范围一致，`effect_type`与design_intent一致，`creative_purpose`写创作目的；另写 `authorization_ref`。工具兼容旧 `intent_note / repeat_of / authorization_ref`，新记录优先明确结构。`authorization_ref`可以引用已允许有意重放的profile，不意味着每次重放需要额外确认；项目严禁复用时不能用说明绕过。原动画正常持帧不自动认作人工freeze。同一时刻前后景合成用 `same_instant_composite`，给 `composite_of / sync_verified / transform_same_position`，不得留下人物双影。

音乐、原声、字幕、辅助层分别登记在对应数组，引用同一MEDIA_MANIFEST，不能靠换代理id掩盖原源重复。字幕需真实文本、目标区间和图层关系；音频需源/目标范围、速度分段及声音处理。时钟转换读state/manifest的实际fps；不同单位不得无标注混算。

## 统一CLI

先运行主工具 `--help`，再看所用子命令的 `--help`；参数以当前安装实现为准。

| 子命令 | 职责 |
|---|---|
| `probe` | 读媒体元数据，不能替代目视/听审 |
| `audit-usage` | 审源区间、复用声明和同步合成 |
| `map-clock` | 检查源与目标时钟映射 |
| `audit-export` | 检查实际导出并生成范围明确的证据 |
| `validate-state` | 检查身份、引用、证据和阶段门槛 |
| `invalidate-evidence` | 标记受版本/约束变化影响的证据 |
| `pack-project` | 收集可迁移交付依赖，不删除原文件 |
| `handoff` | 汇出接手所需信息；当前状态仍只有一份 |

审查者只输出独立报告；写回state由主agent执行。不得为了让验证器绿灯删掉已知问题或伪造听审状态。
