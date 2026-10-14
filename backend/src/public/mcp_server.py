import json

from mcp import types
from mcp.server.lowlevel.server import Server

from src.generation.prompts import GLOSSARY, ORG_PRIMER
from src.generation.tools import TOOLS, Limits

# Клієнти MCP тримають результати в контексті всю розмову, а Claude Code
# відкидає відповідь інструмента понад ~25 тис. токенів. Із заміру: 37 тис.
# символів пройшло, 64 тис. — ні; фрагмент у середньому ~1 360 символів.
LIMITS = Limits(search=8, document_part=30_000)


async def list_tools(_ctx, _params) -> types.ListToolsResult:
    tools = []
    for name, spec in TOOLS.items():
        for decl in spec.declaration.function_declarations or []:
            schema = (
                decl.parameters.json_schema.model_dump(mode="json", exclude_none=True)
                if decl.parameters
                else {"type": "object"}
            )
            tools.append(
                types.Tool(
                    name=name,
                    title=spec.label,
                    description=decl.description,
                    input_schema=schema,
                    # Інструменти лише читають. Gemini без цієї позначки
                    # просить підтвердження на кожен виклик.
                    annotations=types.ToolAnnotations(read_only_hint=True),
                )
            )
    return types.ListToolsResult(tools=tools)


async def call_tool(ctx, params) -> types.CallToolResult:
    spec = TOOLS[params.name]
    deps = ctx.request.app.state
    result = await spec.run(deps, params.arguments or {}, LIMITS)
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
        ],
        is_error="error" in result,
    )


mcp = Server(
    "scribe-rag",
    title="BEST Lviv",
    description=(
        "База знань і довідник членів BEST Lviv: процеси, документи, івенти, "
        "люди, посади, статистика складу."
    ),
    instructions=ORG_PRIMER + GLOSSARY,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)
