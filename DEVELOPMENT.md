# 开发文档 (DEVELOPMENT.md)

本文档面向想要理解、修改或扩展 Mesh Face Sorter 插件的开发者。

## 架构概览

```
__init__.py  (单文件插件，约 740 行)
│
├─ bl_info                        # 插件元信息
├─ _Cache 类                      # 缓存层（解决大场景卡顿）
├─ _ScanStatus 类                 # 扫描进度状态
├─ _scan_meshes()                 # 核心扫描函数
├─ collect_mesh_stats()           # 带缓存的排序入口
├─ format_number()                # 数字格式化（K/M）
│
├─ Operators（9 个）              # 所有用户操作
│   ├─ MESH_OT_FaceSortRefresh          # 刷新（带进度条）
│   ├─ MESH_OT_FaceSortSelect           # 选中单个物体
│   ├─ MESH_OT_FaceSortSelectAll        # 全选
│   ├─ MESH_OT_FaceSortIsolate          # 孤立显示
│   ├─ MESH_OT_FaceSortShowAll          # 显示全部
│   ├─ MESH_OT_FaceSortDeleteEmpty      # 删除空网格
│   ├─ MESH_OT_FaceSortExportMd         # 导出 md 报表
│   ├─ MESH_OT_FaceSortAddDecimate      # 批量加简面修改器
│   └─ MESH_OT_FaceSortAddDecimateToObject  # 单个加简面
│
├─ MESH_PT_FaceSortPanel          # 侧边栏面板（UI）
│
├─ _on_load_post 处理器           # 文件加载时清缓存
│
└─ register() / unregister()      # 注册/反注册
```

## 核心设计

### 1. 缓存层 `_Cache`

**问题**：Blender Panel 的 `draw()` 方法每秒被调用几十次。如果每次都扫描整个场景，大场景会卡死。

**解决方案**：全局缓存，只在显式触发时重新扫描。

```python
class _Cache:
    stats = None      # 缓存的统计列表
    dirty = True      # 是否需要重新扫描

    @classmethod
    def has_data(cls):
        return cls.stats is not None

    @classmethod
    def store(cls, stats):
        cls.stats = stats
        cls.dirty = False
```

**缓存失效时机**：
- 点「刷新列表」按钮 → `force=True`
- 加载新 Blender 文件 → `_on_load_post` 处理器
- 删除空网格 / 孤立显示后（这些操作改变了场景）

**缓存保留时机**：
- 切换排序方式（只重新排序缓存，不扫描）
- 切换升降序
- Panel 每帧重绘

### 2. 扫描进度 `_ScanStatus`

**问题**：扫描几千个物体时，用户不知道还要等多久。

**解决方案**：全局状态对象 + Blender 原生进度条 + UI 进度条。

```python
class _ScanStatus:
    is_scanning = False
    current = 0       # 已扫描数
    total = 0         # 总数
    percent = 0       # 百分比
    message = ""      # 状态文案
```

**三种状态**：
- **未扫描**：`未扫描，请点击刷新列表`
- **扫描中**：`扫描中... 1234/5678 (21%)` + UI 进度条
- **扫描完成**：`✓ 扫描完成：1234 个网格体（14:23:05）`

**注意**：Operator 的 `execute` 是同步阻塞的，扫描过程中 `tag_redraw` 不会真正实时刷新 UI。进度状态主要在扫描**完成后**显示。若要真正实时刷新，需改成 Modal Operator + Timer（见后续扩展方向）。

### 3. 三角面计算优化

**之前（慢）**：
```python
tris_count = sum(len(p.vertices) - 2 for p in mesh.polygons)
# 10 万面 → Python 循环 10 万次
```

**现在（快）**：
```python
tris_count = len(mesh.loop_triangles)
# C 实现，直接读取预计算结果
```

`mesh.loop_triangles` 是 Blender 内部维护的三角面缓存，访问 `len()` 几乎零开销。若需确保是最新的，调用 `mesh.calc_loop_triangles()`。

