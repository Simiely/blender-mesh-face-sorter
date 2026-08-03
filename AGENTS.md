# AGENTS.md · 项目规则

> 写给 AI / 未来维护者的项目上下文。只记录代码里看不出的信息。

## 技术栈

- Blender Python API（**Blender 3.0 – 5.1+**），单文件插件 `mesh_face_sorter.py`（~860 行）
- 面板：侧边栏（N 键）MESH_PT_FaceSortPanel；Operator 用 `bl_options = {'REGISTER', 'UNDO'}`
- 提交规范：`feat:` / `fix:` / `perf:` / `refactor:` / `chore:` / `docs:`

## 关键坑（改代码前必读）

1. **纯手动刷新模式**：`depsgraph_update_post` 每帧触发，自动刷新会让大场景卡死。增删物体后用户点「刷新列表」才重扫；唯一自动失效是 `load_post`（换文件清缓存）。**不要改回自动刷新**
2. **UNDO 与 `bpy.data` 引用陷阱**：`bpy.data.objects.remove()` + UNDO 恢复物体时，缓存中的 Python 引用变野指针 → ReferenceError。**不要在 Operator 里删除物体**，让 Blender 原生 Delete 处理，插件只负责刷新
3. **`bpy.ops.object.modifier_apply` 依赖 active**：应用减面修改器前必须把目标物体设为 `context.view_layer.objects.active`，批量应用逐个设置，**完成后恢复原始 active**（否则隐式上下文污染）
4. **CJK 字符串宽度**：中文名称占 2 个字符宽度，固定字符截断会参差。用 `_display_width()`（`ord(ch) > 0x2E80` 算 2）+ `_truncate_name()` 按显示宽度截断
5. **缓存粒度**：扫描 O(n) 昂贵、排序 O(n log n) 便宜——缓存存原始 stats，排序在 `collect_mesh_stats()` 即时完成，切换排序不重扫

## 约定

- 扫描/缓存统一走 `_scan_meshes()`（唯一入口，回调参数适配）；`invalidate_cache()` 使缓存失效
- 列表最多显示 500 个（超出建议导出报表）；UI 用 `row.active` 原生机制做选中态灰显
- 存储大小是**估算值**（顶点 24B + 边 8B + 面 8B + 循环 8B + UV 8B/条 + 顶点色 16B/条），用于相对比较
- 新功能参考 DEVELOPMENT.md「扩展方向」（Collection 筛选 / Modal 实时进度 / Extensions 兼容）

## 常用命令

- 无构建；测试 = Blender 偏好设置安装插件后启用，3D 视图 N 键打开面板
- 详细开发记录见 DEVELOPMENT.md；版本历史见 CHANGELOG.md
