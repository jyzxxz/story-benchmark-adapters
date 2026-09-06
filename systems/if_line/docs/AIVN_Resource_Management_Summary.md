# AIVN 项目资源管理机制总结

用于与后端对齐内容包、资源对象、上传下载和校验方案

2026-06-14

## 一、核心结论

AIVN 当前资源机制可以概括为“书籍运行索引 + 全局对象池 + scoped id 引用”。章节跟随书籍目录保存，图片、音频等非章节素材进入统一 Objects 对象池，真实文件名使用完整 SHA-256。运行时通过 manifest 和 scoped id 解析资源，不以目录形状、扩展名或字段名猜测资源身份。

- 书籍、内置内容和下载内容保持同构目录结构，只允许根目录不同。
- 全局素材实体信息由 assets_manifest.json 管理，书籍 manifest 只声明本书章节和 used_assets。
- 新数据保存 assets:xxx 或 package:story.xxx 这种 scoped id，不保存长期物理路径。
- 发布包和下载落地结构应保持一致，便于客户端直接安装到 user://content。
## 二、正式内容目录

正式内容根目录固定包含 Books/、Objects/、assets_manifest.json。内置内容根目录是 res://Resources/Builtin，用户下载或安装内容根目录是 user://content。

Books/

Objects/

assets_manifest.json

Books/{package_id}/manifest.json

