bl_info = {
    "name": "Mesh Face Sorter",
    "author": "Simiely",
    "version": (1, 4, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Mesh Sorter",
    "description": "按面数/顶点/三角面排列场景中的网格体，支持孤立显示、删除空网格、导出 md 报表、一键加简面修改器（手动刷新 + 进度提示）",
    "warning": "",
    "doc_url": "https://github.com/Simiely/mesh-face-sorter",
    "category": "Mesh",
}

import bpy
import datetime


# -----------------------------------------------------------------------------
# 缓存层 — 解决大场景卡顿的关键
# -----------------------------------------------------------------------------


class _Cache:
    """简单的全局缓存。避免 Panel 每帧重新扫描场景。
    纯手动刷新模式：只有点「刷新」按钮或加载新文件后才重新扫描。
    切换排序方式时只重新排序缓存数据，不重新扫描。
    """
    stats = None            # 缓存的统计列表（已扫描的原始数据）
    dirty = True            # 是否需要重新扫描

    @classmethod
    def invalidate(cls):
        """标记缓存需要重新扫描。"""
        cls.dirty = True
        cls.stats = None

    @classmethod
    def has_data(cls):
        """是否已有扫描数据（不论是否过期）。"""
        return cls.stats is not None

    @classmethod
    def store(cls, stats):
        cls.stats = stats
        cls.dirty = False


def _scan_meshes(with_progress=False):
    """扫描所有网格体并收集统计信息（不含排序）。
    这是耗时操作，只在手动刷新或首次打开时调用。
    with_progress=True 时更新 _ScanStatus 进度状态。
    """
    all_objects = list(bpy.data.objects)
    total = len(all_objects)
    if with_progress:
        _ScanStatus.reset(total)

    stats = []
    for i, obj in enumerate(all_objects, 1):
        if obj.type == 'MESH':
            mesh = obj.data
            face_count = len(mesh.polygons)
            vert_count = len(mesh.vertices)
            edge_count = len(mesh.edges)
            # 三角面：使用 loop_triangles（C 实现，比 Python 循环快几十倍）
            tris_count = len(mesh.loop_triangles)
            stats.append({
                "object": obj,
                "name": obj.name,
                "faces": face_count,
                "vertices": vert_count,
                "edges": edge_count,
                "tris": tris_count,
                "selected": obj.select_get(),
                "visible": obj.visible_get(),
                "hidden": obj.hide_get(),
            })
        if with_progress:
            _ScanStatus.update(i)

    if with_progress:
        _ScanStatus.finish(len(stats))
    return stats


def collect_mesh_stats(sort_by='FACES', descending=True, force=False):
    """获取排序后的网格体统计信息。

    纯手动刷新模式：
    - force=True → 重新扫描场景（点「刷新」按钮时）
    - 缓存为空（首次打开/加载新文件）→ 自动扫描一次（不显示进度，避免每次切排序都触发）
    - 缓存有数据 → 直接用缓存重新排序（切换排序方式时，不重新扫描）
    """
    if force or not _Cache.has_data():
        # force 时带进度条；首次自动扫描时不带（静默）
        stats = _scan_meshes(with_progress=force)
        _Cache.store(stats)
    else:
        stats = _Cache.stats

    # 按指定字段排序（每次都排，因为排序很快，且可能切换了排序方式）
    key_map = {
        'FACES': "faces",
        'VERTS': "vertices",
        'TRIS': "tris",
    }
    # 复制一份再排序，避免污染缓存原始顺序
    sorted_stats = sorted(stats, key=lambda x: x[key_map[sort_by]], reverse=descending)
    return sorted_stats


def invalidate_cache():
    """外部调用：标记缓存失效。"""
    _Cache.invalidate()


class _ScanStatus:
    """扫描状态，供 Panel 实时显示进度。"""
    is_scanning = False
    current = 0          # 已扫描的物体数
    total = 0            # 总物体数（含非网格体，作为分母）
    percent = 0          # 百分比 0-100
    message = ""         # 状态文案
    last_scanned_count = 0   # 上次扫描得到的网格体数
    last_scan_time = ""      # 上次扫描完成时间

    @classmethod
    def reset(cls, total):
        cls.is_scanning = True
        cls.current = 0
        cls.total = total
        cls.percent = 0
        cls.message = "扫描中..."

    @classmethod
    def update(cls, current):
        cls.current = current
        if cls.total > 0:
            cls.percent = int(current * 100 / cls.total)
            cls.message = f"扫描中... {current}/{cls.total} ({cls.percent}%)"

    @classmethod
    def finish(cls, count):
        cls.is_scanning = False
        cls.current = cls.total
        cls.percent = 100
        cls.last_scanned_count = count
        cls.last_scan_time = datetime.datetime.now().strftime('%H:%M:%S')
        cls.message = f"扫描完成：{count} 个网格体（{cls.last_scan_time}）"

    @classmethod
    def idle(cls):
        """未扫描时的初始状态。"""
        cls.message = "未扫描，请点击「刷新列表」"


# 初始化状态
_ScanStatus.idle()


def format_number(n):
    if n >= 1000000:
        return f"{n / 1000000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


# -----------------------------------------------------------------------------
# Operators - 基础操作
# -----------------------------------------------------------------------------


class MESH_OT_FaceSortRefresh(bpy.types.Operator):
    bl_idname = "mesh_face_sorter.refresh"
    bl_label = "刷新列表"
    bl_description = "重新扫描场景中的所有网格体（带进度提示）"

    def execute(self, context):
        all_objects = list(bpy.data.objects)
        total = len(all_objects)

        # 启动 Blender 原生进度条
        wm = context.window_manager
        wm.progress_begin(0, total)

        # 重置扫描状态
        _ScanStatus.reset(total)

        # 触发 loop_triangles 预计算（让后面读取更快）
        # 同时更新进度
        stats = []
        for i, obj in enumerate(all_objects, 1):
            if obj.type == 'MESH':
                try:
                    obj.data.calc_loop_triangles()
                except Exception:
                    pass
                mesh = obj.data
                stats.append({
                    "object": obj,
                    "name": obj.name,
                    "faces": len(mesh.polygons),
                    "vertices": len(mesh.vertices),
                    "edges": len(mesh.edges),
                    "tris": len(mesh.loop_triangles),
                    "selected": obj.select_get(),
                    "visible": obj.visible_get(),
                    "hidden": obj.hide_get(),
                })
            # 更新进度
            _ScanStatus.update(i)
            wm.progress_update(i)
            # 每扫描 50 个物体刷新一次 UI（避免过度刷新卡顿）
            if i % 50 == 0 or i == total:
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()

        # 完成
        _Cache.store(stats)
        _ScanStatus.finish(len(stats))
        wm.progress_end()

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        self.report({'INFO'}, f"扫描完成：{len(stats)} 个网格体")
        return {'FINISHED'}


class MESH_OT_FaceSortSelect(bpy.types.Operator):
    bl_idname = "mesh_face_sorter.select_object"
    bl_label = "选中物体"
    bl_description = "在场景中选中该物体"

    object_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        if bpy.context.view_layer.objects.active != obj:
            bpy.context.view_layer.objects.active = obj
        return {'FINISHED'}


class MESH_OT_FaceSortSelectAll(bpy.types.Operator):
    bl_idname = "mesh_face_sorter.select_all"
    bl_label = "选中所有网格体"
    bl_description = "选中列表中的全部网格体"

    def execute(self, context):
        bpy.ops.object.select_all(action='DESELECT')
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                obj.select_set(True)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Operators - 孤立显示
# -----------------------------------------------------------------------------


class MESH_OT_FaceSortIsolate(bpy.types.Operator):
    """孤立显示：仅显示该物体，隐藏其他所有网格体"""
    bl_idname = "mesh_face_sorter.isolate"
    bl_label = "孤立显示"
    bl_description = "仅显示该物体，隐藏场景中其他所有网格体"

    object_name: bpy.props.StringProperty()

    def execute(self, context):
        target = bpy.data.objects.get(self.object_name)
        if not target or target.type != 'MESH':
            self.report({'WARNING'}, f"未找到网格体：{self.object_name}")
            return {'CANCELLED'}
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                obj.hide_set(obj != target)
        # 隐藏状态变化，刷新缓存中的 hidden 字段
        invalidate_cache()
        self.report({'INFO'}, f"已孤立显示：{target.name}")
        return {'FINISHED'}


class MESH_OT_FaceSortShowAll(bpy.types.Operator):
    """取消所有网格体的隐藏"""
    bl_idname = "mesh_face_sorter.show_all"
    bl_label = "显示全部"
    bl_description = "取消所有网格体的隐藏"

    def execute(self, context):
        count = 0
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.hide_get():
                obj.hide_set(False)
                count += 1
        invalidate_cache()
        self.report({'INFO'}, f"已显示 {count} 个隐藏的网格体")
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Operators - 删除无面网格体
# -----------------------------------------------------------------------------


class MESH_OT_FaceSortDeleteEmpty(bpy.types.Operator):
    """删除所有面数为 0 的空网格体"""
    bl_idname = "mesh_face_sorter.delete_empty"
    bl_label = "删除无面网格体"
    bl_description = "删除场景中所有面数为 0 的空网格体"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and len(obj.data.polygons) == 0:
                return True
        return False

    def execute(self, context):
        empty_objs = [
            obj for obj in bpy.data.objects
            if obj.type == 'MESH' and len(obj.data.polygons) == 0
        ]
        count = len(empty_objs)
        if count == 0:
            self.report({'INFO'}, "没有空网格体")
            return {'CANCELLED'}
        for obj in empty_objs:
            bpy.data.objects.remove(obj, do_unlink=True)
        invalidate_cache()
        self.report({'INFO'}, f"已删除 {count} 个空网格体")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        empty_count = sum(
            1 for obj in bpy.data.objects
            if obj.type == 'MESH' and len(obj.data.polygons) == 0
        )
        self.layout.label(text=f"将删除 {empty_count} 个面数为 0 的网格体")


# -----------------------------------------------------------------------------
# Operators - 导出 Markdown 报表
# -----------------------------------------------------------------------------


class MESH_OT_FaceSortExportMd(bpy.types.Operator):
    """导出当前列表为 Markdown 报表"""
    bl_idname = "mesh_face_sorter.export_md"
    bl_label = "导出 md 报表"
    bl_description = "将当前网格体列表导出为 Markdown 表格文件"

    filepath: bpy.props.StringProperty(
        subtype='FILE_PATH',
        default="mesh_report.md",
    )

    def execute(self, context):
        scene = context.scene
        # 导出时强制重新计算（确保数据最新）
        stats = collect_mesh_stats(
            sort_by=scene.mesh_face_sorter_sort_by,
            descending=scene.mesh_face_sorter_descending,
            force=True,
        )
        if not stats:
            self.report({'WARNING'}, "场景中没有网格体可导出")
            return {'CANCELLED'}

        path = self.filepath
        if not path.lower().endswith('.md'):
            path += '.md'

        total_faces = sum(s["faces"] for s in stats)
        total_tris = sum(s["tris"] for s in stats)
        total_verts = sum(s["vertices"] for s in stats)

        sort_label = {
            'FACES': '面数', 'VERTS': '顶点数', 'TRIS': '三角面数'
        }[scene.mesh_face_sorter_sort_by]
        order_label = '降序' if scene.mesh_face_sorter_descending else '升序'

        lines = []
        lines.append("# 网格体报表")
        lines.append("")
        lines.append(f"- **生成时间**：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"- **排序方式**：{sort_label}（{order_label}）")
        lines.append(f"- **网格体总数**：{len(stats)}")
        lines.append(f"- **总面数**：{total_faces}")
        lines.append(f"- **总三角面**：{total_tris}")
        lines.append(f"- **总顶点**：{total_verts}")
        lines.append("")
        lines.append("| # | 物体名称 | 面数 | 顶点数 | 三角面 | 边数 | 选中 | 隐藏 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, s in enumerate(stats, 1):
            lines.append(
                f"| {i} | {s['name']} | {s['faces']} | {s['vertices']} "
                f"| {s['tris']} | {s['edges']} | {'是' if s['selected'] else '否'} "
                f"| {'是' if s['hidden'] else '否'} |"
            )
        lines.append("")

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
        except Exception as e:
            self.report({'ERROR'}, f"导出失败：{e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"已导出 {len(stats)} 个网格体到：{path}")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# -----------------------------------------------------------------------------
# Operators - Decimate 修改器
# -----------------------------------------------------------------------------


DECIMATE_MODIFIER_NAME = "Decimate"


def add_decimate_to_object(obj, ratio=0.5):
    if obj.type != 'MESH':
        return False, None
    for mod in obj.modifiers:
        if mod.name == DECIMATE_MODIFIER_NAME and mod.type == 'DECIMATE':
            return False, mod
    mod = obj.modifiers.new(name=DECIMATE_MODIFIER_NAME, type='DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = ratio
    mod.use_collapse_triangulate = False
    return True, mod


class MESH_OT_FaceSortAddDecimate(bpy.types.Operator):
    bl_idname = "mesh_face_sorter.add_decimate"
    bl_label = "一键添加简面修改器"
    bl_description = "给当前选中的所有网格体添加 Decimate 修改器（Collapse 模式，保留 50% 面数）"

    ratio: bpy.props.FloatProperty(
        name="保留比例",
        default=0.5,
        min=0.01,
        max=1.0,
    )

    def execute(self, context):
        selected = [o for o in context.selected_objects if o.type == 'MESH']
        if not selected:
            self.report({'WARNING'}, "请先选中至少一个网格体")
            return {'CANCELLED'}
        added = 0
        skipped = 0
        for obj in selected:
            ok, _ = add_decimate_to_object(obj, self.ratio)
            if ok:
                added += 1
            else:
                skipped += 1
        msg = f"已添加简面修改器：{added} 个物体"
        if skipped:
            msg += f"（跳过 {skipped} 个已存在）"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class MESH_OT_FaceSortAddDecimateToObject(bpy.types.Operator):
    bl_idname = "mesh_face_sorter.add_decimate_to_object"
    bl_label = "添加简面"
    bl_description = "给该物体添加 Decimate 修改器（无需先选中）"

    object_name: bpy.props.StringProperty()
    ratio: bpy.props.FloatProperty(default=0.5, min=0.01, max=1.0)

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, f"未找到网格体：{self.object_name}")
            return {'CANCELLED'}
        ok, _ = add_decimate_to_object(obj, self.ratio)
        if ok:
            self.report({'INFO'}, f"已添加简面修改器：{obj.name}")
        else:
            self.report({'INFO'}, f"已存在简面修改器，跳过：{obj.name}")
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Panel
# -----------------------------------------------------------------------------


class MESH_PT_FaceSortPanel(bpy.types.Panel):
    bl_label = "面数排序"
    bl_idname = "MESH_PT_FaceSortPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Mesh Sorter'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        sort_by = scene.mesh_face_sorter_sort_by
        descending = scene.mesh_face_sorter_descending
        is_scanning = _ScanStatus.is_scanning

        # 扫描状态区（顶部最醒目位置）
        status_box = layout.box()
        if is_scanning:
            # 扫描中：显示进度条 + 百分比 + 当前/总数
            status_box.label(text=_ScanStatus.message, icon='SORTTIME')
            # Blender 原生进度条（UI 控件）
            status_box.progress(
                factor=_ScanStatus.percent / 100.0,
                text=f"{_ScanStatus.percent}%  ({_ScanStatus.current}/{_ScanStatus.total})",
            )
        else:
            # 非扫描状态：显示上次扫描结果
            if _ScanStatus.last_scanned_count > 0:
                status_box.label(
                    text=f"✓ {_ScanStatus.message}",
                    icon='CHECKMARK',
                )
            else:
                status_box.label(
                    text=_ScanStatus.message,
                    icon='INFO',
                )

        # 扫描中时禁用其他操作（除了刷新按钮本身）
        # 使用 active=False 的方式：把后续 UI 放到一个 enabled 开关控制的块里

        # 使用缓存的数据（不会每帧重新扫描）
        stats = collect_mesh_stats(sort_by=sort_by, descending=descending)
        total_faces = sum(s["faces"] for s in stats)
        total_tris = sum(s["tris"] for s in stats)
        total_verts = sum(s["vertices"] for s in stats)

        # 统计区
        box = layout.box()
        box.enabled = not is_scanning
        box.label(text="手动刷新模式：增删物体后请点「刷新列表」", icon='INFO')
        row = box.row()
        row.label(text=f"网格体数量：{len(stats)}")
        row = box.row()
        row.label(text=f"总面数：{format_number(total_faces)}")
        row = box.row()
        row.label(text=f"总三角面：{format_number(total_tris)}")
        row = box.row()
        row.label(text=f"总顶点：{format_number(total_verts)}")

        # 排序方式切换
        box = layout.box()
        box.enabled = not is_scanning
        row = box.row()
        row.label(text="排序：")
        row.prop(scene, "mesh_face_sorter_sort_by", text="")
        row.prop(scene, "mesh_face_sorter_descending",
                 text="", icon='SORT_DESC' if descending else 'SORT_ASC',
                 toggle=True)

        # 基础操作按钮（刷新始终可用，其他扫描中禁用）
        row = layout.row()
        # 刷新按钮：扫描中显示为「扫描中...」并禁用
        if is_scanning:
            row.operator("mesh_face_sorter.refresh", text="扫描中...", icon='SORTTIME')
        else:
            row.operator("mesh_face_sorter.refresh", icon='FILE_REFRESH')
        row.enabled = not is_scanning
        row.operator("mesh_face_sorter.select_all", icon='SELECT_SET')

        row = layout.row()
        row.enabled = not is_scanning
        row.operator("mesh_face_sorter.show_all", icon='HIDE_OFF')
        row.operator("mesh_face_sorter.delete_empty", icon='TRASH')

        row = layout.row()
        row.enabled = not is_scanning
        row.operator("mesh_face_sorter.export_md", icon='EXPORT')

        # 一键添加简面修改器
        layout.separator()
        row = layout.row()
        row.scale_y = 1.4
        row.enabled = not is_scanning
        op = row.operator(
            "mesh_face_sorter.add_decimate",
            text="一键添加简面修改器",
            icon='MOD_DECIM',
        )
        op.ratio = 0.5

        layout.separator()

        if not stats:
            layout.label(text="场景中没有网格体", icon='INFO')
            return

        # 列表 — 限制最大显示数量，避免超多物体时 UI 卡顿
        MAX_DISPLAY = 500
        box = layout.box()
        box.enabled = not is_scanning
        col = box.column(align=True)

        sort_icon = 'SORT_DESC' if descending else 'SORT_ASC'
        header = col.row(align=True)
        header.label(text="#", icon=sort_icon)
        header.label(text="物体名称")
        if sort_by == 'FACES':
            header.label(text="面数*")
        elif sort_by == 'VERTS':
            header.label(text="顶点*")
        else:
            header.label(text="三角面*")
        header.label(text="", icon='HIDE_OFF')
        header.label(text="", icon='MOD_DECIM')

        col.separator()

        display_stats = stats[:MAX_DISPLAY]
        for i, s in enumerate(display_stats, 1):
            row = col.row(align=True)
            row.alignment = 'CENTER'
            row.label(text=str(i))
            op_name = row.operator(
                "mesh_face_sorter.select_object",
                text=s["name"],
                icon='OBJECT_DATA' if s["selected"] else 'MESH_DATA',
                emboss=False,
            )
            op_name.object_name = s["name"]

            if sort_by == 'FACES':
                row.label(text=format_number(s["faces"]))
            elif sort_by == 'VERTS':
                row.label(text=format_number(s["vertices"]))
            else:
                row.label(text=format_number(s["tris"]))

            op_iso = row.operator(
                "mesh_face_sorter.isolate",
                text="",
                icon='HIDE_OFF' if not s["hidden"] else 'HIDE_ON',
                emboss=True,
            )
            op_iso.object_name = s["name"]

            op_dec = row.operator(
                "mesh_face_sorter.add_decimate_to_object",
                text="",
                icon='MOD_DECIM',
                emboss=True,
            )
            op_dec.object_name = s["name"]
            op_dec.ratio = 0.5

        # 如果超出最大显示数量，提示
        if len(stats) > MAX_DISPLAY:
            col.separator()
            col.label(text=f"（仅显示前 {MAX_DISPLAY} 个，共 {len(stats)} 个网格体）",
                      icon='INFO')
            col.label(text="点击「刷新列表」可重新排序，或使用「导出 md 报表」查看全部",
                      icon='INFO')


# -----------------------------------------------------------------------------
# 应用处理器 — 仅在加载新文件时清缓存（不自动监听场景变化）
# -----------------------------------------------------------------------------


@bpy.app.handlers.persistent
def _on_load_post(dummy):
    """文件加载时清缓存，下次打开面板会重新扫描。"""
    _Cache.invalidate()


# -----------------------------------------------------------------------------
# 注册
# -----------------------------------------------------------------------------


classes = (
    MESH_OT_FaceSortRefresh,
    MESH_OT_FaceSortSelect,
    MESH_OT_FaceSortSelectAll,
    MESH_OT_FaceSortIsolate,
    MESH_OT_FaceSortShowAll,
    MESH_OT_FaceSortDeleteEmpty,
    MESH_OT_FaceSortExportMd,
    MESH_OT_FaceSortAddDecimate,
    MESH_OT_FaceSortAddDecimateToObject,
    MESH_PT_FaceSortPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mesh_face_sorter_sort_by = bpy.props.EnumProperty(
        name="排序方式",
        items=[
            ('FACES', "面数", "按面数排序"),
            ('VERTS', "顶点", "按顶点数排序"),
            ('TRIS', "三角面", "按三角面数排序"),
        ],
        default='FACES',
    )
    bpy.types.Scene.mesh_face_sorter_descending = bpy.props.BoolProperty(
        name="降序",
        default=True,
    )
    # 注册应用处理器（仅文件加载时清缓存）
    bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    # 移除应用处理器
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    del bpy.types.Scene.mesh_face_sorter_sort_by
    del bpy.types.Scene.mesh_face_sorter_descending
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