### 4. 手动刷新模式

**设计决策**：不做自动监听场景变化（不挂 `depsgraph_update_post`），改为纯手动刷新。

**原因**：
- `depsgraph_update_post` 每帧都会触发（视口旋转、物体移动都会触发），导致缓存频繁失效
- 自动失效 + 自动重算 = 回到每帧扫描的卡顿问题
- 手动刷新更可控，用户知道何时在扫描

**唯一的自动失效**：`load_post`（加载新文件时清缓存，避免显示旧文件数据）。

## 关键 API 说明

### 物体遍历

```python
for obj in bpy.data.objects:        # 遍历所有物体
    if obj.type == 'MESH':          # 只处理网格体
        mesh = obj.data
        faces = len(mesh.polygons)  # 面数
        verts = len(mesh.vertices)  # 顶点数
        tris = len(mesh.loop_triangles)  # 三角面数
```

### 选中状态

```python
obj.select_get()       # 获取选中状态（比 obj.select 更可靠）
obj.select_set(True)   # 选中物体
bpy.context.view_layer.objects.active = obj  # 设为活动对象
```

### 隐藏状态

```python
obj.hide_get()         # 获取隐藏状态
obj.hide_set(True)     # 隐藏物体
obj.visible_get()      # 是否可见（考虑视口隐藏/渲染隐藏/集合隐藏）
```

### 修改器操作

```python
# 检查是否已有同名修改器
for mod in obj.modifiers:
    if mod.name == "Decimate" and mod.type == 'DECIMATE':
        # 已存在，跳过
        ...

# 添加新修改器
mod = obj.modifiers.new(name="Decimate", type='DECIMATE')
mod.decimate_type = 'COLLAPSE'    # 'COLLAPSE' / 'UNSUBDIV' / 'DISSOLVE'
mod.ratio = 0.5                   # 保留比例
mod.use_collapse_triangulate = False
```

### Blender 原生进度条

```python
wm = context.window_manager
wm.progress_begin(0, total)        # 开始
wm.progress_update(current)        # 更新
wm.progress_end()                  # 结束
```

### UI 进度条（Panel 内）

```python
box.progress(
    factor=0.5,                    # 0.0 ~ 1.0
    text="50%  (1234/5678)",
)
```

### 文件对话框

```python
class MyOperator(bpy.types.Operator):
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        # 用户选好文件后执行
        with open(self.filepath, 'w') as f:
            f.write(...)
        return {'FINISHED'}
```

### 确认对话框

```python
class MyOperator(bpy.types.Operator):
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.label(text="确认删除？")

    def execute(self, context):
        # 用户点确认后执行
        ...
        return {'FINISHED'}
```

## 兼容性策略

### 5.0/5.1 Breaking Changes 应对

| Blender 变更 | 本插件应对 |
|---|---|
| `bpy.props` 不支持 dict-like 访问 | 全部用属性访问 `scene.xxx` |
| BGL 模块移除 | 未使用 bgl |
| Grease Pencil 重命名 | 未使用 |
| Python 3.13 | 无不兼容语法 |
| Node Tools 全局唯一 idname | 未使用 Node Tools |

### 使用的稳定 API（2.8+ 全部支持）

- `bpy.types.Panel` / `Operator` 标准注册
- `bpy.data.objects` 遍历
- `obj.data.polygons` / `vertices` / `edges` 网格访问
- `obj.modifiers.new()` 修改器操作
- `obj.select_set()` / `obj.hide_set()` 物体状态
- `bpy.app.handlers.load_post` 文件加载处理器
- `wm.progress_begin/update/end` 进度条
- `wm.invoke_props_dialog` / `fileselect_add` 对话框

### 向后兼容扩展（4.2+ Extensions）

如果未来要发布到 Blender Extensions 平台，补一个 `blender_manifest.toml`：

