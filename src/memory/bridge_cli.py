from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from uuid import UUID

from memory.daemon import build_client, close_client_resources, load_hermes_env
from memory.models import Session, normalize_platform
from memory.recall import (
    delete_session,
    export_sessions,
    find_live_session_route,
    list_live_session_routes,
    load_session_transcript,
    list_all_sessions,
    list_named_sessions,
    list_recent_sessions,
    normalize_memory_session_id,
    normalize_current_session_id,
    prune_sessions,
    resolve_session_reference,
    search_sessions,
    session_stats,
    update_session_title,
)


def _load_stdin_json(default: object) -> object:
    raw = sys.stdin.read()
    if not raw.strip():
        return default
    return json.loads(raw)


def _parse_started_at(value: str) -> datetime:
    started_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if started_at.tzinfo is None:
        return started_at.replace(tzinfo=timezone.utc)
    return started_at.astimezone(timezone.utc)


async def _ensure_live_session(
    client,
    *,
    memory_session_id: str,
    hermes_session_id: str,
    platform: str,
    started_at: str,
    model: str | None,
    agent_namespace: str | None,
):
    existing = await client.transport.get_session(memory_session_id)
    if existing is not None:
        return existing

    session = Session(
        id=UUID(memory_session_id),
        agent_namespace=agent_namespace,
        platform=normalize_platform(platform),
        legacy_session_id=hermes_session_id,
        started_at=_parse_started_at(started_at),
        model=model,
        message_count=0,
        user_message_count=0,
        tool_call_count=0,
        topics=[],
        dominant_emotions=[],
        dominant_emotion_counts={},
    )
    try:
        return await client.transport.insert_session(session)
    except Exception:
        existing = await client.transport.get_session(memory_session_id)
        if existing is None:
            raise
        return existing


