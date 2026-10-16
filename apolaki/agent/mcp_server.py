"""Apolaki Memory MCP Server — exposes memory store as MCP tools.

Runs as a stdio MCP server for the Apolaki scaffold engine.
Tools: store, search, graph_query, graph_stats, wiki_page, context_prompt
"""
from __future__ import annotations

import json
import sys
import logging
from datetime import datetime, timezone

from .memory_store import ApolakiMemoryStore

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("apolaki-memory")

store = ApolakiMemoryStore()

TOOLS = [
    {
        "name": "apolaki_store_memory",
        "description": "Store a new memory. Use for brand decisions, campaign results, audience insights, content performance, strategic context — anything worth remembering across sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The memory content"},
                "memory_type": {
                    "type": "string",
                    "enum": ["observation", "decision", "brand", "campaign", "audience", "content", "strategy", "team", "project"],
                    "default": "observation",
                },
                "significance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "source": {"type": "string", "default": ""},
            },
            "required": ["content"],
        },
    },
    {
        "name": "apolaki_search_memory",
        "description": "Search memories semantically with TGS-verified graph retrieval. Use for recalling brand voice, past campaigns, team context, project details, strategic decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "n_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                "memory_type": {"type": "string", "description": "Filter by type"},
                "use_graph": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
    {
        "name": "apolaki_graph_stats",
        "description": "Knowledge graph statistics — entity counts, top entities, triple counts. Use for understanding what Apolaki knows structurally.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "apolaki_wiki_page",
        "description": "Generate or retrieve a wiki page for an entity. Creates interlinked markdown from the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_name": {"type": "string", "description": "Entity to generate wiki page for"},
            },
            "required": ["entity_name"],
        },
    },
    {
        "name": "apolaki_context_prompt",
        "description": "Get a session context prompt with top brand memories, recent decisions, and active campaigns. Load at session start.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle_tool(name: str, args: dict) -> str:
    if name == "apolaki_store_memory":
        mid = store.store(
            content=args["content"],
            memory_type=args.get("memory_type", "observation"),
            significance=args.get("significance", 0.5),
            tags=args.get("tags", []),
            source=args.get("source", ""),
        )
        stats = store.stats()
        return json.dumps({
            "stored": mid,
            "total_memories": stats["memories"],
            "total_entities": stats["entities"],
        })

    elif name == "apolaki_search_memory":
        results = store.search(
            query=args["query"],
            n_results=args.get("n_results", 5),
            memory_type=args.get("memory_type"),
            use_graph=args.get("use_graph", True),
        )
        return json.dumps({
            "results": [
                {
                    "content": r.memory.content,
                    "type": r.memory.memory_type,
                    "significance": r.memory.significance,
                    "score": round(r.score, 4),
                    "source": r.source,
                    "verification": r.verification,
                    "graph_entities": r.graph_entities,
                    "tags": r.memory.tags,
                    "created_at": r.memory.created_at,
                }
                for r in results
            ],
            "count": len(results),
        })

    elif name == "apolaki_graph_stats":
        return json.dumps(store.stats())

    elif name == "apolaki_wiki_page":
        page = store.generate_wiki_page(args["entity_name"])
        if page:
            return page
        return f"No entity found for '{args['entity_name']}'"

    elif name == "apolaki_context_prompt":
        # Build session context from high-significance memories
        brand = store.search("brand voice principles identity", n_results=3, memory_type="brand", use_graph=False)
        strategy = store.search("current strategy priorities campaigns", n_results=3, memory_type="strategy", use_graph=False)
        recent = store.search("recent decisions outcomes", n_results=3, use_graph=False)

        sections = ["## Session Context (from memory)"]

        if brand:
            sections.append("\n### Brand Voice")
            for r in brand:
                sections.append(f"- {r.memory.content[:200]}")

        if strategy:
            sections.append("\n### Active Strategy")
            for r in strategy:
                sections.append(f"- {r.memory.content[:200]}")

        if recent:
            sections.append("\n### Recent Context")
            for r in recent:
                sections.append(f"- [{r.memory.memory_type}] {r.memory.content[:200]}")

        stats = store.stats()
        sections.append(f"\n*Memory: {stats['memories']} memories, {stats['entities']} entities, {stats['triples']} graph edges*")

        return "\n".join(sections)

    return json.dumps({"error": f"Unknown tool: {name}"})


# ── MCP stdio protocol ──

def read_message() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line.strip())
    except json.JSONDecodeError:
        return None


def write_message(msg: dict):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    logger.info("Apolaki Memory MCP starting...")
    stats = store.stats()
    logger.info("Loaded: %d memories, %d entities, %d triples", stats["memories"], stats["entities"], stats["triples"])

    while True:
        msg = read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        req_id = msg.get("id")

        if method == "initialize":
            write_message({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "apolaki-memory", "version": "1.0.0"},
                },
            })

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            write_message({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            try:
                result = handle_tool(tool_name, tool_args)
                write_message({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result}],
                        "isError": False,
                    },
                })
            except Exception as e:
                logger.exception("Tool error: %s", tool_name)
                write_message({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })

        elif method == "ping":
            write_message({"jsonrpc": "2.0", "id": req_id, "result": {}})

        else:
            if req_id is not None:
                write_message({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


if __name__ == "__main__":
    main()
