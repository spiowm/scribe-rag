import html
import json

from mcp import types
from mcp.server.lowlevel.server import Server

from src.config import settings
from src.generation.prompts import MCP_ABOUT, MCP_INSTRUCTIONS, MCP_SEARCH_RULES
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
                    description=(decl.description or "")
                    + (MCP_SEARCH_RULES if name == "search_knowledge_base" else ""),
                    input_schema=schema,
                    # Інструменти лише читають. Gemini без цієї позначки
                    # просить підтвердження на кожен виклик.
                    annotations=types.ToolAnnotations(read_only_hint=True),
                )
            )
    return types.ListToolsResult(tools=tools)


def _attrs(**values) -> str:
    return " ".join(
        f'{k}="{html.escape(str(v), quote=True)}"' for k, v in values.items() if v
    )


def search_as_text(results: list[dict]) -> str:
    """Фрагменти, згруповані за документом, у тегах <document>.

    Теги на кожен документ радять і Anthropic, і Google. У JSON модель бачила б
    екрановані переноси й лапки, а title, url і path — у кожному фрагменті знову:
    на 8 фрагментах текст коротший на 13–19%. Markdown-роздільники не годяться:
    сторінки Notion самі повні `#` і `---`.
    """
    docs: dict[str, list[dict]] = {}
    for item in results:
        docs.setdefault(item["source_id"], []).append(item)
    blocks = []
    for index, (source_id, items) in enumerate(docs.items(), 1):
        first = items[0]
        head = _attrs(
            index=index,
            title=first["title"],
            url=first["url"],
            path=first.get("path"),
            source_id=source_id,
        )
        # Фрагменти одного документа не обовʼязково сусідні — розрив позначено.
        body = "\n\n[…]\n\n".join(item["text"] for item in items)
        blocks.append(f"<document {head}>\n{body}\n</document>")
    return "\n\n".join(blocks)


def document_as_text(result: dict, source_id: str) -> str:
    head = _attrs(
        title=result["title"],
        url=result["url"],
        path=result.get("path"),
        source_id=source_id,
        last_edited=result.get("last_edited"),
        part=f"{result['part']} з {result['parts_total']}",
    )
    return f"<document {head}>\n{result['text']}\n</document>"


async def call_tool(ctx, params) -> types.CallToolResult:
    spec = TOOLS[params.name]
    deps = ctx.request.app.state
    args = params.arguments or {}
    result = await spec.run(deps, args, LIMITS)
    if params.name == "search_knowledge_base" and "results" in result:
        text = search_as_text(result["results"]) or "Нічого не знайдено."
    elif params.name == "get_document" and "text" in result:
        text = document_as_text(result, args.get("source_id", ""))
    else:
        text = json.dumps(result, ensure_ascii=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        is_error="error" in result,
    )


mcp = Server(
    # Назва, за якою агент вирішує, чи кликати сервер: «scribe» нічого не каже
    # про BEST, і питання про жаргон осередку йшли повз базу.
    "best-lviv",
    title="BEST Lviv",
    website_url="https://best-lviv.org.ua",
    icons=[
        types.Icon(
            src=f"{settings.PUBLIC_URL}/icon.png",
            mime_type="image/png",
            sizes=["512x512"],
        )
    ],
    description=MCP_ABOUT,
    instructions=MCP_INSTRUCTIONS,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)