async def _run_enrich(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        context = await client.enrich_context(
            args.user_message,
            platform=args.platform,
            active_session_id=normalize_current_session_id(args.active_session_id),
            agent_namespace=args.agent_namespace,
        )
        return {"success": True, "context": context}
    finally:
        await close_client_resources(client)


async def _run_session_search(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        if args.query:
            return await search_sessions(
                client,
                query=args.query,
                role_filter=args.role_filter,
                limit=args.limit,
                current_session_id=args.current_session_id,
                platform=args.platform,
                agent_namespace=args.agent_namespace,
            )
        return await list_recent_sessions(
            client,
            limit=args.limit,
            current_session_id=args.current_session_id,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_resolve(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await resolve_session_reference(
            client,
            reference=args.reference,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_list(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        if args.all_sessions:
            return await list_all_sessions(
                client,
                limit=args.limit,
                platform=args.platform,
                agent_namespace=args.agent_namespace,
            )
        if args.named_only:
            return await list_named_sessions(
                client,
                limit=args.limit,
                platform=args.platform,
                agent_namespace=args.agent_namespace,
            )
        return await list_recent_sessions(
            client,
            limit=args.limit,
            current_session_id=None,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_transcript(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await load_session_transcript(
            client,
            reference=args.reference,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_title(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await update_session_title(
            client,
            reference=args.reference,
            title=args.title,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_delete(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await delete_session(
            client,
            reference=args.reference,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_export(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await export_sessions(
            client,
            reference=args.reference,
            platform=args.platform,
            limit=args.limit,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_prune(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await prune_sessions(
            client,
            older_than_days=args.older_than,
            platform=args.platform,
            limit=args.limit,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_stats(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await session_stats(
            client,
            platform=args.platform,
            limit=args.limit,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_routes(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await list_live_session_routes(
            client,
            platform=args.platform,
            limit=args.limit,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_session_route_find(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        return await find_live_session_route(
            client,
            platform=args.platform,
            chat_id=args.chat_id,
            thread_id=args.thread_id,
            session_key=args.session_key,
            limit=args.limit,
            agent_namespace=args.agent_namespace,
        )
    finally:
        await close_client_resources(client)


async def _run_live_session_sync(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        await _ensure_live_session(
            client,
            memory_session_id=args.memory_session_id,
            hermes_session_id=args.hermes_session_id,
            platform=args.platform,
            started_at=args.started_at,
            model=args.model,
            agent_namespace=args.agent_namespace,
        )
        updates = _load_stdin_json({})
        if not isinstance(updates, dict):
            raise ValueError("live-session-sync expects a JSON object on stdin.")
        stored = await client.transport.update_session(args.memory_session_id, updates)
        return {
            "success": True,
            "backend": "memory",
            "session_id": str(stored.id or args.memory_session_id),
            "message": "Memory live session metadata synced.",
        }
    finally:
        await close_client_resources(client)


async def _run_live_session_append(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        await _ensure_live_session(
            client,
            memory_session_id=args.memory_session_id,
            hermes_session_id=args.hermes_session_id,
            platform=args.platform,
            started_at=args.started_at,
            model=args.model,
            agent_namespace=args.agent_namespace,
        )
        messages = _load_stdin_json([])
        if not isinstance(messages, list):
            raise ValueError("live-session-append expects a JSON array on stdin.")
        stored = await client.store_messages_batch(
            args.memory_session_id,
            messages,
            platform=args.platform,
            agent_namespace=args.agent_namespace,
        )
        curator = await client.curate_live_continuity(
            args.memory_session_id,
            agent_namespace=args.agent_namespace,
            mode="hot",
        )
        return {
            "success": True,
            "backend": "memory",
            "session_id": args.memory_session_id,
            "count": len(stored),
            "curator": curator,
            "message": f"Stored {len(stored)} memory live message(s).",
        }
    finally:
        await close_client_resources(client)


async def _run_live_session_end(args: argparse.Namespace) -> dict[str, object]:
    client = build_client()
    try:
        await _ensure_live_session(
            client,
            memory_session_id=args.memory_session_id,
            hermes_session_id=args.hermes_session_id,
            platform=args.platform,
            started_at=args.started_at,
            model=args.model,
            agent_namespace=args.agent_namespace,
        )
        payload = _load_stdin_json({})
        if not isinstance(payload, dict):
            raise ValueError("live-session-end expects a JSON object on stdin.")
        end_reason = payload.get("end_reason")
        summary = payload.get("summary")
        if end_reason is not None:
            await client.transport.update_session(
                args.memory_session_id,
                {
                    "legacy_session_id": args.hermes_session_id,
                    "end_reason": end_reason,
                },
            )
        ended = await client.end_session(args.memory_session_id, summary=summary)
        curator = await client.curate_live_continuity(
            args.memory_session_id,
            agent_namespace=args.agent_namespace,
            mode="warm",
            force=True,
        )
        return {
            "success": True,
            "backend": "memory",
            "session_id": str(ended.id or args.memory_session_id),
            "curator": curator,
            "message": "Memory live session ended.",
        }
    finally:
        await close_client_resources(client)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m memory.bridge_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_agent_namespace_arg(target: argparse.ArgumentParser) -> None:
        target.add_argument("--agent-namespace")

    enrich = subparsers.add_parser("enrich")
    enrich.add_argument("--user-message", required=True)
    enrich.add_argument("--platform", default="local")
    enrich.add_argument("--active-session-id")
    _add_agent_namespace_arg(enrich)

    session_search = subparsers.add_parser("session-search")
    session_search.add_argument("--query", default="")
    session_search.add_argument("--role-filter")
    session_search.add_argument("--limit", type=int, default=3)
    session_search.add_argument("--current-session-id")
    session_search.add_argument("--platform")
    _add_agent_namespace_arg(session_search)

    session_resolve = subparsers.add_parser("session-resolve")
    session_resolve.add_argument("--reference", required=True)
    session_resolve.add_argument("--platform")
    _add_agent_namespace_arg(session_resolve)

    session_list = subparsers.add_parser("session-list")
    session_list.add_argument("--limit", type=int, default=10)
    session_list.add_argument("--platform")
    session_list.add_argument("--named-only", action="store_true")
    session_list.add_argument("--all-sessions", action="store_true")
    _add_agent_namespace_arg(session_list)

    session_transcript = subparsers.add_parser("session-transcript")
    session_transcript.add_argument("--reference", required=True)
    session_transcript.add_argument("--platform")
    _add_agent_namespace_arg(session_transcript)

    session_title = subparsers.add_parser("session-title")
    session_title.add_argument("--reference", required=True)
    session_title.add_argument("--title")
    session_title.add_argument("--platform")
    _add_agent_namespace_arg(session_title)

    session_delete = subparsers.add_parser("session-delete")
    session_delete.add_argument("--reference", required=True)
    session_delete.add_argument("--platform")
    _add_agent_namespace_arg(session_delete)

    session_export = subparsers.add_parser("session-export")
    session_export.add_argument("--reference")
    session_export.add_argument("--platform")
    session_export.add_argument("--limit", type=int, default=1000)
    _add_agent_namespace_arg(session_export)

    session_prune = subparsers.add_parser("session-prune")
    session_prune.add_argument("--older-than", type=int, default=90)
    session_prune.add_argument("--platform")
    session_prune.add_argument("--limit", type=int, default=5000)
    _add_agent_namespace_arg(session_prune)

    session_stats_parser = subparsers.add_parser("session-stats")
    session_stats_parser.add_argument("--platform")
    session_stats_parser.add_argument("--limit", type=int, default=5000)
    _add_agent_namespace_arg(session_stats_parser)

    session_routes = subparsers.add_parser("session-routes")
    session_routes.add_argument("--platform")
    session_routes.add_argument("--limit", type=int, default=1000)
    _add_agent_namespace_arg(session_routes)

    session_route_find = subparsers.add_parser("session-route-find")
    session_route_find.add_argument("--platform", required=True)
    session_route_find.add_argument("--chat-id", required=True)
    session_route_find.add_argument("--thread-id")
    session_route_find.add_argument("--session-key")
    session_route_find.add_argument("--limit", type=int, default=1000)
    _add_agent_namespace_arg(session_route_find)

    live_session_sync = subparsers.add_parser("live-session-sync")
    live_session_sync.add_argument("--hermes-session-id", required=True)
    live_session_sync.add_argument("--memory-session-id", required=True)
    live_session_sync.add_argument("--platform", required=True)
    live_session_sync.add_argument("--started-at", required=True)
    live_session_sync.add_argument("--model")
    _add_agent_namespace_arg(live_session_sync)

    live_session_append = subparsers.add_parser("live-session-append")
    live_session_append.add_argument("--hermes-session-id", required=True)
    live_session_append.add_argument("--memory-session-id", required=True)
    live_session_append.add_argument("--platform", required=True)
    live_session_append.add_argument("--started-at", required=True)
    live_session_append.add_argument("--model")
    _add_agent_namespace_arg(live_session_append)

    live_session_end = subparsers.add_parser("live-session-end")
    live_session_end.add_argument("--hermes-session-id", required=True)
    live_session_end.add_argument("--memory-session-id", required=True)
    live_session_end.add_argument("--platform", required=True)
    live_session_end.add_argument("--started-at", required=True)
    live_session_end.add_argument("--model")
    _add_agent_namespace_arg(live_session_end)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_hermes_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "enrich":
        result = asyncio.run(_run_enrich(args))
    elif args.command == "session-search":
        result = asyncio.run(_run_session_search(args))
    elif args.command == "session-resolve":
        result = asyncio.run(_run_session_resolve(args))
    elif args.command == "session-list":
        result = asyncio.run(_run_session_list(args))
    elif args.command == "session-transcript":
        result = asyncio.run(_run_session_transcript(args))
    elif args.command == "session-title":
        result = asyncio.run(_run_session_title(args))
    elif args.command == "session-delete":
        result = asyncio.run(_run_session_delete(args))
    elif args.command == "session-export":
        result = asyncio.run(_run_session_export(args))
    elif args.command == "session-prune":
        result = asyncio.run(_run_session_prune(args))
    elif args.command == "session-stats":
        result = asyncio.run(_run_session_stats(args))
    elif args.command == "session-routes":
        result = asyncio.run(_run_session_routes(args))
    elif args.command == "session-route-find":
        result = asyncio.run(_run_session_route_find(args))
    elif args.command == "live-session-sync":
        result = asyncio.run(_run_live_session_sync(args))
    elif args.command == "live-session-append":
        result = asyncio.run(_run_live_session_append(args))
    elif args.command == "live-session-end":
        result = asyncio.run(_run_live_session_end(args))
    else:
        parser.error(f"Unknown command: {args.command}")
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
