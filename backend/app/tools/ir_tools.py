"""ir 工具组：Editing IR 校验与 JSON Schema 导出。"""

from app.ir.schema import EditingIR, IRValidationError, timeline_duration, validate_ir
from app.tools.registry import registry


@registry.register(
    name="validate_ir",
    description="校验 Editing IR：结构、素材引用、trim 范围、字幕重叠、文件存在性。",
    parameters={
        "type": "object",
        "properties": {
            "ir": {"type": "object", "description": "Editing IR JSON"},
            "check_paths": {"type": "boolean", "description": "是否校验素材文件存在，默认 true"},
        },
        "required": ["ir"],
    },
)
def validate_ir_tool(ir: dict, check_paths: bool = True) -> dict:
    try:
        parsed = validate_ir(ir, check_paths=check_paths)
        return {"valid": True, "errors": [], "timeline_duration": timeline_duration(parsed)}
    except IRValidationError as e:
        return {"valid": False, "errors": e.errors, "timeline_duration": None}


def export_json_schema() -> dict:
    return EditingIR.model_json_schema()