```toml
schema_version = "1.0.0"
id = "mesh_face_sorter"
version = "1.4.0"
name = "Mesh Face Sorter"
tagline = "按面数排列网格体，支持减面、孤立显示、导出报表"
maintainer = "Simiely"
type = "add-on"
blender_version_min = "3.0.0"
license = ["SPDX:MIT"]
```

当前传统安装方式（`bl_info` + Install）在 5.1 仍完全支持，不急着迁移。

## 扩展方向

### 1. 实时扫描进度（Modal Operator）

当前扫描是同步的，要改成实时刷新 UI：

```python
class MESH_OT_FaceSortRefresh(bpy.types.Operator):
    def modal(self, context, event):
        if event.type == 'TIMER':
            # 每帧扫描一批物体
            for i in range(50):
                if self.index < len(self.objects):
                    self._scan_one(self.objects[self.index])
                    self.index += 1
                else:
                    self._finish()
                    return {'FINISHED'}
            _ScanStatus.update(self.index)
            return {'PASS_THROUGH'}
        return {'PASS_THROUGH'}

    def execute(self, context):
        wm = context.window_manager
        self.timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}
```

### 2. 自定义 Decimate 参数

当前 ratio 硬编码 0.5，可以加 UI 滑块：

```python
# 注册到 Scene
bpy.types.Scene.mesh_face_sorter_decimate_ratio = bpy.props.FloatProperty(
    name="简面比例",
    default=0.5, min=0.01, max=1.0,
)

# Panel 里
row.prop(scene, "mesh_face_sorter_decimate_ratio", text="比例")

# Operator 里
op.ratio = scene.mesh_face_sorter_decimate_ratio
```

### 3. 按 Collection 筛选

```python
# 注册到 Scene
bpy.types.Scene.mesh_face_sorter_collection = bpy.props.PointerProperty(
    type=bpy.types.Collection,
)

# 扫描时筛选
col = scene.mesh_face_sorter_collection
if col:
    objects = [o for o in col.objects if o.type == 'MESH']
else:
    objects = [o for o in bpy.data.objects if o.type == 'MESH']
```

### 4. 批量应用修改器

```python
class MESH_OT_ApplyAllDecimate(bpy.types.Operator):
    """应用所有 Decimate 修改器（破坏性）"""
    def execute(self, context):
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                for mod in list(obj.modifiers):
                    if mod.type == 'DECIMATE':
                        bpy.context.view_layer.objects.active = obj
                        bpy.ops.object.modifier_apply(modifier=mod.name)
        return {'FINISHED'}
```

## 开发调试

### 本地安装测试

```bash
# 1. 直接在 Blender 里安装 __init__.py
# 2. 修改代码后，在 Blender 的 Python Console 里重载：
import importlib
import mesh_face_sorter  # 或实际的模块名
importlib.reload(mesh_face_sorter)

# 3. 或者删除旧插件重新安装
```

### 查看日志

```bash
# macOS
tail -f ~/Library/Application\ Support/Blender/4.x/scripts/logs/blender.log

# Linux
tail -f ~/.config/blender/4.x/scripts/logs/blender.log
```

### 语法检查（不用 Blender）

```bash
python3 -c "import ast; ast.parse(open('__init__.py').read()); print('OK')"
```

### 兼容性检查清单

每次改动后检查：
- [ ] 无 `context.scene['xxx']` dict-like 访问
- [ ] 无 `import bgl`
- [ ] 无 `bpy.app.handlers.depsgraph_update_post` 自动监听（避免卡顿）
- [ ] Operator 类名不与 Blender 内置类型冲突
- [ ] `bl_info` 的 `blender` 字段设为 `(3, 0, 0)`（最低支持版本）
- [ ] `register()` / `unregister()` 成对，处理器正确移除

## 提交规范

```
feat: 新增功能描述
fix: 修复问题描述
perf: 性能优化描述
refactor: 重构描述
docs: 文档更新
```

## 文件结构

```
mesh-face-sorter/
├── __init__.py          # 插件主文件（约 740 行）
├── README.md            # 用户文档
└── DEVELOPMENT.md       # 本文档（开发文档）
```
