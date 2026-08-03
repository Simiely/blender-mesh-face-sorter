# 开发文档（DEVELOPMENT.md）

> 面向开发者的项目文档：架构说明 + 关键问题与方案（一坑一篇）。
> 每个问题用统一格式：**TL;DR**（一句话结论）→ 问题 / 根因 / 解决 / 预防。

## 项目概览

Blender 插件（v1.6.0，Blender 3.0-5.1+），单文件 `mesh_face_sorter.py`（~860 行）。按面数/顶点/存储大小排列网格体，支持 Decimate 减面、孤立显示、清理场景、导出报表。

## 架构说明

```
mesh_face_sorter.py（单文件插件）
├─ _Cache                 缓存层（核心，存原始 stats）
├─ _ScanStatus            扫描进度状态
├─ _scan_meshes()         统一扫描入口（唯一真相来源）
├─ collect_mesh_stats()   缓存 + 排序（排序即时完成，不重扫）
├─ _estimate_mesh_size()  内存占用估算（顶点24B+边8B+面8B+循环8B+UV8B+顶点色16B）
├─ _display_width/_truncate_name  CJK 显示宽度截断
├─ Operators（11 个）：Refresh/Select/SelectAll/Isolate/ShowAll/
│                     DeleteEmpty/ExportMd/AddDecimate/AddDecimateToObject/
│                     ApplyDecimate/PurgeOrphanData
├─ MESH_PT_FaceSortPanel  Panel UI（侧边栏 N 键）
├─ _on_load_post          文件加载时清缓存
└─ register() / unregister()
```

## 关键问题与方案

### 1. 手动刷新 vs 自动监听

**TL;DR**：`depsgraph_update_post` 每帧触发，自动刷新大场景会卡死。**纯手动刷新**：增删物体后用户点「刷新列表」才重扫；唯一自动失效是 `load_post`（换文件清缓存）。

- **问题**：自动刷新在大场景下卡死
- **根因**：`depsgraph_update_post` 每帧触发，draw() 高频调用模型下扫描是重负载
- **决策**：纯手动刷新，让用户掌控刷新时机
- **预防**：不要改回自动刷新；自动化的代价不一定值得

### 2. 缓存粒度

**TL;DR**：扫描 O(n) 昂贵、排序 O(n log n) 便宜——**缓存存原始 stats，排序即时完成，切换排序不重扫**。

- **问题**：切换排序方式需要重新扫描吗
- **决策**：`collect_mesh_stats()` 中即时排序，只重排缓存
- **预防**：昂贵操作（扫描）与廉价操作（排序）解耦，廉价操作自由切换

### 3. 存储大小估算

**TL;DR**：Blender 没有 `obj.size_in_bytes` API，基于网格组件估算（顶点 24B + 边 8B + 面 8B + 循环 8B + UV 8B/条 + 顶点色 16B/条）。**非精确值，用于相对比较**。

- **预防**：没有精确 API 时，可接受的近似比什么都不做好；标注"估算值"

### 4. 删除空网格的 UNDO 陷阱

**TL;DR**：`bpy.data.objects.remove()` + UNDO 恢复物体时，缓存中的 Python 引用变野指针 → ReferenceError。**不要在 Operator 里删除物体**，让 Blender 原生 Delete 处理，插件只负责刷新。

- **问题**：`bl_options = {'REGISTER', 'UNDO'}` + `bpy.data.objects.remove()` 导致撤销时缓存引用失效，连锁 ReferenceError
- **尝试**：`invalidate_cache()` 提前调用、拓宽异常捕获——仍不稳定
- **最终**：移除所有删除相关代码，用户手动 Delete 后刷新
- **预防**：Blender UNDO 对 `bpy.data` 操作深层耦合；涉及删除的场景先评估 UNDO 交互

### 5. Panel 内选中状态反馈

**TL;DR**：用 `row.active = is_selected` 让未选中行灰显 + `▶` 前缀 + OBJECT_DATA 图标做选中标记——**利用原生机制而非自建高亮**。

### 6. CJK 字符串宽度计算

**TL;DR**：中文名称占 2 字符宽度，固定字符截断会参差。`_display_width()`：`ord(ch) > 0x2E80`（CJK 起点）算 2，其余算 1；`_truncate_name()` 按显示宽度截断。

- **预防**：中文 UI 场景（名称截断/对齐）统一用显示宽度计算，不用 `len()`

### 7. 代码去重：_scan_meshes 统一入口

**TL;DR**：Refresh.execute 与初始扫描有 80% 重复代码 → `_scan_meshes(with_progress, on_progress)` 作为唯一扫描入口，差异点参数注入。**两个函数 80% 相同 = 必须合并**。

## 踩坑记录

### modifier_apply 的 active 依赖

`bpy.ops.object.modifier_apply` 要求目标物体是 `context.view_layer.objects.active`。批量应用需逐个设置 active，**完成后恢复原始 active**（否则隐式上下文污染导致后续操作异常）。

### orphan_purge 计数不可靠

`bpy.ops.outliner.orphans_purge()` 清理的数据类型远超 meshes+materials，统计数量是误导。直接报告"已清理"即可。

### 列表最多 500 个

Blender UI 每行都创建 Operator 按钮实例，超大量物体时 Panel 渲染卡。500 是经验值，超出建议导出报表查看。

## 扩展方向

- **Collection 筛选**：按 Collection 过滤扫描范围
- **实时扫描进度**：Modal Operator + Timer 实现真正的逐帧扫描
- **Blender Extensions 兼容**：补 `blender_manifest.toml` 可发布到官方扩展平台

## 开发环境

- Blender 3.0 – 5.1+（无构建工具，单文件插件）
- 测试：偏好设置安装 → N 键侧边栏打开面板；提交规范 `feat:/fix:/perf:/refactor:/chore:/docs:`
