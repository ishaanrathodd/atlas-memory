from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from memory.bridge_cli import _ensure_live_session
from memory.daemon import build_client, close_client_resources, load_hermes_env
from memory.recall import (
    find_live_session_route,
    list_live_session_routes,
    load_session_transcript,
    normalize_current_session_id,
    resolve_session_reference,
)


async def _handle_request(client, request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request.get("operation") or "").strip()
    if operation == "ping":
        return {"success": True, "result": {"success": True, "message": "pong"}}

    if operation == "enrich":
        context = await client.enrich_context(
            str(request.get("user_message") or ""),
            platform=str(request.get("platform") or "local"),
            active_session_id=normalize_current_session_id(request.get("active_session_id")),
            agent_namespace=request.get("agent_namespace"),
        )
        return {"success": True, "result": {"success": True, "context": context}}

    if operation == "session-routes":
        result = await list_live_session_routes(
            client,
            platform=request.get("platform"),
            limit=int(request.get("limit") or 1000),
            agent_namespace=request.get("agent_namespace"),
        )
        return {"success": True, "result": result}

    if operation == "session-resolve":
        result = await resolve_session_reference(
            client,
            reference=str(request.get("reference") or ""),
            platform=request.get("platform"),
            agent_namespace=request.get("agent_namespace"),
        )
        return {"success": True, "result": result}

    if operation == "session-transcript":
        result = await load_session_transcript(
            client,
            reference=str(request.get("reference") or ""),
            platform=request.get("platform"),
            agent_namespace=request.get("agent_namespace"),
        )
        return {"success": True, "result": result}

    if operation == "session-route-find":
        result = await find_live_session_route(
            client,
            platform=str(request.get("platform") or ""),
            chat_id=str(request.get("chat_id") or ""),
            thread_id=request.get("thread_id"),
            session_key=request.get("session_key"),
            limit=int(request.get("limit") or 1000),
            agent_namespace=request.get("agent_namespace"),
        )
        return {"success": True, "result": result}

    if operation == "live-session-sync":
        await _ensure_live_session(
            client,
            memory_session_id=str(request.get("memory_session_id") or ""),
            hermes_session_id=str(request.get("hermes_session_id") or ""),
            platform=str(request.get("platform") or "local"),
            started_at=str(request.get("started_at") or ""),
            model=request.get("model"),
            agent_namespace=request.get("agent_namespace"),
        )
        updates = request.get("updates")
        if not isinstance(updates, dict):
            raise ValueError("live-session-sync expects updates to be a JSON object.")
        stored = await client.transport.update_session(str(request.get("memory_session_id") or ""), updates)
        return {
            "success": True,
            "result": {
                "success": True,
                "backend": "memory",
                "session_id": str(stored.id or request.get("memory_session_id") or ""),
                "message": "Memory live session metadata synced.",
            },
        }

    if operation == "live-session-append":
        await _ensure_live_session(
            client,
            memory_session_id=str(request.get("memory_session_id") or ""),
            hermes_session_id=str(request.get("hermes_session_id") or ""),
            platform=str(request.get("platform") or "local"),
            started_at=str(request.get("started_at") or ""),
            model=request.get("model"),
            agent_namespace=request.get("agent_namespace"),
        )
        messages = request.get("messages")
        if not isinstance(messages, list):
            raise ValueError("live-session-append expects messages to be a JSON array.")
        stored = await client.store_messages_batch(
            str(request.get("memory_session_id") or ""),
            messages,
            platform=str(request.get("platform") or "local"),
            agent_namespace=request.get("agent_namespace"),
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "backend": "memory",
                "session_id": str(request.get("memory_session_id") or ""),
                "count": len(stored),
                "message": f"Stored {len(stored)} memory live message(s).",
            },
        }

    if operation == "live-session-end":
        await _ensure_live_session(
            client,
            memory_session_id=str(request.get("memory_session_id") or ""),
            hermes_session_id=str(request.get("hermes_session_id") or ""),
            platform=str(request.get("platform") or "local"),
            started_at=str(request.get("started_at") or ""),
            model=request.get("model"),
            agent_namespace=request.get("agent_namespace"),
        )
        end_reason = request.get("end_reason")
        summary = request.get("summary")
        if end_reason is not None:
            await client.transport.update_session(
                str(request.get("memory_session_id") or ""),
                {
                    "legacy_session_id": str(request.get("hermes_session_id") or ""),
                    "end_reason": end_reason,
                },
            )
        ended = await client.end_session(str(request.get("memory_session_id") or ""), summary=summary)
        return {
            "success": True,
            "result": {
                "success": True,
                "backend": "memory",
                "session_id": str(ended.id or request.get("memory_session_id") or ""),
                "message": "Memory live session ended.",
            },
        }

    raise ValueError(f"Unsupported memory bridge server operation: {operation}")


async def _serve() -> int:
    load_hermes_env()
    client = build_client()
    try:
        while True:
            raw = await asyncio.to_thread(sys.stdin.readline)
            if not raw:
                break
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Memory bridge server expected a JSON object request.")
                response = await _handle_request(client, request)
            except Exception as exc:
                response = {"success": False, "error": str(exc)}
            sys.stdout.write(json.dumps(response, ensure_ascii=False))
            sys.stdout.write("\n")
            sys.stdout.flush()
    finally:
        await close_client_resources(client)
    return 0


def main() -> int:
    return asyncio.run(_serve())


if __name__ == "__main__":
    raise SystemExit(main())
