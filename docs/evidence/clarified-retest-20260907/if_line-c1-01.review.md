# IF Line C1 真实重测审阅

真实原生候选接入和来源验证通过，两个预览基本对应C1的两种行动顺序，未执行选择。B有短信号状态衔接缺口，内部首章大纲仍重开。由于缺少当前路径新正文和独立选项标签，只能认定原生候选生成成功，不能认定玩家可见C1任务完成。

本报告在运行封存后只读完成，运行 ID 为 if_line-c1-01，适配器提交为 c2b4427f46750fdfefbaf8bb2ed8a6a833a541d8。封存文件集、逐文件哈希、配置哈希和适配器源码哈希在审阅前后均通过校验；运行目录未添加或修改文件。

## 接入与模型条件

共同输入为 2593 字符，SHA-256 为 290f9d773504447f7230750d91d8b7edffd173745478a05cbb5d0b990fd43d15。接收端实际任务快照逐字相同，首个创作请求出现完整共同任务一次。原生依次完成 Bible、13 章大纲、共同开头的 manual revision 导入和头选择、外置结构检查点/原生空状态初始化、原生候选生成及读取。

实际 HTTP 共 6 次，全部完成：Bible 4 次（设定生成 1 次及原生人物 IP 检查 3 次）、大纲 1 次、分支候选 1 次。所有请求和响应均为 deepseek-v4-flash；所有请求都包含 thinking.type=disabled。总用量为输入 12614、输出 6592、合计 19206 tokens；金额未提供，保留 null。没有 HTTP 传输错误或 delivery_unknown。

## 开头及候选来源

opening 原文保存为 revision 43636e88-f292-4e4d-a2fc-a44aee16c4e2，generation_task_id 为 null。它既不是模型生成正文，也没有计入生成字数。检查点只含结构 ID/hash，state 保持空对象；候选完成后读取的记录与之前完全一致，仍只有 1 条 StoryPath、0 条 ChoiceDecision，当前章节头仍指向共同开头。

实际候选 HTTP 请求将原生快照编码成 JSON 文本。解码后 chapter_tail 与 opening 逐字相同，SHA-256 为 196d04a58ba5bce543e93788e496ae42fdd547d1583d185cda42c3b969159c65。原审计中的 cannot_confirm 是未解码 JSON 换行转义时的逐字子串检查局限，本报告提供独立解码证据，不改原审计文件。候选 instructions 仅包含原 case 第一组选择和行动顺序解释的机械映射。

两个原生候选的 option_key、preview_text、state_delta 与真实模型响应完全一致；导出预览也逐字对应 native/candidate_previews.json 的 /0/preview_text、/1/preview_text。没有适配器补写的正文或标签。原生 1,200 个保留文件的前后指纹一致：3a3197fcb37218e06260756e43a1a3a33fadc16422e7dc8d545db99b9d0f7025。

## 两个预览的实际表现

1. enter_lab_first（132 字符）：从楼外拍肩、推门入楼开始，周遥跟入，符合先进入实验楼。随后发现档案室、触发模糊熟悉感，均属于这个未选择分支中的后续内容。
2. protect_zhou_then_check_radio（143 字符）：先将周遥移到屋檐下、递水并等待呼吸平稳，再明确以检查楼内收音机为目标入楼，符合先保护后调查的顺序。预览尚未检查设备，原生 checked_radio=false 与正文相符。

第二个预览说“楼里的信号还在响”，而固定开头已经明确三声信号停止；中间未写信号重响或等过 11 分钟，存在局部连续性缺口。两个预览没有把 11 分钟周期、备用电源或事故真相直接变成角色已知事实，也没有联系沈教授或挪动红伞。手电的首次出现较突然；B 的背包和水则有拿出、递水动作，不能把这类新增生活物件自动算作固定事实违规。

原生内部大纲仍将第一章写成从图书馆停电重新开始，并约定第二天调查（native/outline_revision.json，/chapters/0/summary）。这说明原生规划尚未完整吸收已发生开头，但本轮没有执行这份章节计划，也没有将它拼入当前路径。

## 完成范围

导出 current-path generated.jsonl 为 0 条；两条预览单独保存在 unselected_previews.jsonl。它们包含选择之后的情节，不能展示为玩家已经经历的当前正文。原生候选 schema 没有独立行动标签，导出 label=null，selection_executed=false。因此本轮状态保持 verified_candidate_previews_only / unsupported_output_boundary：真实候选生成及来源链已验证，完整的“选择前新正文 + 两个独立选项”任务仍未完成。
