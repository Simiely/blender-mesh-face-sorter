# 开发文档

记录关键设计决策、踩坑与解法，供后续迭代参考。

## 架构

```
mesh_face_sorter.py  (~860 行，单文件插件)
│
├─ _Cache                 缓存层（核心）
├─ _ScanStatus            扫描进度状态
├─ _scan_meshes()         统一扫描入口
├─ collect_mesh_stats()   缓存 + 排序
├─ _estimate_mesh_size()  内存占用估算
├─ format_number/format_size  数字格式化
├─ _display_width/_truncate_name  CJK 字符串截断
│
├─ Operators (11 个)
│   ├─ Refresh             刷新（带进度条）
│   ├─ Select / SelectAll  选中
│   ├─ Isolate / ShowAll   孤立显示
│   ├─ DeleteEmpty         删除空网格
│   ├─ ExportMd            导出 md 报表
│   ├─ AddDecimate         批量减面
│   ├─ AddDecimateToObject 单个减面
│   ├─ ApplyDecimate       应用减面修改器
│   └─ PurgeOrphanData     清理未使用数据
│
├─ MESH_PT_FaceSortPanel   Panel UI
├─ _on_load_post           文件加载时清缓存
└─ register() / unregister()
```

## 关键决策

### 1. 手动刷新 vs 自动监听

**问题**：`depsgraph_update_post` 每帧触发，自动刷新会导致大场景卡死。

**决策**：纯手动刷新。增删物体后用户点「刷新列表」才重扫。唯一的自动失效是 `load_post`（换文件清缓存）。

**思考**：自动化的代价并不总是值得的。在 Blender Panel 的 `draw()` 高频调用模型下，让用户掌控刷新时机比"智能"自动刷新更可靠。

### 2. 缓存粒度

**问题**：切换排序方式需要重新扫描吗？

**决策**：缓存存原始扫描数据（stats），排序在 `collect_mesh_stats()` 中即时完成。切换排序方式不重扫，只重排缓存。

**思考**：扫描是 O(n) 且昂贵（遍历所有物体、读 mesh 数据），排序是 O(n log n) 但便宜（纯内存操作）。将昂贵操作与廉价操作解耦，让廉价操作自由切换。

### 3. 存储大小估算

**问题**：Blender 没有 `obj.size_in_bytes` API。

**决策**：基于网格数据组件估算 — 顶点(24B) + 边(8B) + 面(8B) + 循环(8B) + UV(8B/条) + 顶点色(16B/条)。非精确值，但相对排序足够。

**思考**：没有精确 API 时，可接受的近似比什么都不做更好。这个值用于"找出最占资源的模型"，相对比较才是核心需求。

### 4. 删除空网格的 UNDO 陷阱

**问题**：`bl_options = {'REGISTER', 'UNDO'}` + `bpy.data.objects.remove()` 导致撤销时缓存引用失效，触发连锁 ReferenceError。

**尝试**：`invalidate_cache()` 在删除前调用、拓宽异常捕获到 `Exception` —— 仍不稳定。

**最终**：移除所有删除相关代码，用户手动 Delete 后刷新即可。

**思考**：Blender 的 UNDO 系统对 `bpy.data` 的操作有深层耦合。当 UNDO 恢复物体时，缓存中的 Python 引用变成野指针。与其修复，不如避免——让 Blender 原生 Delete 处理，插件只负责刷新。

### 5. Panel 内选中状态反馈

**问题**：列表行如何区分选中/未选中？

**决策**：`row.active = is_selected` 让未选中行发灰，选中行加 `▶` 前缀 + `OBJECT_DATA` 图标 + `emboss=True`。

**思考**：`row.active` 是 Blender 原生的"禁用"语义，但视觉效果恰好是灰显，很适合做"非选中"的视觉区分。利用原生机制而非自建高亮逻辑。

### 6. CJK 字符串宽度计算

**问题**：中文名称在 Blender UI 中占用宽度是英文字符的 2 倍，固定字符截断会导致显示参差不齐。

**决策**：`_display_width()` 判断 `ord(ch) > 0x2E80`，CJK 字符算 2 宽度，其余算 1。`_truncate_name()` 按显示宽度截断。

**思考**：`0x2E80` 是 CJK Radicals Supplement 的起点，覆盖了中日韩统一表意文字及扩展区。`…`（U+2026）被正确识别为宽度 1，不会出现截断误差。

### 7. 代码去重：_scan_meshes 统一入口

**问题**：`Refresh.execute` 和初始扫描有重复的扫描逻辑。

**决策**：`_scan_meshes(with_progress, on_progress)` 作为唯一扫描入口，通过回调参数适配不同场景。

**思考**：当两个函数有 80% 相同的代码时，不是"差不多"，而是"必须合并"。差异点通过参数注入，保持单一真相来源。

## 踩坑记录

### bpy.ops.object.modifier_apply 的 active 依赖

`modifier_apply` 要求目标物体是 `context.view_layer.objects.active`。批量应用时需逐个设置 active，并在完成后恢复原始 active。不恢复可能导致后续操作异常的"隐式上下文污染"。

### orphan_purge 计数不可靠

`bpy.ops.outliner.orphans_purge()` 清理的数据类型远超 meshes + materials。试图统计清理数量只会得到误导性数字。直接报告"已清理"即可。

### 列表最多 500 个

Blender UI 每行都会创建 Operator 按钮实例，超大量物体时 Panel 渲染本身会卡。500 是经验值，超出建议导出报表查看。

## 扩展方向

- **Collection 筛选**：按 Collection 过滤扫描范围
- **实时扫描进度**：Modal Operator + Timer 实现真正的逐帧扫描
- **Blender Extensions 兼容**：补 `blender_manifest.toml` 可发布到官方扩展平台

## 提交规范

```
feat: 新功能
fix: 修复
perf: 性能优化
refactor: 重构
chore: 工程相关（重命名、版本号等）
docs: 文档
```