Books/{package_id}/Chapters/*.json

Objects/sha256_{完整 SHA-256}.{ext}

| 用途 | 根目录 | 说明 |
| --- | --- | --- |
| 内置内容 | res://Resources/Builtin | 随客户端打包，只读。 |
| 用户/下载内容 | user://content | 社区下载、本地安装、用户导入内容。 |
| 用户运行状态 | user://state | 存档、阅读历史等运行期数据，不属于内容资源。 |
| 缓存 | user://content/cache | 下载、解压、缩略图、临时文件，可清理，不注册为正式资源。 |

## 三、两个 manifest 的分工

Books/{package_id}/manifest.json 是书籍自己的运行索引；assets_manifest.json 是全局素材实体索引。两者不要互相塞对方的数据。

| 文件 | 负责内容 | 不能负责的内容 |
| --- | --- | --- |
| Books/{package_id}/manifest.json | 书籍元数据、入口章节、章节 assets、cover、used_assets、dependencies/source。 | 不保存图片、音频等非章节素材的 hash、ext、category 或正式 path。 |
| assets_manifest.json | 全局素材实体：逻辑 asset id、type、category、ext、hash、aliases、metadata。 | 不保存正式资源 path，不保存书籍章节流程。 |
| Chapters/*.json | VN 节点图和剧情数据，资源字段保存 scoped id。 | 不长期保存 Objects 物理路径，不引用 cache 路径。 |

## 四、关键字段约定

| 字段 | 所在位置 | 含义 | 后端规则 |
| --- | --- | --- | --- |
| package_id | 书籍 manifest | 作品稳定身份。 | 同一作品升级版本时保持不变，不能用 md5/sha256 替代。 |
| version | 书籍 manifest / assets manifest | 内容版本。 | 和 package_id 一起表达版本发布，不表达单文件内容身份。 |
| entry_story | 书籍 manifest | 入口章节 asset id。 | 必须存在于本书 assets 中，不是文件路径。 |
| cover | 书籍 manifest | 封面资源。 | 必须为空或 assets:xxx。 |
| assets | 书籍 manifest | 章节条目。 | 章节条目可以有 path，且必须在本书 Chapters/ 下。 |
| used_assets | 书籍 manifest | 本书声明使用的全局素材 scoped id 列表。 | 只保存 assets:xxx，不重复 hash、ext、category、path。 |
| hash | assets_manifest / 章节 entry | 文件内容 SHA-256。 | 服务端必须重算校验。 |
| ext | assets_manifest | 对象扩展名。 | 用于还原 Objects/sha256_hash.ext。 |
| aliases | assets_manifest / 章节 entry | 兼容短名或展示查找。 | 不能作为唯一事实来源，主 id 仍是 asset id。 |

## 五、资源引用规则

项目当前推荐两类长期引用：全局素材使用 assets:{asset_id}，章节或剧情流使用 {package_id 或 namespace}:{story_asset_id}。发布整理和 used_assets 收集只处理带 [EAsset] 标记的节点字段，普通文本即使长得像路径，也不应该被服务端或客户端猜成资源引用。

- 全局素材示例：assets:bg.queqiao_interface_starry。
- 章节引用示例：darkforestfall:story.darkforestfall.00_prologue_lake_below。
- 新章节、场景、存档和历史记录里不应保存旧的物理资源路径。
## 六、运行时解析机制

- AssetResolver 注册内置 assets、用户 assets、内置 Books、用户 Books、用户 Works。
- 优先级大致为 Builtin < UserBooks < UserWorks；Work 用于本地编辑覆盖已安装包。
- 书籍章节资产会注册为 package/namespace 作用域下的 asset id。
- 全局素材不是启动时全量注册，而是遇到 assets:xxx 时按需从 assets_manifest.json 读取并缓存。
- 当 CurrentPackageId 存在时，全局素材必须在当前书 manifest 的 used_assets 中声明，或由 source/dependencies 间接声明，才允许解析。
- 资源加载统一走 AssetService：res:// 优先走 Godot ResourceLoader，user:// 原始图片/音频按文件读取。
## 七、发布与上传包格式

客户端发布 work 或 book 时，会整理成同构 zip。后端第一版最稳妥的方案，是直接接受并复核这个结构。

- 发布时会把外部非章节资源复制到根 Objects/。
- 章节里的资源字段会被重写成 assets:{asset_id} 或 {package_id}:story.xxx。
- 内置 builtin 资源可以保留引用，不强制重复复制进社区包。
- 发布完成后会校验 book manifest 和 assets manifest。
Books/{package_id}/manifest.json

Books/{package_id}/Chapters/*.json

Objects/sha256_{hash}.{ext}

assets_manifest.json

## 八、服务端建议契约

- 上传接口第一版建议直接接受客户端生成的同构 zip。
- 服务端解包必须进入临时沙箱，拒绝绝对路径、../、user://、res:// 和非白名单文件。
- 服务端重新读取 manifest，重新计算所有章节和 Objects 文件 SHA-256。
- 对象存储以 sha256 + ext 去重；同 hash 同 ext 可以复用，不按书籍建立独立对象目录。
- 作品元数据表保存 package_id、version、title、author_id、entry_story、cover、used_assets、依赖关系和审核状态。
- 下载接口可以返回完整 zip，或返回 manifest + 对象清单 + 分片对象；客户端最终落地结构仍必须还原成 user://content 同构目录。
- 删除和清理应基于引用关系做 GC，不按“某本书目录下的素材”删除，因为素材是全局对象池。
## 九、服务端校验清单

| 校验项 | 规则 |
| --- | --- |
| 根结构 | 只接受 Books/、Objects/、assets_manifest.json。 |
| package_id | 必须存在、可作目录名、稳定，不允许为空。 |
| 章节 path | 必须是相对路径，必须在对应 Books/{package_id}/Chapters/ 下。 |
| 非章节素材 path | assets_manifest 中非章节素材不能写正式 path。 |
| Objects 文件名 | 必须是 sha256_{64位hex}.{ext}。 |
| hash 一致性 | manifest hash、文件名 hash、实际文件 SHA-256 必须一致。 |
| 扩展名白名单 | 图片：png/jpg/jpeg/webp/bmp/tga；音频：ogg/wav/mp3；文本等按产品需要收窄。 |
| cover | 必须为空或 assets:xxx，且能在 assets_manifest 中解析。 |
| used_assets | 只能是 assets:xxx，且不得携带 path、hash、ext、category。 |
| entry_story | 必须指向本书 assets 中的章节条目。 |
| 依赖 | dependencies/source 指向已存在或可下载的 package_id/version。 |

## 十、当前代码依据

| 模块 | 职责 |
| --- | --- |
| Scripts/Core/Asset/AssetContentPaths.cs | 正式目录、缓存目录、用户状态目录约定。 |
| Scripts/Core/Asset/AssetManifest.cs | Manifest 数据结构和读写入口。 |
| Scripts/Core/Asset/AssetResourceStore.cs | Objects 文件名、SHA-256、导入用户对象池。 |
| Scripts/Core/Asset/AssetResolver.cs | Manifest 注册、scoped id 解析、全局素材懒加载、used_assets 限制。 |
| Scripts/Core/Asset/AssetService.cs | 纹理、音频、文本加载统一入口。 |
| Scripts/Community/Publish/PackagePublisher.cs | 发布 zip 组装。 |
| Scripts/Community/Publish/PackagePublishAssembler.cs | 发布时重写 [EAsset] 字段和构建 used_assets。 |
| Scripts/Community/Package/PackageValidator.cs | 客户端发布前校验。 |
| Scripts/Logic/VNRuntime/VNResourcePreloader.cs | 运行时预加载 used_assets 和图内引用。 |

## 十一、需要进一步定案的问题

- 服务端是否以完整 zip 为唯一上传格式，还是支持 manifest + objects 的分片上传。
- 同 package_id 的版本升级策略：覆盖、保留多版本，还是用户选择安装版本。
- 对象存储 GC 策略：按引用计数、审核状态、版本保留窗口来清理。
- 下载端 installer 目前代码里只有缓存目录预留，完整安装流程需要后续实现。
- 审核系统是否需要保存章节 JSON 的结构化解析结果，用于安全检查、搜索和推荐。
