"""editor 工具组：IR → Resolve 时间线 / FCPXML / 剪辑清单。"""

from app.ir import exporters
from app.ir.schema import validate_ir
from app.tools.registry import registry

_IR_PARAM = {
    "type": "object",
    "properties": {"ir": {"type": "object", "description": "Editing IR JSON"}},
    "required": ["ir"],
}


@registry.register(
    name="create_resolve_timeline",
    description="在 DaVinci Resolve 中按 Editing IR 创建项目、导入素材、生成时间线与字幕。需要 Resolve 正在运行。",
    parameters=_IR_PARAM,
)
def create_resolve_timeline(ir: dict) -> dict:
    from app.adapters.resolve_adapter import execute_ir

    parsed = validate_ir(ir)  # 执行前二次校验（设计文档 7.2）
    return execute_ir(parsed)


@registry.register(
    name="export_fcpxml",
    description="将 Editing IR 导出为 FCPXML（可手动导入 Resolve/FCP 的备用路径）。",
    parameters=_IR_PARAM,
)
def export_fcpxml_tool(ir: dict) -> dict:
    parsed = validate_ir(ir, check_paths=False)
    return {"fcpxml": exporters.export_fcpxml(parsed)}


@registry.register(
    name="export_edit_list",
    description="将 Editing IR 导出为人类可读的 Markdown 剪辑清单（降级路径）。",
    parameters=_IR_PARAM,
)
def export_edit_list_tool(ir: dict) -> dict:
    parsed = validate_ir(ir, check_paths=False)
    return {"markdown": exporters.export_edit_list(parsed)}
