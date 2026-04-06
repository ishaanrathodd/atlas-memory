from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from memory.bridge_cli import _ensure_live_session
from memory.heartbeat import build_response_profile, build_rhythm_profile, build_thread_emotion_profile, rank_due_opportunities
from memory.models import EpisodeRole
from memory.curator_runtime import build_client, close_client_resources, load_hermes_env
from memory.recall import (
    find_live_session_route,
    list_live_session_routes,
    load_session_transcript,
    normalize_current_session_id,
    resolve_session_reference,
)


def _parse_request_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _merge_session_updates(existing_session: Any, updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(updates)
    incoming_model_config = updates.get("model_config")
    if not isinstance(incoming_model_config, dict):
        return merged

    existing_model_config = getattr(existing_session, "session_model_config", None)
    if existing_model_config is None:
        existing_model_config = getattr(existing_session, "model_config", None)
    base_model_config = dict(existing_model_config) if isinstance(existing_model_config, dict) else {}
    merged_model_config = dict(base_model_config)
    incoming_copy = dict(incoming_model_config)

    base_routing = base_model_config.get("routing")
    incoming_routing = incoming_copy.get("routing")
    if isinstance(base_routing, dict) and isinstance(incoming_routing, dict):
        merged_routing = dict(base_routing)
        merged_routing.update(incoming_routing)
        incoming_copy["routing"] = merged_routing

    merged_model_config.update(incoming_copy)
    merged["model_config"] = merged_model_config
    return merged


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
        memory_session_id = str(request.get("memory_session_id") or "")
        await _ensure_live_session(
            client,
            memory_session_id=memory_session_id,
            hermes_session_id=str(request.get("hermes_session_id") or ""),
            platform=str(request.get("platform") or "local"),
            started_at=str(request.get("started_at") or ""),
            model=request.get("model"),
            agent_namespace=request.get("agent_namespace"),
        )
        updates = request.get("updates")
        if not isinstance(updates, dict):
            raise ValueError("live-session-sync expects updates to be a JSON object.")
        existing_session = await client.transport.get_session(memory_session_id)
        merged_updates = _merge_session_updates(existing_session, updates)
        stored = await client.transport.update_session(memory_session_id, merged_updates)
        return {
            "success": True,
            "result": {
                "success": True,
                "backend": "memory",
                "session_id": str(stored.id or memory_session_id),
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
        curator = await client.curate_live_continuity(
            str(request.get("memory_session_id") or ""),
            agent_namespace=request.get("agent_namespace"),
            mode="hot",
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "backend": "memory",
                "session_id": str(request.get("memory_session_id") or ""),
                "count": len(stored),
                "curator": curator,
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
        curator = await client.curate_live_continuity(
            str(request.get("memory_session_id") or ""),
            agent_namespace=request.get("agent_namespace"),
            mode="warm",
            force=True,
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "backend": "memory",
                "session_id": str(ended.id or request.get("memory_session_id") or ""),
                "curator": curator,
                "message": "Memory live session ended.",
            },
        }

    if operation == "presence-sync":
        stored = await client.record_presence_event(
            role=str(request.get("role") or ""),
            session_id=normalize_current_session_id(request.get("session_id")),
            platform=str(request.get("platform") or "") or None,
            occurred_at=_parse_request_datetime(request.get("occurred_at")),
            agent_namespace=request.get("agent_namespace"),
            thread_summary=request.get("thread_summary"),
            proactive=bool(request.get("proactive", False)),
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "presence_state": stored.model_dump(mode="json"),
            },
        }

    if operation == "heartbeat-poll":
        now = _parse_request_datetime(request.get("now"))
        presence = await client.refresh_presence(
            agent_namespace=request.get("agent_namespace"),
            now=now,
        )
        dropoff = await client.ensure_conversation_dropoff_opportunity(
            agent_namespace=request.get("agent_namespace"),
            now=now,
        )
        promise_followups = await client.ensure_promise_followup_opportunities(
            agent_namespace=request.get("agent_namespace"),
            now=now,
        )
        handoffs = await client.transport.list_session_handoffs(
            limit=20,
            agent_namespace=request.get("agent_namespace"),
        )
        opportunities = await client.list_heartbeat_opportunities(
            limit=int(request.get("limit") or 10),
            agent_namespace=request.get("agent_namespace"),
            statuses=["pending"],
        )
        recent_dispatches = await client.list_heartbeat_dispatches(
            limit=24,
            agent_namespace=request.get("agent_namespace"),
        )
        recent_episodes = await client.list_recent_episodes(
            limit=120,
            platform=str(getattr(presence.active_platform, "value", presence.active_platform))
            if presence is not None and presence.active_platform is not None
            else None,
            agent_namespace=request.get("agent_namespace"),
        )
        user_activity = [
            item.message_timestamp
            for item in recent_episodes
            if str(getattr(item.role, "value", item.role)) == EpisodeRole.USER.value
        ]
        rhythm_profile = build_rhythm_profile(user_activity, now=now)
        response_profile = build_response_profile(
            recent_dispatches,
            recent_episodes,
            now=now,
        )
        thread_emotion_profiles: dict[str, dict[str, Any]] = {}
        session_ids = {
            str(item.session_id)
            for item in opportunities
            if item.session_id is not None
        }
        for session_id in session_ids:
            session_episodes = await client.transport.list_episodes_for_session(session_id, limit=8)
            matching_handoff = next((item for item in handoffs if str(item.session_id) == session_id), None)
            thread_emotion_profiles[session_id] = build_thread_emotion_profile(
                session_episodes,
                handoff_tone=matching_handoff.emotional_tone if matching_handoff is not None else None,
                presence=presence if presence is not None and str(presence.active_session_id or "") == session_id else None,
            )
        due = rank_due_opportunities(
            opportunities,
            state=presence,
            recent_dispatches=recent_dispatches,
            rhythm_profile=rhythm_profile,
            response_profile=response_profile,
            thread_emotion_profiles=thread_emotion_profiles,
            now=now,
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "presence_state": presence.model_dump(mode="json") if presence is not None else None,
                "rhythm_profile": rhythm_profile,
                "response_profile": response_profile,
                "thread_emotion_profiles": thread_emotion_profiles,
                "created_opportunity": dropoff.model_dump(mode="json") if dropoff is not None else None,
                "promise_followups": [item.model_dump(mode="json") for item in promise_followups],
                "due_opportunities": due,
            },
        }

    if operation == "heartbeat-create-opportunity":
        kind = str(request.get("kind") or "").strip()
        if kind != "background_task_completion":
            raise ValueError(f"Unsupported heartbeat opportunity kind: {kind}")
        created = await client.create_background_task_completion_opportunity(
            session_id=str(request.get("session_id") or "") or None,
            reason_summary=str(request.get("reason_summary") or ""),
            agent_namespace=request.get("agent_namespace"),
            now=_parse_request_datetime(request.get("now")),
            priority_score=float(request.get("priority_score") or 0.8),
            source_refs=[
                str(item).strip()
                for item in (request.get("source_refs") or [])
                if str(item).strip()
            ],
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "opportunity": created.model_dump(mode="json"),
            },
        }

    if operation == "background-job-create":
        created = await client.create_background_job(
            title=str(request.get("title") or ""),
            session_id=str(request.get("session_id") or "") or None,
            agent_namespace=request.get("agent_namespace"),
            kind=str(request.get("kind") or "other"),
            description=str(request.get("description") or "") or None,
            priority_score=float(request.get("priority_score") or 0.6),
            source_refs=[
                str(item).strip()
                for item in (request.get("source_refs") or [])
                if str(item).strip()
            ],
            job_key=str(request.get("job_key") or "") or None,
            now=_parse_request_datetime(request.get("now")),
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "job": created.model_dump(mode="json"),
            },
        }

    if operation == "background-job-list":
        jobs = await client.list_background_jobs(
            limit=int(request.get("limit") or 10),
            agent_namespace=request.get("agent_namespace"),
            statuses=[
                str(item).strip()
                for item in (request.get("statuses") or [])
                if str(item).strip()
            ]
            or None,
            session_id=str(request.get("session_id") or "") or None,
            job_key=str(request.get("job_key") or "") or None,
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "jobs": [item.model_dump(mode="json") for item in jobs],
            },
        }

    if operation == "background-job-transition":
        job_key = str(request.get("job_key") or "").strip()
        if not job_key:
            raise ValueError("background-job-transition requires job_key.")
        status = str(request.get("status") or "").strip()
        if not status:
            raise ValueError("background-job-transition requires status.")
        job = await client.transition_background_job(
            job_key,
            status=status,
            agent_namespace=request.get("agent_namespace"),
            progress_note=str(request.get("progress_note") or "").strip() or None,
            completion_summary=str(request.get("completion_summary") or "").strip() or None,
            result_refs=[
                str(item).strip()
                for item in (request.get("result_refs") or [])
                if str(item).strip()
            ]
            or None,
            started_at=_parse_request_datetime(request.get("started_at")),
            completed_at=_parse_request_datetime(request.get("completed_at")),
            updated_at=_parse_request_datetime(request.get("updated_at")),
        )
        return {
            "success": True,
            "result": {
                "success": job is not None,
                "job": job.model_dump(mode="json") if job is not None else None,
            },
        }

    if operation == "background-job-complete":
        job_key = str(request.get("job_key") or "").strip()
        if not job_key:
            raise ValueError("background-job-complete requires job_key.")
        completed = await client.complete_background_job(
            job_key,
            agent_namespace=request.get("agent_namespace"),
            completion_summary=str(request.get("completion_summary") or ""),
            result_refs=[
                str(item).strip()
                for item in (request.get("result_refs") or [])
                if str(item).strip()
            ],
            create_heartbeat=bool(request.get("create_heartbeat", True)),
            now=_parse_request_datetime(request.get("now")),
            priority_score=float(request.get("priority_score") or 0.82),
        )
        return {
            "success": True,
            "result": {
                "success": completed.get("job") is not None,
                "job": completed["job"].model_dump(mode="json") if completed.get("job") is not None else None,
                "opportunity": (
                    completed["opportunity"].model_dump(mode="json")
                    if completed.get("opportunity") is not None
                    else None
                ),
            },
        }

    if operation == "heartbeat-dispatch-context":
        opportunity_key = str(request.get("opportunity_key") or "").strip()
        if not opportunity_key:
            raise ValueError("heartbeat-dispatch-context requires opportunity_key.")
        packet = await client.build_heartbeat_context(
            opportunity_key=opportunity_key,
            agent_namespace=request.get("agent_namespace"),
        )
        if packet is None:
            return {
                "success": True,
                "result": {
                    "success": False,
                    "error": "not_found",
                    "message": f"No heartbeat opportunity matched '{opportunity_key}'.",
                },
            }
        return {
            "success": True,
            "result": {
                "success": True,
                "packet": packet,
            },
        }

    if operation == "heartbeat-dispatch-cooldown":
        opportunity_key = str(request.get("opportunity_key") or "").strip()
        if not opportunity_key:
            raise ValueError("heartbeat-dispatch-cooldown requires opportunity_key.")
        result = await client.heartbeat_dispatch_cooldown(
            opportunity_key=opportunity_key,
            session_id=str(request.get("session_id") or "") or None,
            agent_namespace=request.get("agent_namespace"),
            now=_parse_request_datetime(request.get("now")),
        )
        return {"success": True, "result": result}

    if operation == "heartbeat-record-dispatch":
        opportunity_key = str(request.get("opportunity_key") or "").strip()
        if not opportunity_key:
            raise ValueError("heartbeat-record-dispatch requires opportunity_key.")
        status = str(request.get("dispatch_status") or "").strip()
        if not status:
            raise ValueError("heartbeat-record-dispatch requires dispatch_status.")
        dispatch = await client.record_heartbeat_dispatch(
            opportunity_key=opportunity_key,
            dispatch_status=status,
            agent_namespace=request.get("agent_namespace"),
            opportunity_kind=str(request.get("opportunity_kind") or "").strip() or None,
            session_id=str(request.get("session_id") or "") or None,
            target=str(request.get("target") or "").strip() or None,
            send_score=float(request.get("send_score")) if request.get("send_score") is not None else None,
            response_preview=str(request.get("response_preview") or "").strip() or None,
            failure_reason=str(request.get("failure_reason") or "").strip() or None,
            attempted_at=_parse_request_datetime(request.get("attempted_at")),
        )
        return {
            "success": True,
            "result": {
                "success": True,
                "dispatch": dispatch.model_dump(mode="json"),
            },
        }

    if operation == "heartbeat-transition-opportunity":
        opportunity_key = str(request.get("opportunity_key") or "").strip()
        status = str(request.get("status") or "").strip()
        changed = await client.transition_heartbeat_opportunity(
            opportunity_key,
            status=status,
            agent_namespace=request.get("agent_namespace"),
        )
        return {
            "success": True,
            "result": {
                "success": changed,
                "opportunity_key": opportunity_key,
                "status": status,
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
