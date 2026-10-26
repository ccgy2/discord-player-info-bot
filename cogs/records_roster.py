# -*- coding: utf-8 -*-
"""기존 bot.py에 넣는 단일 파일 등말소 Cog · v5.4.2-single.1

저장 위치: cogs/records_roster.py
- 보조 .py / _records_roster 폴더가 필요 없는 단일 파일입니다.
- 기존 cogs 자동 로더가 이 파일을 불러오면 시작합니다. bot.py를 재실행합니다.
- 자동 로더가 없는 봇은 기존 비동기 로더에서
  await bot.load_extension("cogs.records_roster")를 한 번 호출해야 합니다.
- 기존 discord.py 2.x, firebase-admin 설치가 필요합니다. 이 파일은 별도
  로그인, bot.run(), 토큰 설정, 패키지 설치를 하지 않습니다.
- 기존 Firebase 연결을 재사용합니다. 프로젝트/기록실이 모호하면 쓰지 않고
  로그로 안내합니다. 설정은 아래 값 또는 기존 RECORDS_ 환경변수를 사용합니다.
- 웹 기록실 v5.4.1/5.4.2의 승인 데이터와 호환됩니다. 글 작성만으로 역할이
  변경되지는 않으며, 웹에서 확인된 사용자-선수 연결과 승인이 필요합니다.
- 채널 1551385834995978301, 1군 1520484992017367120,
  2군 1520485077182582977. 승인 후 새 역할 확인 → 반대 역할 제거.
- 다른 역할은 건드리지 않습니다. 봇의 역할 관리 권한/역할 순서와 채널
  보기/메시지 기록 보기, MESSAGE CONTENT INTENT는 Discord에서 설정해야 합니다.
- 첫 연결 기본값은 새 공지부터 수집. 기존 커서가 있으면 누락분을 이어 읽습니다.
  과거 글 수집은 첫 연결 전에 RECORDS_BACKFILL_LIMIT=0~500을 지정합니다.
- 민감한 서비스 계정 키/봇 토큰을 이 파일에 적거나 공개 저장소에 올리지 마세요.
- 오프라인 회귀/모의 API 검사용으로 확인한 코드입니다. 이 제공 과정에서
  운영 Firebase 로그인·실제 역할 변경·실제 서버 배포는 실행하지 않았습니다.

추가 설정이 필요한 경우(환경변수 우선):
 RECORDS_BRIDGE_ENABLED=true/false
 RECORDS_FIREBASE_PROJECT_ID=기록실 프로젝트 ID
 RECORDS_SITE_ID=sst-dbo 또는 kmb-league
 RECORDS_FIREBASE_APP=기존에 초기화한 Firebase Admin 앱 이름(기본 [DEFAULT])
 다른 프로젝트 서비스 계정: RECORDS_FIREBASE_CLIENT_EMAIL,
 RECORDS_FIREBASE_PRIVATE_KEY. 이 키는 실행 환경의 비밀 변수로만 설정합니다.
 RECORDS_ROLE_SYNC_ENABLED=false: 수집만 유지하고 역할 반영 일시 중지.

기존 다중 파일 Cog를 교체한다면 cogs/records_roster.py만 교체하고 재시작하세요.
옛 직접 시작(records_roster_bridge)과 이 Cog를 동시에 실행하지 않습니다.
공식 문서: https://discordpy.readthedocs.io/en/stable/ext/commands/extensions.html
           https://firebase.google.com/docs/reference/admin/python/firebase_admin
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from discord.ext import commands

# ── 필요할 때만 수정하는 설정 (비밀 키를 쓰는 곳이 아닙니다) ──
RECORDS_ENABLED = True
RECORDS_PROJECT_ID = ""   # 빈칸: 기존 초기화된 Firebase 프로젝트가 하나일 때 재사용
RECORDS_SITE_ID = ""      # 빈칸: 기존 DB의 SST/KMB meta/site가 정확히 하나일 때 선택
FIREBASE_APP_NAME = "[DEFAULT]"
STARTUP_RETRY_SECONDS = 60
COG_VERSION = "5.4.2-single.1"
LOGGER = logging.getLogger(__name__)

# ═══════════════ 승인된 군 역할 변경 · 기존 v5.4.2 프로토콜 ═══════════════
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")
DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,180}$")
NONCE = re.compile(r"^[a-f0-9]{32}$")
SQUADS = {"first", "second", "unassigned"}
ROLE_NAMES = {"first": "1군", "second": "2군"}
# Use the server owner's explicit IDs. Labels are for display only: a renamed
# role remains the same role, and another role with its old name is unrelated.
ROLE_IDS = {"first": 1520484992017367120, "second": 1520485077182582977}
LEASE_SECONDS = 90
HTTP_TIMEOUT_SECONDS = 20
MIN_LEASE_SECONDS = 30
EMPTY_LEASE = datetime(1970, 1, 1, tzinfo=timezone.utc)
ACTIVE_STATUSES = {"pending", "applying", "retry"}


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value if isinstance(value, datetime) and value.tzinfo is not None else EMPTY_LEASE


def document_id(value):
    if not isinstance(value, str) or not DOCUMENT_ID.fullmatch(value):
        raise ValueError("invalid-document-id")
    return value


def identity_of(job):
    return tuple(job.get(field) for field in ("id", "actionId", "playerId", "guildId", "userId", "targetSquad", "squadVersion"))


def validate_context(job, action, player, forward, reverse, guild_id):
    """Return (status, code) on invalid work; never infer an identity."""
    if (not job or job.get("id") != job.get("actionId")
            or not isinstance(job.get("guildId"), str) or not SNOWFLAKE.fullmatch(job["guildId"])
            or not isinstance(job.get("userId"), str) or not SNOWFLAKE.fullmatch(job["userId"])
            or job["guildId"] != guild_id or job.get("targetSquad") not in SQUADS
            or type(job.get("squadVersion")) is not int or job["squadVersion"] < 0):
        return "blocked", "invalid-job"
    if (not action or action.get("id") != job["actionId"] or action.get("status") not in {"approved", "unchanged"}
            or action.get("playerId") != job.get("playerId")
            or action.get("discordGuildId") != job["guildId"] or action.get("discordUserId") != job["userId"]
            or action.get("toSquad") != job["targetSquad"] or action.get("squadVersionAfter") != job["squadVersion"]):
        return "blocked", "invalid-approved-action"
    if (not forward or not reverse
            or any(binding.get(key) != job.get(key) for binding in (forward, reverse) for key in ("playerId", "guildId", "userId"))
            or not forward.get("sourceActionId") or forward.get("sourceActionId") != reverse.get("sourceActionId")):
        return "blocked", "binding-mismatch"
    if reverse.get("latestActionId") != job["actionId"]:
        return "superseded", "job-superseded"
    if not player or player.get("deleted") or (player.get("active") is False and job["targetSquad"] != "unassigned"):
        return "blocked", "player-unavailable"
    squad = player.get("squad") if player.get("squad") in SQUADS else "unassigned"
    if squad != job["targetSquad"] or player.get("squadVersion", 0) != job["squadVersion"]:
        return "superseded", "job-superseded"
    if action["status"] == "approved" and player.get("squadActionId") != job["actionId"]:
        return "superseded", "job-superseded"
    return None


@dataclass
class RoleClaim:
    job: dict
    token: str
    lease_until: datetime


class FirestoreRoleStore:
    """Read approved work; write only execution state and member lease fields."""

    READ_COLLECTIONS = {"players", "rosterActions", "rosterDiscordPlayers", "rosterDiscordUsers",
                        "rosterRoleJobs", "rosterRoleRetries", "integrations"}

    def __init__(self, database, site_id, guild_id, firestore_module=None, *, token_factory=None):
        if site_id not in {"kmb-league", "sst-dbo"} or not SNOWFLAKE.fullmatch(str(guild_id)):
            raise ValueError("invalid-role-worker-scope")
        if firestore_module is None:
            from firebase_admin import firestore as firestore_module
        self.db, self.fs = database, firestore_module
        self.prefix, self.guild_id = "sites/" + site_id + "/", str(guild_id)
        self.token_factory = token_factory or (lambda: secrets.token_hex(16))
        self.cursors = {}

    def ref(self, collection, identifier):
        if collection not in self.READ_COLLECTIONS:
            raise ValueError("invalid-role-worker-collection")
        return self.db.document(self.prefix + collection + "/" + document_id(identifier))

    def transaction(self, callback):
        return self.fs.transactional(callback)(self.db.transaction())

    def _page(self, collection, status, limit):
        key = collection + ":" + status
        query = self.db.collection(self.prefix + collection).where("status", "==", status).limit(limit)
        if self.cursors.get(key) is not None:
            query = query.start_after(self.cursors[key])
        snapshots = list(query.stream())
        self.cursors[key] = snapshots[-1] if len(snapshots) == limit else None
        return [{**item.to_dict(), "_documentId": item.id} for item in snapshots]

    def candidates(self, limit=10):
        rows = []
        for status in ("pending", "retry", "applying"):
            rows.extend(self._page("rosterRoleJobs", status, limit))
        return [row["_documentId"] for row in rows if row.get("guildId") == self.guild_id]

    def pending_retries(self, limit=20):
        return self._page("rosterRoleRetries", "pending", limit)

    def _load(self, tx, identifier):
        job_ref = self.ref("rosterRoleJobs", identifier)
        snapshot = job_ref.get(transaction=tx)
        job = snapshot.to_dict() if snapshot.exists else None
        if not job:
            return {"job_ref": job_ref, "job": None}
        try:
            if job.get("id") != identifier:
                raise ValueError("invalid-job")
            action_ref = self.ref("rosterActions", job.get("actionId"))
            player_ref = self.ref("players", job.get("playerId"))
            forward_ref = self.ref("rosterDiscordPlayers", job.get("playerId"))
            if not SNOWFLAKE.fullmatch(str(job.get("guildId", ""))) or not SNOWFLAKE.fullmatch(str(job.get("userId", ""))):
                raise ValueError("invalid-job")
            reverse_ref = self.ref("rosterDiscordUsers", job["guildId"] + "_" + job["userId"])
        except (ValueError, TypeError):
            return {"job_ref": job_ref, "job": job, "invalid": ("blocked", "invalid-job")}
        state = {"job_ref": job_ref, "job": job, "reverse_ref": reverse_ref}
        for key, ref in (("action", action_ref), ("player", player_ref), ("forward", forward_ref), ("reverse", reverse_ref)):
            item = ref.get(transaction=tx)
            state[key] = item.to_dict() if item.exists else None
        state["invalid"] = validate_context(job, state["action"], state["player"], state["forward"], state["reverse"], self.guild_id)
        return state

    def _write_job(self, tx, state, fields):
        tx.set(state["job_ref"], {**fields, "updatedAt": self.fs.SERVER_TIMESTAMP}, merge=True)

    def _release(self, tx, state):
        # Identity, head, and creation proof remain exactly as approved by the web.
        tx.set(state["reverse_ref"], {"leaseToken": "", "leaseJobId": "", "leaseUntil": EMPTY_LEASE}, merge=True)

    @staticmethod
    def _owns(state, claim):
        job, reverse = state.get("job") or {}, state.get("reverse") or {}
        return (identity_of(job) == identity_of(claim.job) and job.get("leaseToken") == claim.token
                and reverse.get("leaseToken") == claim.token and reverse.get("leaseJobId") == claim.job["id"])

    def claim(self, identifier, now):
        document_id(identifier)

        def commit(tx):
            state = self._load(tx, identifier)
            job = state["job"]
            if not job or job.get("status") not in ACTIVE_STATUSES:
                return None
            if state.get("invalid"):
                status, code = state["invalid"]
                self._write_job(tx, state, {"status": status, "errorCode": code})
                return None
            reverse = state["reverse"]
            if reverse.get("leaseToken") and timestamp(reverse.get("leaseUntil")) > now:
                return None
            if job["status"] != "applying" and timestamp(job.get("nextAttemptAt")) > now:
                return None
            token = self.token_factory()
            if not NONCE.fullmatch(token):
                raise ValueError("invalid-lease-token")
            until = now + timedelta(seconds=LEASE_SECONDS)
            attempts = max(0, int(job.get("attempts", 0))) + 1
            fields = {"status": "applying", "attempts": attempts, "errorCode": "", "phase": "prepare",
                      "leaseToken": token, "leaseUntil": until, "lastAttemptAt": self.fs.SERVER_TIMESTAMP}
            self._write_job(tx, state, fields)
            tx.set(state["reverse_ref"], {"leaseToken": token, "leaseUntil": until, "leaseJobId": identifier}, merge=True)
            return RoleClaim({**job, **fields}, token, until)

        return self.transaction(commit)

    def guard(self, claim, now, fields=None):
        """Renew only a still-owned, still-current lease before each HTTP call."""
        allowed = {"phase", "desiredRoleId", "oppositeRoleId", "observedRoles", "observedAt"}
        fields = {key: value for key, value in (fields or {}).items() if key in allowed}

        def commit(tx):
            state = self._load(tx, claim.job["id"])
            if not self._owns(state, claim) or state["job"].get("status") != "applying":
                return None
            if state.get("invalid"):
                status, code = state["invalid"]
                self._write_job(tx, state, {"status": status, "errorCode": code})
                self._release(tx, state)
                return None
            if timestamp(state["reverse"].get("leaseUntil")) <= now:
                return None
            until = now + timedelta(seconds=LEASE_SECONDS)
            self._write_job(tx, state, {**fields, "leaseUntil": until})
            tx.set(state["reverse_ref"], {"leaseUntil": until}, merge=True)
            return until

        return self.transaction(commit)

    def finish(self, claim, now, *, status, error_code="", fields=None, uncertain=False):
        if status not in {"applied", "retry", "blocked", "superseded"}:
            raise ValueError("invalid-role-job-result")
        safe_fields = {key: value for key, value in (fields or {}).items()
                       if key in {"phase", "observedRoles", "observedAt", "desiredRoleId", "oppositeRoleId"}}

        def commit(tx):
            state = self._load(tx, claim.job["id"])
            if not self._owns(state, claim):
                return "lease-lost"
            result, code = state.get("invalid") or (status, error_code)
            output = {**safe_fields, "status": result, "errorCode": code}
            if result == "applied":
                output["appliedAt"] = self.fs.SERVER_TIMESTAMP
            if result == "retry":
                attempt = int(state["job"].get("attempts", 1))
                delay = min(300, 5 * 2 ** min(max(attempt - 1, 0), 6))
                output["nextAttemptAt"] = now + timedelta(seconds=delay)
                if uncertain:
                    output["nextAttemptAt"] = max(output["nextAttemptAt"], timestamp(state["reverse"].get("leaseUntil")) + timedelta(seconds=5))
            self._write_job(tx, state, output)
            # On an HTTP timeout the server may already have accepted a request.
            # Keep the lease through its expiry before any newer approval/retry.
            if not uncertain or result != "retry":
                self._release(tx, state)
            return result

        return self.transaction(commit)

    def consume_retry(self, request, now):
        identifier = document_id(request.get("_documentId"))
        request_ref = self.ref("rosterRoleRetries", identifier)

        def commit(tx):
            snapshot = request_ref.get(transaction=tx)
            current = snapshot.to_dict() if snapshot.exists else {}
            if current.get("status") != "pending" or current.get("nonce") != request.get("nonce"):
                return "superseded"
            state = self._load(tx, identifier)
            job, reverse = state.get("job"), state.get("reverse") or {}
            code = ""
            if not NONCE.fullmatch(str(current.get("nonce", ""))):
                code = "invalid-retry-nonce"
            elif not job:
                code = "invalid-job"
            elif state.get("invalid"):
                code = state["invalid"][1]
            elif job.get("status") not in {"blocked", "retry"}:
                code = "job-not-retryable"
            elif reverse.get("leaseToken") and timestamp(reverse.get("leaseUntil")) > now:
                code = "role-worker-busy"
            if not code:
                self._write_job(tx, state, {"status": "pending", "errorCode": "", "nextAttemptAt": now})
            tx.set(request_ref, {**current, "status": "failed" if code else "accepted", "errorCode": code,
                                 "checkedAt": self.fs.SERVER_TIMESTAMP})
            return "failed" if code else "accepted"

        return self.transaction(commit)

    def health(self, error_code="", *, disabled=False):
        self.ref("integrations", "discordRoster").set({
            "rolesStatus": "disabled" if disabled else "error" if error_code else "online",
            "rolesErrorCode": error_code, "rolesHeartbeatAt": self.fs.SERVER_TIMESTAMP,
        }, merge=True)


class RoleSyncError(Exception):
    def __init__(self, code, *, blocked=True):
        super().__init__(code)
        self.code, self.blocked = code, blocked


class LeaseLost(Exception):
    pass


def failure(error):
    """Stable, safe diagnostics only; raw HTTP text can contain private details."""
    if isinstance(error, RoleSyncError):
        return ("blocked" if error.blocked else "retry"), error.code, False
    if isinstance(error, LeaseLost):
        return "retry", "lease-lost", False
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "retry", "timeout", True
    status, code = getattr(error, "status", None), getattr(error, "code", None)
    if status == 403:
        return "blocked", "discord-permission-denied", False
    if status == 404 and code == 10007:
        return "blocked", "member-not-found", False
    if status == 404 and code in {10004, 10003}:
        return "blocked", "guild-unavailable", False
    if status == 404 and code == 10011:
        return "blocked", "role-definition-changed", False
    if status == 429:
        return "retry", "discord-rate-limited", False
    # A transport reset or HTTP 5xx can follow a mutation Discord already
    # accepted. Preserve the lease just as for a timeout, then reread on retry.
    return "retry", "connection-error", True


def select_roles(roles, bot_member, guild_id):
    chosen = {}
    for target, identifier in ROLE_IDS.items():
        matches = [role for role in roles if getattr(role, "id", None) == identifier]
        if len(matches) != 1:
            # Keep the v5.4.1 site's actionable role-configuration error code.
            # Never adopt a same-name replacement after a configured ID vanishes.
            raise RoleSyncError("role-definition-changed")
        role = matches[0]
        if getattr(role, "managed", False) or str(role.id) == guild_id or (hasattr(role, "is_default") and role.is_default()):
            raise RoleSyncError("role-not-manageable")
        chosen[target] = role
    # Resolve the bot's role IDs against fresh role definitions, not cached role
    # permissions/positions. Missing cache entries can only make this fail closed.
    bot_ids = {int(role.id) for role in bot_member.roles}
    bot_roles = [role for role in roles if int(role.id) in bot_ids or str(role.id) == guild_id]
    if not any(getattr(getattr(role, "permissions", None), "manage_roles", False)
               or getattr(getattr(role, "permissions", None), "administrator", False) for role in bot_roles):
        raise RoleSyncError("manage-roles-required")
    highest = max((getattr(role, "position", 0) for role in bot_roles), default=0)
    if any(highest <= getattr(role, "position", 0) for role in chosen.values()):
        raise RoleSyncError("role-hierarchy")
    return chosen


class RecordsRoleWorker:
    def __init__(self, bot, store: FirestoreRoleStore, *, clock=None, blocking=None, log=None,
                 poll_seconds=15, http_timeout=HTTP_TIMEOUT_SECONDS):
        if not 0 < http_timeout <= HTTP_TIMEOUT_SECONDS:
            raise ValueError("role-http-timeout-must-be-at-most-20-seconds")
        self.bot, self.store = bot, store
        self.clock = clock or utc_now
        self.blocking = blocking or asyncio.to_thread
        self.log = log or print
        self.poll_seconds, self.http_timeout = poll_seconds, http_timeout
        self.task = None
        self._closed = False
        self.last_error = ""
        self.last_health = EMPTY_LEASE

    async def start(self):
        if not self._closed and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run(), name="records-roster-roles")

    async def close(self):
        """Stop this worker permanently; a new extension owns a new worker.

        In-flight requests may already have reached Discord. Cancellation flows
        through process() without releasing its bounded persisted member lease.
        """
        self._closed = True
        task = self.task
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
        finally:
            if self.task is task:
                self.task = None

    async def _heartbeat(self, *, force=False):
        if force or (self.clock() - self.last_health).total_seconds() >= 30:
            await self.blocking(self.store.health, self.last_error)
            self.last_health = self.clock()

    async def _call(self, claim, phase, callback, fields=None):
        await self._heartbeat()
        now = self.clock()
        until = await self.blocking(self.store.guard, claim, now, {**(fields or {}), "phase": phase})
        if until is None or (until - self.clock()).total_seconds() < MIN_LEASE_SECONDS:
            raise LeaseLost()
        claim.lease_until = until
        return await asyncio.wait_for(callback(), timeout=self.http_timeout)

    async def _context(self, claim, guild, expected_roles=None):
        roles = await self._call(claim, "prepare", guild.fetch_roles)
        bot_id = getattr(getattr(self.bot, "user", None), "id", None)
        if bot_id is None:
            raise RoleSyncError("guild-unavailable")
        own_member = await self._call(claim, "prepare", lambda: guild.fetch_member(int(bot_id)))
        chosen = select_roles(roles, own_member, self.store.guild_id)
        if expected_roles and any(chosen[key].id != expected_roles[key].id for key in ROLE_NAMES):
            raise RoleSyncError("role-definition-changed")
        member = await self._call(claim, "verify", lambda: guild.fetch_member(int(claim.job["userId"])))
        if str(member.id) != claim.job["userId"] or str(getattr(getattr(member, "guild", None), "id", "")) != self.store.guild_id:
            raise RoleSyncError("binding-mismatch")
        if getattr(member, "bot", False) or int(member.id) == int(bot_id):
            raise RoleSyncError("member-is-bot")
        ids = {int(role.id) for role in member.roles}
        observed = [str(chosen[key].id) for key in ROLE_NAMES if int(chosen[key].id) in ids]
        return chosen, member, ids, {"observedRoles": observed, "observedAt": self.clock()}

    async def process(self, identifier):
        if self._closed:
            return None
        claim = await self.blocking(self.store.claim, identifier, self.clock())
        if claim is None:
            return None
        evidence = {}
        try:
            guild = self.bot.get_guild(int(self.store.guild_id))
            if guild is None:
                guild = await self._call(claim, "prepare", lambda: self.bot.fetch_guild(int(self.store.guild_id)))
            if str(getattr(guild, "id", "")) != self.store.guild_id:
                raise RoleSyncError("guild-unavailable")
            chosen, member, ids, evidence = await self._context(claim, guild)
            target = claim.job["targetSquad"]
            reason = "기록실 등말소 승인 " + claim.job["actionId"]
            if target != "unassigned":
                desired, opposite = chosen[target], chosen["second" if target == "first" else "first"]
                role_fields = {"desiredRoleId": str(desired.id), "oppositeRoleId": str(opposite.id)}
                evidence.update(role_fields)
                if int(desired.id) not in ids:
                    await self._call(claim, "add", lambda: member.add_roles(desired, reason=reason, atomic=True), evidence)
                # Confirm the desired role exists before removing its opposite.
                _, member, ids, observation = await self._context(claim, guild, chosen)
                evidence.update(observation)
                if int(desired.id) not in ids:
                    raise RoleSyncError("role-verification-failed", blocked=False)
                if int(opposite.id) in ids:
                    await self._call(claim, "remove", lambda: member.remove_roles(opposite, reason=reason, atomic=True), evidence)
            else:
                # Only an approved correction/manual unassigned action reaches
                # here. Remove the two squad roles individually, preserving others.
                evidence.update({"desiredRoleId": "", "oppositeRoleId": ""})
                for target_name in ROLE_NAMES:
                    _, member, ids, observation = await self._context(claim, guild, chosen)
                    evidence.update(observation)
                    role = chosen[target_name]
                    if int(role.id) in ids:
                        await self._call(claim, "remove", lambda role=role: member.remove_roles(role, reason=reason, atomic=True), evidence)
            _, _, ids, observation = await self._context(claim, guild, chosen)
            evidence.update(observation)
            expected = set() if target == "unassigned" else {int(chosen[target].id)}
            actual = ids & {int(role.id) for role in chosen.values()}
            if actual != expected:
                raise RoleSyncError("role-verification-failed", blocked=False)
            return await self.blocking(lambda: self.store.finish(claim, self.clock(), status="applied", fields={**evidence, "phase": "verified"}))
        except asyncio.CancelledError:
            # Do not release an in-flight lease on shutdown: a request may already
            # have reached Discord. The next process waits for its bounded expiry.
            raise
        except Exception as error:
            status, code, uncertain = failure(error)
            self.last_error = code
            self.log("기록실 군 역할 변경 보류: " + code)
            return await self.blocking(lambda: self.store.finish(claim, self.clock(), status=status, error_code=code,
                                                                fields=evidence, uncertain=uncertain))

    async def poll_once(self):
        if self._closed:
            return
        self.last_error = ""
        await self._heartbeat(force=True)
        for request in await self.blocking(self.store.pending_retries):
            await self.blocking(self.store.consume_retry, request, self.clock())
        for identifier in await self.blocking(self.store.candidates):
            await self.process(identifier)
        await self._heartbeat(force=True)

    async def run(self):
        while not self._closed and not self.bot.is_closed():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = failure(error)[1]
                self.log("기록실 군 역할 작업 재시도 대기: " + code)
                try:
                    await self.blocking(self.store.health, code)
                except Exception:
                    pass
            await asyncio.sleep(self.poll_seconds)


# ═══════════════ 채널 원문·멘션 수집 / 웹 원문 재확인 ═══════════════
CHANNEL_ID = "1551385834995978301"
SITES = {"sst-dbo", "kmb-league"}
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,29}$")


def text(value: Any, limit: int = 12000) -> str:
    # The browser contract uses JavaScript string.length (UTF-16 code units).
    raw = str(value or "").strip().encode("utf-16-le", errors="surrogatepass")
    return raw[:limit * 2].decode("utf-16-le", errors="ignore")


def text_units(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def snowflake(value: Any) -> str:
    value = text(value, 24)
    if not SNOWFLAKE.fullmatch(value):
        raise ValueError("Discord ID는 17~20자리 숫자여야 합니다.")
    return value


def utc_iso(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Discord 시각은 시간대가 포함되어야 합니다.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def message_id_at(value: datetime) -> str:
    """A high snowflake at this UTC instant; used only for first-run cutoff."""
    milliseconds = int(value.timestamp() * 1000) - 1420070400000
    return str((milliseconds << 22) | ((1 << 22) - 1))


def source_id(channel_id: Any, message_id: Any) -> str:
    return snowflake(channel_id) + "_" + snowflake(message_id)


@dataclass(frozen=True)
class BridgeConfig:
    project_id: str
    site_id: str = "kmb-league"
    channel_id: str = CHANNEL_ID
    guild_id: str = ""
    poll_seconds: int = 15
    backfill_limit: int = 0
    role_sync_enabled: bool = True

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        enabled = text(env.get("RECORDS_BRIDGE_ENABLED", "false")).lower()
        if enabled in {"", "0", "false", "no", "off"}:
            return None
        if enabled not in {"1", "true", "yes", "on"}:
            raise ValueError("RECORDS_BRIDGE_ENABLED는 true 또는 false로 설정하세요.")
        project = text(env.get("RECORDS_FIREBASE_PROJECT_ID"))
        if not PROJECT_ID.fullmatch(project):
            raise ValueError("RECORDS_FIREBASE_PROJECT_ID에 기록실 프로젝트 ID를 명시하세요.")
        site = text(env.get("RECORDS_SITE_ID", "kmb-league"))
        if site not in SITES:
            raise ValueError("RECORDS_SITE_ID는 kmb-league 또는 sst-dbo만 가능합니다.")
        channel = snowflake(env.get("RECORDS_DISCORD_CHANNEL_ID", CHANNEL_ID))
        if channel != CHANNEL_ID:
            raise ValueError("현재 기록실은 등말소 채널 1551385834995978301만 지원합니다.")
        guild = text(env.get("RECORDS_DISCORD_GUILD_ID"))
        if guild:
            guild = snowflake(guild)
        try:
            backfill = int(env.get("RECORDS_BACKFILL_LIMIT", "0"))
        except (TypeError, ValueError):
            raise ValueError("RECORDS_BACKFILL_LIMIT은 0~500의 정수여야 합니다.") from None
        if not 0 <= backfill <= 500:
            raise ValueError("RECORDS_BACKFILL_LIMIT은 0~500의 정수여야 합니다.")
        roles_enabled = text(env.get("RECORDS_ROLE_SYNC_ENABLED", "true")).lower()
        if roles_enabled not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
            raise ValueError("RECORDS_ROLE_SYNC_ENABLED는 true 또는 false로 설정하세요.")
        return cls(project, site, channel, guild, 15, backfill, roles_enabled in {"1", "true", "yes", "on"})


def records_database(config: BridgeConfig, existing_db, environment=None):
    """Reuse a DB only after an explicit, matching target project selection."""
    env = os.environ if environment is None else environment
    actual_project = text(getattr(existing_db, "project", ""))
    if actual_project and actual_project == config.project_id:
        return existing_db
    import firebase_admin
    from firebase_admin import credentials, firestore

    email = text(env.get("RECORDS_FIREBASE_CLIENT_EMAIL"))
    private_key = str(env.get("RECORDS_FIREBASE_PRIVATE_KEY", "")).replace("\\n", "\n")
    if not email or not private_key:
        raise ValueError("기록실이 별도 Firebase 프로젝트입니다. RECORDS_FIREBASE_CLIENT_EMAIL과 RECORDS_FIREBASE_PRIVATE_KEY를 설정하세요.")
    name = "records-roster-" + config.site_id + "-" + config.project_id
    try:
        app = firebase_admin.get_app(name)
    except ValueError:
        credential = credentials.Certificate({
            "type": "service_account", "project_id": config.project_id,
            "client_email": email, "private_key": private_key,
            "token_uri": "https://oauth2.googleapis.com/token",
        })
        app = firebase_admin.initialize_app(credential, {"projectId": config.project_id}, name=name)
    if app.project_id != config.project_id:
        raise ValueError("기록실 연결의 프로젝트 ID가 일치하지 않습니다.")
    return firestore.client(app=app)


def is_unknown_message(error: Exception) -> bool:
    return getattr(error, "status", None) == 404 and getattr(error, "code", None) == 10008


def discord_text(message) -> tuple[str, bool, bool]:
    """Flatten visible text and embeds, retaining labels; never fetch attachments."""
    blocks = []
    content = str(getattr(message, "content", "") or "").strip()
    if content:
        blocks.append(content)
    all_embeds = list(getattr(message, "embeds", []) or [])
    limited = len(all_embeds) > 10
    for embed in all_embeds[:10]:
        data = embed.to_dict() if hasattr(embed, "to_dict") else embed
        if not isinstance(data, dict):
            continue
        for field in ("title", "description"):
            if data.get(field):
                blocks.append(str(data[field]))
        fields = data.get("fields", [])
        limited = limited or len(fields) > 25
        for field in fields[:25]:
            if isinstance(field, dict):
                blocks.append(str(field.get("name", "")) + ": " + str(field.get("value", "")))
        # Footer text can identify a request, but it is never an approval signal.
        footer = data.get("footer")
        if isinstance(footer, dict) and footer.get("text"):
            blocks.append(str(footer["text"]))
    raw = "\n".join(blocks).replace("\r\n", "\n").replace("\r", "\n")
    return text(raw, 12000), limited or text_units(raw) > 12000, bool(getattr(message, "attachments", None))


def safe_error_code(error: Exception) -> str:
    """Only stable diagnostic categories, never token/header/credential strings."""
    status = getattr(error, "status", None)
    if status == 403:
        return "discord-permission-denied"
    if status == 429:
        return "discord-rate-limited"
    if is_unknown_message(error):
        return "source-deleted"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    return "connection-error"


ACTION = re.compile(r"^(1\s*군\s*등록|2\s*군\s*말소|1\s*군\s*말소|2\s*군\s*등록)\s*(?:[:：=]|[-–—]>?)?\s*(.*)$")
TEAM = re.compile(r"^(?:대상\s*팀|팀|소속\s*팀|소속|구단|구단명)\s*[:：=]\s*(.+)$")
PLAYER = re.compile(r"^(?:선수|선수명|닉네임|대상\s*선수)\s*[:：=]\s*(.*)$")
EMPTY_NAMES = {"", "-", "—", "없음", "해당없음", "해당 없음", "없습니다"}
USER_MENTION = re.compile(r"<@!?([0-9]{17,20})>")
NON_USER_PING = re.compile(r"<@&[0-9]+>|@everyone\b|@here\b", re.IGNORECASE)
MENTION_METADATA = re.compile(r"^(?:요청자|작성자|승인자|검토자|담당자|사유|메모|날짜|일시)\s*[:：=]")


def plain_line(line: str) -> str:
    # Preserve the raw source separately; this only recognizes explicit labels.
    return line.replace("**", "").replace("`", "").strip().lstrip("#>•- ").strip("[]【】 ")


SECTION = re.compile(r"^(등록\s*\(\s*Call-up\s*\)|말소\s*\(\s*Send-down\s*\))\s*:?(.*)$", re.IGNORECASE)
NOTICE_STOP = re.compile(r"^(?:■\s*)?(?:사유\s*및\s*안내사항|안내사항|사유|비고)\s*[:：]", re.IGNORECASE)
BULLET = re.compile(r"^(?:[-•*]\s*)?(.*?)\s*$")
PLAYER_LINE = re.compile(r"^(?P<name>[^()（）:：]+?)\s*[（(](?P<meta>[^()（）]*)[）)]\s*[:：]\s*(?P<mention>.*)$")


def parse_entries(raw_text: str) -> tuple[list[dict], bool]:
    """Parse a notice section into *hints* and user mentions, never authorize changes.

    Registration (Call-up) => first; Send-down => second. The nickname is the
    label before the position/number parentheses, never the entire row. Discord
    roles are only modified after a matching web admin approval.
    """
    lines = [re.sub(r"^[\s📌■⚾]+", "", plain_line(line)).strip() for line in raw_text.splitlines() if line.strip()]
    explicit_teams = {m.group(1).strip() for line in lines if (m := TEAM.fullmatch(line))}
    team = next(iter(explicit_teams)) if len(explicit_teams) == 1 else ""
    entries, target, overflow = [], None, len(explicit_teams) > 1

    def append(nickname, to_squad, source, user_id=""):
        nonlocal overflow
        if any(text_units(x) > bound for x,bound in ((nickname,120),(team,100),(source,1200))):
            overflow = True
        if len(entries) >= 100:
            overflow = True
            return
        entries.append({"id":str(len(entries)),"nickname":text(nickname,120),
                        "teamHint":text(team,100),"toSquad":to_squad,
                        "text":text(source,1200),"discordUserId":user_id})

    def parse_names(value, to_squad, original):
        value = value.strip()
        if value in EMPTY_NAMES or value in {"- 없음","• 없음"}:
            return
        # One player/line in the announcement format. Split neither position
        # and jersey number on '/' nor nickname from its actual mention.
        matched = PLAYER_LINE.fullmatch(value)
        if matched:
            nickname = matched.group('name').strip()
            raw_mention = matched.group('mention').strip()
            ids = USER_MENTION.findall(raw_mention)
            if len(ids) == 1 and not NON_USER_PING.search(raw_mention):
                append(nickname,to_squad,original,ids[0]);return
            # No identity is inferred from @display-text, a role ping or two IDs.
            append(nickname,'unknown' if len(ids)>1 or NON_USER_PING.search(raw_mention) else to_squad,original)
            return
        ids=USER_MENTION.findall(value)
        if ids and not NON_USER_PING.search(value):
            # A bare mention list has no unambiguous nickname; website matches ID.
            for uid in dict.fromkeys(ids):append('',to_squad,original,uid)
            return
        # Legacy '1군 등록: 이름' or '선수: 이름' must still import as hint only.
        m=PLAYER.fullmatch(value)
        nickname=(m.group(1) if m else value).strip()
        nickname=re.sub(r"\s*[（(][^()（）]*[）)]\s*$",'',nickname).strip()
        if nickname and nickname not in EMPTY_NAMES and not NON_USER_PING.search(nickname):
            append(nickname,to_squad,original)

    for line in lines:
        if TEAM.fullmatch(line):continue
        if NOTICE_STOP.match(line):
            target=None
            break  # Explanation below is not a second list of players.
        section=SECTION.fullmatch(line)
        if section:
            target='first' if section.group(1).lower().startswith('등록') else 'second'
            if section.group(2).strip():parse_names(section.group(2),target,line)
            continue
        action=ACTION.fullmatch(line)
        if action:
            label=action.group(1).replace(' ','')
            target='first' if label in {'1군등록','2군말소'} else 'second'
            if action.group(2).strip():parse_names(action.group(2),target,line)
            continue
        if target and line.startswith(('📌','■')):
            target=None;continue
        if target and not line.startswith(('----','════')):
            m=BULLET.fullmatch(line)
            candidate=m.group(1).strip() if m else line
            if candidate:
                parse_names(candidate,target,line)
    if not entries:
        entries.append({"id":"0","nickname":"","teamHint":text(team,100),
                        "toSquad":"unknown","text":text(raw_text,1200),"discordUserId":""})
    return entries,overflow


def message_snapshot(message, config: BridgeConfig, expected_guild: str) -> dict:
    channel = snowflake(getattr(getattr(message, "channel", None), "id", ""))
    guild = snowflake(getattr(getattr(message, "guild", None), "id", ""))
    if channel != config.channel_id or guild != expected_guild:
        raise ValueError("지정한 등말소 채널/서버의 메시지만 수집할 수 있습니다.")
    identifier = snowflake(message.id)
    author = getattr(message, "author", None)
    author_id = snowflake(getattr(author, "id", ""))
    author_name = str(getattr(author, "display_name", "") or str(author))
    raw, truncated, attachments = discord_text(message)
    entries, overflow = parse_entries(raw)
    evidence = {
        "content": str(getattr(message, "content", "") or ""),
        "embeds": [embed.to_dict() if hasattr(embed, "to_dict") else embed for embed in (getattr(message, "embeds", []) or [])],
        "attachments": [{"id": str(getattr(item, "id", "")), "name": str(getattr(item, "filename", "")),
                         "size": getattr(item, "size", None)} for item in (getattr(message, "attachments", []) or [])],
    }
    digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {
        "id": source_id(channel, identifier), "schemaVersion": 1,
        "channelId": channel, "guildId": guild, "messageId": identifier,
        "authorId": author_id, "authorName": text(author_name, 120),
        "rawText": raw, "contentHash": digest, "entries": entries, "deleted": False,
        "textTruncated": truncated or overflow or text_units(author_name) > 120, "hasAttachments": attachments,
        "sourceCreatedAt": utc_iso(getattr(message, "created_at", None)),
        "sourceEditedAt": utc_iso(getattr(message, "edited_at", None)),
    }


def next_source(previous: dict | None, incoming: dict, timestamp) -> tuple[str, dict | None]:
    """Pure transition used inside a Firestore transaction."""
    if previous:
        if previous.get("deleted") and not incoming.get("deleted"):
            return "stale", None  # Deleted Discord IDs cannot be restored.
        old_time = previous.get("sourceEditedAt") or previous.get("sourceCreatedAt", "")
        new_time = incoming.get("sourceEditedAt") or incoming.get("sourceCreatedAt", "")
        if not incoming.get("deleted") and old_time and new_time and new_time < old_time:
            return "stale", None
        revision = int(previous.get("revision", 0))
        if (previous.get("contentHash") == incoming.get("contentHash")
                and bool(previous.get("deleted")) == bool(incoming.get("deleted"))
                and previous.get("entries") == incoming.get("entries")):
            # Even a same-content edit/revert advances Discord's edit watermark.
            # Otherwise an older history snapshot with different text can overwrite it.
            value = {**previous, "sourceEditedAt": incoming.get("sourceEditedAt", ""), "verifiedAt": timestamp}
            if value["sourceEditedAt"] != previous.get("sourceEditedAt", ""):
                value["updatedAt"] = timestamp
            return "unchanged", value
        return "changed", {**incoming, "revision": revision + 1,
                           "createdAt": previous.get("createdAt", timestamp),
                           "updatedAt": timestamp, "verifiedAt": timestamp}
    return "created", {**incoming, "revision": 1, "createdAt": timestamp,
                       "updatedAt": timestamp, "verifiedAt": timestamp}


class FirestoreInboxStore:
    """Writes only inbox evidence, check responses and integration health/cursor."""

    def __init__(self, database, config: BridgeConfig, firestore_module=None):
        if firestore_module is None:
            from firebase_admin import firestore as firestore_module
        self.db = database
        self.fs = firestore_module
        self.config = config
        self.prefix = "sites/" + config.site_id + "/"

    def ref(self, collection: str, identifier: str):
        if collection not in {"rosterInbox", "rosterSourceChecks", "integrations"}:
            raise ValueError("등말소 수집기가 수정할 수 없는 컬렉션입니다.")
        return self.db.document(self.prefix + collection + "/" + identifier)

    def transaction(self, callback):
        return self.fs.transactional(callback)(self.db.transaction())

    def verify_target(self):
        snapshot = self.db.document(self.prefix + "meta/site").get()
        if not snapshot.exists or snapshot.to_dict().get("siteId") != self.config.site_id:
            raise ValueError("지정 프로젝트에 일치하는 기록실이 없습니다. RECORDS_FIREBASE_PROJECT_ID와 RECORDS_SITE_ID를 확인하세요.")

    def state(self) -> dict:
        snapshot = self.ref("integrations", "discordRoster").get()
        value = snapshot.to_dict() if snapshot.exists else {}
        if value.get("channelId") and value["channelId"] != self.config.channel_id:
            raise ValueError("기존 등말소 연동의 채널이 다릅니다. 채널별 기록을 확인한 뒤 별도 설정으로 연결하세요.")
        return value

    def initialize_cursor(self, cursor: str, guild_id: str):
        ref = self.ref("integrations", "discordRoster")

        def commit(tx):
            snapshot = ref.get(transaction=tx)
            old = snapshot.to_dict() if snapshot.exists else {}
            if old.get("channelId") and old["channelId"] != self.config.channel_id:
                raise ValueError("기존 등말소 채널 설정과 일치하지 않습니다.")
            if old.get("cursor"):
                return old["cursor"]
            tx.set(ref, {**old, "schemaVersion": 1, "channelId": self.config.channel_id,
                         "guildId": guild_id, "siteId": self.config.site_id,
                         "cursor": snowflake(cursor), "startedAt": self.fs.SERVER_TIMESTAMP,
                         "updatedAt": self.fs.SERVER_TIMESTAMP}, merge=True)
            return cursor

        return self.transaction(commit)

    def advance_cursor(self, cursor: str):
        ref = self.ref("integrations", "discordRoster")
        cursor = snowflake(cursor)

        def commit(tx):
            snapshot = ref.get(transaction=tx)
            old = snapshot.to_dict() if snapshot.exists else {}
            if old.get("cursor") and int(old["cursor"]) >= int(cursor):
                return old["cursor"]
            tx.set(ref, {"cursor": cursor, "updatedAt": self.fs.SERVER_TIMESTAMP}, merge=True)
            return cursor

        return self.transaction(commit)

    def health(self, guild_id: str, error_code: str = ""):
        self.ref("integrations", "discordRoster").set({
            "schemaVersion": 1, "channelId": self.config.channel_id, "guildId": guild_id,
            "siteId": self.config.site_id, "status": "error" if error_code else "online",
            "errorCode": error_code, "heartbeatAt": self.fs.SERVER_TIMESTAMP,
            "updatedAt": self.fs.SERVER_TIMESTAMP,
        }, merge=True)

    def pending_checks(self, limit=25) -> list[dict]:
        query = self.db.collection(self.prefix + "rosterSourceChecks").where("status", "==", "pending").limit(limit)
        checks = []
        for snapshot in query.stream():
            value = snapshot.to_dict()
            if snapshot.id.startswith(self.config.channel_id + "_"):
                checks.append({**value, "id": snapshot.id})
        return checks

    def recent_sources(self, limit=100) -> list[dict]:
        query = self.db.collection(self.prefix + "rosterInbox").order_by("updatedAt", direction="DESCENDING").limit(limit)
        return [value for item in query.stream() if (value := item.to_dict()).get("channelId") == self.config.channel_id and not value.get("deleted")]

    def observe(self, incoming: dict, check: dict | None = None) -> str:
        identifier = source_id(incoming["channelId"], incoming["messageId"])
        if identifier != incoming["id"] or incoming["channelId"] != self.config.channel_id:
            raise ValueError("수집 원문의 채널 또는 ID가 다릅니다.")
        source_ref = self.ref("rosterInbox", identifier)
        check_ref = self.ref("rosterSourceChecks", identifier) if check else None

        def commit(tx):
            snapshot = source_ref.get(transaction=tx)
            previous = snapshot.to_dict() if snapshot.exists else None
            current_check = check_ref.get(transaction=tx) if check_ref else None
            status, updated = next_source(previous, incoming, self.fs.SERVER_TIMESTAMP)
            if updated is not None:
                tx.set(source_ref, updated)
            if current_check and current_check.exists:
                current = current_check.to_dict()
                if current.get("status") == "pending" and current.get("nonce") == check.get("nonce"):
                    fields = {**current, "checkedAt": self.fs.SERVER_TIMESTAMP}
                    if status == "stale":
                        fields.update({"status": "failed", "errorCode": "source-version-conflict"})
                    else:
                        fields.update({"status": "verified", "errorCode": "", "sourceRevision": updated["revision"],
                                       "contentHash": updated["contentHash"], "verifiedAt": self.fs.SERVER_TIMESTAMP})
                    tx.set(check_ref, fields)
            return status

        return self.transaction(commit)

    def mark_deleted(self, identifier: str, check: dict | None = None):
        source_ref = self.ref("rosterInbox", identifier)
        check_ref = self.ref("rosterSourceChecks", identifier) if check else None

        def commit(tx):
            snapshot = source_ref.get(transaction=tx)
            current_check = check_ref.get(transaction=tx) if check_ref else None
            status = "ignored"
            if snapshot.exists:
                previous = snapshot.to_dict()
                incoming = {**previous, "deleted": True, "contentHash": hashlib.sha256(("deleted:" + identifier).encode()).hexdigest()}
                status, updated = next_source(previous, incoming, self.fs.SERVER_TIMESTAMP)
                if updated is not None:
                    tx.set(source_ref, updated)
            if current_check and current_check.exists:
                current = current_check.to_dict()
                if current.get("status") == "pending" and current.get("nonce") == check.get("nonce"):
                    tx.set(check_ref, {**current, "status": "failed", "errorCode": "source-deleted", "checkedAt": self.fs.SERVER_TIMESTAMP})
            return status

        return self.transaction(commit)

    def fail_check(self, check: dict, error_code: str):
        ref = self.ref("rosterSourceChecks", check["id"])

        def commit(tx):
            snapshot = ref.get(transaction=tx)
            if not snapshot.exists:
                return "ignored"
            current = snapshot.to_dict()
            if current.get("status") != "pending" or current.get("nonce") != check.get("nonce"):
                return "superseded"
            tx.set(ref, {**current, "status": "failed", "errorCode": error_code, "checkedAt": self.fs.SERVER_TIMESTAMP})
            return "failed"

        return self.transaction(commit)


class RecordsRosterBridge:
    def __init__(self, bot, store: FirestoreInboxStore, config: BridgeConfig, *, clock=None, blocking=None, log=None):
        self.bot = bot
        self.store = store
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._blocking = blocking or asyncio.to_thread
        self._blocking_tasks = set()
        self.blocking = self._call_blocking
        self.log = log or print
        self.channel = None
        self.guild_id = ""
        self.task = None
        self.cursor = ""
        self.cutoff = message_id_at(self.clock())
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.refresh_offset = 0
        self.last_refresh = None
        self.started = False
        self.poll_error = ""
        self.role_worker = None
        self.closed = False
        self._close_task = None
        self._listeners = {}
        self._event_tasks = set()

    async def _call_blocking(self, callback, *args, **kwargs):
        # Cancelling to_thread's waiter does not stop its thread. Keep the real
        # operation alive and tracked so unload can drain it before returning.
        if self.closed:
            raise asyncio.CancelledError()
        task = asyncio.create_task(self._blocking(callback, *args, **kwargs))
        self._blocking_tasks.add(task)
        task.add_done_callback(self._blocking_done)
        result = await asyncio.shield(task)
        if self.closed:
            raise asyncio.CancelledError()
        return result

    def _blocking_done(self, task):
        self._blocking_tasks.discard(task)
        if not task.cancelled():
            # A cancelled listener may no longer await this executor result.
            task.exception()

    def _listener(self, name):
        callback = getattr(self, name)

        async def dispatch(*args):
            if self.closed:
                return
            task = asyncio.current_task()
            self._event_tasks.add(task)
            try:
                await callback(*args)
            finally:
                self._event_tasks.discard(task)

        dispatch.__name__ = name
        return dispatch

    async def close(self):
        """Permanently stop this bridge; a reload creates a fresh instance."""
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(self._close(), name="records-roster-close")
        await asyncio.shield(self._close_task)

    async def _close(self):
        for name, callback in tuple(self._listeners.items()):
            self.bot.remove_listener(callback, name)
        self._listeners.clear()
        self.started = False
        tasks = {task for task in (self.task, *self._event_tasks)
                 if task is not None and task is not asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if self.role_worker is not None:
            await self.role_worker.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        self._event_tasks.clear()
        # Do not cancel operations already running in executor threads. Await
        # their completion; none can schedule another call after closed=True.
        if self._blocking_tasks:
            await asyncio.gather(*tuple(self._blocking_tasks), return_exceptions=True)

    async def start_roles(self):
        if self.closed:
            return
        if not self.config.role_sync_enabled:
            await self.blocking(lambda: FirestoreRoleStore(self.store.db, self.config.site_id, self.guild_id, self.store.fs).health(disabled=True))
            return
        if self.role_worker is None:
            role_store = FirestoreRoleStore(self.store.db, self.config.site_id, self.guild_id, self.store.fs)
            self.role_worker = RecordsRoleWorker(self.bot, role_store, clock=self.clock, blocking=self.blocking, log=self.log)
        await self.role_worker.start()

    def lock(self, message_id):
        return self.locks[int(snowflake(message_id)) % len(self.locks)]

    def accepts(self, channel_id, guild_id=None):
        return not self.closed and str(channel_id) == self.config.channel_id and (guild_id is None or str(guild_id) == self.guild_id)

    async def start(self):
        if self.closed:
            return
        if self.task is not None and not self.task.done():
            return
        if not getattr(getattr(self.bot, "intents", None), "message_content", False):
            raise ValueError("MESSAGE CONTENT INTENT를 코드와 Developer Portal에서 켜야 합니다.")
        channel = self.bot.get_channel(int(self.config.channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(self.config.channel_id))
        guild = getattr(channel, "guild", None)
        guild_id = snowflake(getattr(guild, "id", ""))
        if self.config.guild_id and self.config.guild_id != guild_id:
            raise ValueError("지정한 등말소 채널과 서버가 다릅니다.")
        if not hasattr(channel, "fetch_message") or not hasattr(channel, "history"):
            raise ValueError("등말소 채널은 메시지를 읽을 수 있는 서버 채널이어야 합니다.")
        member = getattr(guild, "me", None)
        if hasattr(channel, "permissions_for") and member is not None:
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.read_message_history:
                raise ValueError("등말소 채널 보기와 메시지 기록 보기 권한이 필요합니다.")
        await self.blocking(self.store.verify_target)
        state = await self.blocking(self.store.state)
        self.channel, self.guild_id = channel, guild_id
        if state.get("cursor"):
            self.cursor = snowflake(state["cursor"])
        elif self.config.backfill_limit:
            messages = [message async for message in channel.history(limit=self.config.backfill_limit, oldest_first=False)]
            # Persist the lower bound first, then advance one message at a time.
            # Even if startup stops mid-backfill, the next run resumes the gap.
            lower_bound = str(min((int(message.id) for message in messages), default=int(self.cutoff) + 1) - 1)
            self.cursor = await self.blocking(self.store.initialize_cursor, lower_bound, guild_id)
            for message in reversed(messages):
                await self.ingest(message, allow_before_cutoff=True)
                self.cursor = await self.blocking(self.store.advance_cursor, str(message.id))
        else:
            self.cursor = await self.blocking(self.store.initialize_cursor, self.cutoff, guild_id)
        await self.blocking(self.store.health, guild_id)
        if not self.started:
            for name in ("on_message", "on_raw_message_edit", "on_raw_message_delete", "on_raw_bulk_message_delete"):
                callback = self._listener(name)
                self.bot.add_listener(callback, name)
                self._listeners[name] = callback
            self.started = True
        self.task = asyncio.create_task(self.run(), name="records-roster-inbox")
        self.log("기록실 등말소 수집 연결 완료: " + self.config.site_id + " / 채널 " + self.config.channel_id)

    async def ingest(self, message, *, allow_before_cutoff=False, check=None):
        if not self.accepts(getattr(getattr(message, "channel", None), "id", ""), getattr(getattr(message, "guild", None), "id", "")):
            return "ignored"
        if not allow_before_cutoff and int(message.id) <= int(self.cutoff):
            return "ignored"
        incoming = message_snapshot(message, self.config, self.guild_id)
        return await self.blocking(self.store.observe, incoming, check)

    async def fetch_and_observe(self, message_id, check=None):
        message_id = snowflake(message_id)
        async with self.lock(message_id):
            try:
                message = await self.channel.fetch_message(int(message_id))
            except Exception as error:
                if is_unknown_message(error):
                    return await self.blocking(self.store.mark_deleted, source_id(self.config.channel_id, message_id), check)
                if check:
                    await self.blocking(self.store.fail_check, check, safe_error_code(error))
                raise
            return await self.ingest(message, allow_before_cutoff=True, check=check)

    async def on_message(self, message):
        if not self.accepts(getattr(getattr(message, "channel", None), "id", ""), getattr(getattr(message, "guild", None), "id", "")):
            return
        try:
            async with self.lock(message.id):
                await self.ingest(message)
        except Exception as error:
            self.log("등말소 원문 수집 보류: " + safe_error_code(error))

    async def on_raw_message_edit(self, payload):
        if not self.accepts(getattr(payload, "channel_id", ""), getattr(payload, "guild_id", None)):
            return
        try:
            # Raw update data can be partial; always fetch the canonical message.
            await self.fetch_and_observe(payload.message_id)
        except Exception as error:
            self.log("등말소 원문 수정 확인 보류: " + safe_error_code(error))

    async def on_raw_message_delete(self, payload):
        if not self.accepts(getattr(payload, "channel_id", ""), getattr(payload, "guild_id", None)):
            return
        try:
            async with self.lock(payload.message_id):
                await self.blocking(self.store.mark_deleted, source_id(self.config.channel_id, payload.message_id))
        except Exception as error:
            self.log("등말소 원문 삭제 확인 보류: " + safe_error_code(error))

    async def on_raw_bulk_message_delete(self, payload):
        if not self.accepts(getattr(payload, "channel_id", ""), getattr(payload, "guild_id", None)):
            return
        for identifier in getattr(payload, "message_ids", []):
            # Reuse the single-delete handler, including its scope and error rules.
            class Deleted:
                channel_id = payload.channel_id
                guild_id = getattr(payload, "guild_id", None)
                message_id = identifier
            await self.on_raw_message_delete(Deleted)

    async def refresh_checks(self):
        checks = await self.blocking(self.store.pending_checks)
        for check in checks:
            identifier = text(check.get("id"))
            prefix, separator, message_id = identifier.partition("_")
            if separator != "_" or prefix != self.config.channel_id or not SNOWFLAKE.fullmatch(message_id):
                continue
            if not re.fullmatch(r"[a-f0-9]{32}", text(check.get("nonce"))):
                await self.blocking(self.store.fail_check, check, "invalid-check-nonce")
                continue
            try:
                await self.fetch_and_observe(message_id, check)
            except Exception as error:
                self.poll_error = safe_error_code(error)
                self.log("등말소 승인 전 확인 보류: " + safe_error_code(error))

    async def catch_up(self):
        import discord
        # Each poll processes one page. The durable cursor advances only after a
        # successful observation, so restarts and transient errors do not skip rows.
        async for message in self.channel.history(limit=100, after=discord.Object(id=int(self.cursor)), oldest_first=True):
            async with self.lock(message.id):
                await self.ingest(message, allow_before_cutoff=True)
            self.cursor = await self.blocking(self.store.advance_cursor, str(message.id))

    async def refresh_recent(self):
        now = self.clock()
        if self.last_refresh and (now - self.last_refresh).total_seconds() < 60:
            return
        sources = await self.blocking(self.store.recent_sources)
        if sources:
            # Bound background work. Every explicit approval check has priority and
            # fetches its own canonical source regardless of age/background window.
            start = self.refresh_offset % len(sources)
            chosen = (sources[start:] + sources[:start])[:20]
            for source in chosen:
                try:
                    await self.fetch_and_observe(source["messageId"])
                except Exception as error:
                    self.poll_error = safe_error_code(error)
                    self.log("등말소 정기 원문 확인 보류: " + safe_error_code(error))
            self.refresh_offset = (start + len(chosen)) % len(sources)
        self.last_refresh = now

    async def poll_once(self):
        self.poll_error = ""
        await self.refresh_checks()
        await self.catch_up()
        await self.refresh_recent()
        await self.blocking(self.store.health, self.guild_id, self.poll_error)

    async def run(self):
        while not self.closed and not self.bot.is_closed():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = safe_error_code(error)
                self.log("등말소 연동 재시도 대기: " + code)
                try:
                    await self.blocking(self.store.health, self.guild_id, code)
                except Exception:
                    pass
            await asyncio.sleep(self.config.poll_seconds)


async def start_records_roster_bridge(bot, existing_db, current=None, environment=None):
    """Opt-in factory retained for callers; the Cog owns its own lifecycle."""
    if current is not None:
        await current.start()
        await current.start_roles()
        return current
    config = BridgeConfig.from_environment(environment)
    if config is None:
        return None
    database = records_database(config, existing_db, environment)
    bridge = RecordsRosterBridge(bot, FirestoreInboxStore(database, config), config)
    try:
        await bridge.start()
        await bridge.start_roles()
    except BaseException:
        await bridge.close()
        raise
    return bridge


# ═══════════════ 기존 봇 연결 자동 탐색 (쓰기 전에 읽기 검증) ═══════════════
class RecordsConfigurationError(ValueError):
    """A fixed, non-secret configuration error safe to show in the bot log."""

    def __init__(self, code: str, help_text: str):
        super().__init__(code)
        self.code = code
        self.help_text = help_text


def _sync_client(value) -> bool:
    """Accept a Firestore sync client only; never import or execute bot.py."""
    if value is None or not isinstance(getattr(value, "project", None), str):
        return False
    if "async" in type(value).__module__.lower() or type(value).__name__.lower().startswith("async"):
        return False
    return all(callable(getattr(value, method, None)) for method in ("document", "collection", "transaction"))


def _available_databases(bot, environment) -> list:
    """Reuse explicit bot attributes and public firebase_admin.get_app()."""
    clients = []
    for key in ("records_firestore", "firestore_db", "firebase_db", "db"):
        client = getattr(bot, key, None)
        if _sync_client(client) and all(client is not old for old in clients):
            clients.append(client)
    # These existing named/default apps do not require a bot.py assignment.
    try:
        import firebase_admin
        from firebase_admin import firestore
    except ImportError:
        if clients:
            return clients
        raise RecordsConfigurationError(
            "firebase-sdk-missing",
            "기존 봇 실행 환경에 firebase-admin 패키지가 필요합니다. pip install firebase-admin",
        ) from None
    app_name = text(environment.get("RECORDS_FIREBASE_APP", FIREBASE_APP_NAME), 120) or "[DEFAULT]"
    names = [app_name] + ([] if app_name == "[DEFAULT]" else ["[DEFAULT]"])
    for name in names:
        try:
            app = firebase_admin.get_app(name)
        except ValueError:
            continue
        client = firestore.client(app=app)
        if _sync_client(client) and all(client is not old for old in clients):
            clients.append(client)
    return clients


def resolve_connection(bot, environment=None):
    """Resolve exactly one existing records site before any bridge writes.

    Explicit RECORDS_* environment settings take precedence over file settings.
    Without a project setting only one existing initialized project is accepted.
    Without a site setting both known meta paths are read; exactly one must match.
    No site metadata, profile, credential file or database is created by discovery.
    """
    env = dict(os.environ if environment is None else environment)
    env.setdefault("RECORDS_BRIDGE_ENABLED", "true" if RECORDS_ENABLED else "false")
    enabled = text(env["RECORDS_BRIDGE_ENABLED"]).lower()
    if enabled in {"", "0", "false", "no", "off"}:
        return None, None
    if enabled not in {"1", "true", "yes", "on"}:
        raise RecordsConfigurationError("enabled-setting-invalid", "RECORDS_BRIDGE_ENABLED는 true 또는 false로 지정하세요.")
    project = text(env.get("RECORDS_FIREBASE_PROJECT_ID", RECORDS_PROJECT_ID), 80)
    site = text(env.get("RECORDS_SITE_ID", RECORDS_SITE_ID), 40)
    if project and not PROJECT_ID.fullmatch(project):
        raise RecordsConfigurationError("project-setting-invalid", "RECORDS_FIREBASE_PROJECT_ID 또는 파일 위의 RECORDS_PROJECT_ID를 확인하세요.")
    if site and site not in SITES:
        raise RecordsConfigurationError("site-setting-invalid", "기록실은 sst-dbo 또는 kmb-league 중 하나로 지정하세요.")
    clients = _available_databases(bot, env)
    if not project:
        projects = {client.project for client in clients if PROJECT_ID.fullmatch(client.project)}
        if len(projects) != 1:
            raise RecordsConfigurationError(
                "project-selection-required",
                "기록실 Firebase 프로젝트를 하나로 확인하지 못했습니다. 기존 RECORDS_FIREBASE_PROJECT_ID 환경변수 또는 파일 위 RECORDS_PROJECT_ID를 지정하세요. 새 DB는 만들지 않습니다.",
            )
        project = next(iter(projects))
    existing = next((client for client in clients if client.project == project), None)
    env["RECORDS_FIREBASE_PROJECT_ID"] = project
    # A provisional site label is only needed to name a separate Admin app;
    # records_database does not read/write any site documents.
    env["RECORDS_SITE_ID"] = site or "kmb-league"
    preliminary = BridgeConfig.from_environment(env)
    try:
        database = records_database(preliminary, existing, env)
    except ValueError:
        raise RecordsConfigurationError(
            "records-credentials-required",
            "기록실과 기존 봇의 Firebase 프로젝트가 다릅니다. 기존 RECORDS_FIREBASE_CLIENT_EMAIL / RECORDS_FIREBASE_PRIVATE_KEY 또는 해당 프로젝트의 초기화된 Admin 연결이 필요합니다. 비밀 키는 이 파일에 적지 마세요.",
        ) from None
    if not _sync_client(database) or database.project != project:
        raise RecordsConfigurationError("database-project-mismatch", "기록실 프로젝트와 DB 연결이 다릅니다. 설정을 확인하세요.")
    found = []
    for candidate in ([site] if site else ["sst-dbo", "kmb-league"]):
        snapshot = database.document("sites/" + candidate + "/meta/site").get(timeout=15)
        value = snapshot.to_dict() if snapshot.exists else None
        if isinstance(value, dict) and value.get("siteId") == candidate:
            found.append(candidate)
    if len(found) != 1:
        if len(found) > 1:
            raise RecordsConfigurationError(
                "site-selection-required",
                "같은 프로젝트에 SST와 KMB 기록실이 모두 있습니다. RECORDS_SITE_ID를 sst-dbo 또는 kmb-league로 지정하세요. 한 파일은 한 기록실에만 연결합니다.",
            )
        raise RecordsConfigurationError(
            "records-site-not-found",
            "현재 Firebase에 일치하는 기록실 meta/site를 찾지 못했습니다. RECORDS_FIREBASE_PROJECT_ID와 RECORDS_SITE_ID를 확인하세요. 계정/DB를 새로 만들지 않았습니다.",
        )
    env["RECORDS_SITE_ID"] = found[0]
    return BridgeConfig.from_environment(env), database


# ═══════════════ Cog 라이프사이클 / 시작·중지 ═══════════════
class RecordsRosterCog(commands.Cog, name="RecordsRoster"):
    """Own all listeners/tasks without replacing bot.py's existing events."""

    def __init__(self, bot):
        self.bot = bot
        self.bridge = None
        self.state = "waiting"
        self.last_error = ""
        self._closed = False
        self._startup_task = None
        self._close_task = None
        self._configuration_task = None
        self._activation_cutoff = message_id_at(datetime.now(timezone.utc))

    async def cog_load(self):
        # setup_hook runs before Gateway connects; never wait for ready here.
        self._ensure_startup()

    def _ensure_startup(self):
        if self._closed or (self._startup_task and not self._startup_task.done()):
            return
        if self.bridge is not None:
            inbox_alive = self.bridge.task is not None and not self.bridge.task.done()
            worker = self.bridge.role_worker
            roles_alive = not self.bridge.config.role_sync_enabled or (
                worker is not None and worker.task is not None and not worker.task.done()
            )
            if inbox_alive and roles_alive:
                return
        self._startup_task = asyncio.create_task(self._bootstrap(), name="records-roster-single-start")

    @commands.Cog.listener()
    async def on_ready(self):
        self._ensure_startup()

    async def _bootstrap(self):
        try:
            await self.bot.wait_until_ready()
            while not self._closed and not self.bot.is_closed():
                candidate = self.bridge
                try:
                    if candidate is None:
                        self.state = "checking-connection"
                        self._configuration_task = asyncio.create_task(
                            asyncio.to_thread(resolve_connection, self.bot), name="records-roster-check"
                        )
                        config, database = await asyncio.shield(self._configuration_task)
                        if self._closed:
                            return
                        if config is None:
                            self.state = "disabled"
                            LOGGER.info("등말소 Cog가 설정으로 비활성화되어 있습니다 (RECORDS_BRIDGE_ENABLED=false).")
                            return
                        self.state = "starting"
                        candidate = RecordsRosterBridge(
                            self.bot, FirestoreInboxStore(database, config), config,
                            log=lambda message: LOGGER.warning("%s", message),
                        )
                        candidate.cutoff = self._activation_cutoff
                        self.bridge = candidate
                    await candidate.start()
                    await candidate.start_roles()
                    if not self._closed:
                        self.state = "running"
                        self.last_error = ""
                        LOGGER.info(
                            "등말소 Cog 실행: 사이트=%s, 채널=%s, 역할=%s/%s. 웹 승인 후에만 역할을 변경합니다.",
                            candidate.config.site_id, CHANNEL_ID, ROLE_IDS["first"], ROLE_IDS["second"],
                        )
                    return
                except asyncio.CancelledError:
                    if candidate is not None:
                        await candidate.close()
                    raise
                except Exception as error:
                    if candidate is not None:
                        await candidate.close()
                    if self.bridge is candidate:
                        self.bridge = None
                    if self._closed:
                        return
                    self.state = "error"
                    # Only fixed, application-defined guidance is printed. An SDK
                    # exception may contain credentials; never print its repr.
                    code = error.code if isinstance(error, RecordsConfigurationError) else type(error).__name__
                    message = error.help_text if isinstance(error, RecordsConfigurationError) else (
                        "기록실 프로젝트·Firestore 권한·채널 읽기 권한·MESSAGE CONTENT INTENT를 확인하세요. 기존 봇 명령어는 유지합니다."
                    )
                    if code != self.last_error:
                        LOGGER.warning("등말소 연결 보류 (%s). %s", code, message)
                    self.last_error = code
                    await asyncio.sleep(STARTUP_RETRY_SECONDS)
        finally:
            # Cancellation of an executor waiter does not cancel its sync read.
            # Drain the read-only connection check before unload can complete.
            task = self._configuration_task
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
            elif task is not None and not task.cancelled():
                task.exception()
            self._configuration_task = None

    async def cog_unload(self):
        if self._close_task is None:
            self._closed = True
            self.state = "closed"
            self._close_task = asyncio.create_task(self._shutdown(), name="records-roster-single-close")
        await asyncio.shield(self._close_task)

    async def _shutdown(self):
        startup = self._startup_task
        if startup is not None and not startup.done():
            startup.cancel()
        service = self.bridge
        if service is not None:
            await service.close()
        if startup is not None:
            await asyncio.gather(startup, return_exceptions=True)
        if self.bridge is not None and self.bridge is not service:
            await self.bridge.close()
        self.bridge = None
        self._startup_task = None
        if getattr(self.bot, "_records_roster_single_owner", None) is self:
            delattr(self.bot, "_records_roster_single_owner")


async def setup(bot):
    """discord.py 2.x extension entrypoint: cogs.records_roster."""
    if bot.get_cog("RecordsRoster") is not None or getattr(bot, "_records_roster_single_owner", None) is not None:
        raise RuntimeError("등말소 Cog가 이미 실행 중입니다. records_roster.py를 여러 이름으로 중복 로드하지 마세요.")
    # Old direct-start integrations must not run alongside this Cog. Inspect only
    # an already loaded main module; importing bot.py would start a second bot.
    main = sys.modules.get("__main__")
    legacy = getattr(main, "RECORDS_ROSTER_BRIDGE", None)
    legacy_task = getattr(legacy, "task", None)
    if legacy_task is not None and not legacy_task.done():
        raise RuntimeError("옛 직접 실행 등말소 연동이 이미 실행 중입니다. 기존 시작 호출을 끄고 이 Cog만 한 번 불러오세요.")
    cog = RecordsRosterCog(bot)
    bot._records_roster_single_owner = cog
    try:
        await bot.add_cog(cog)
    except BaseException:
        await cog.cog_unload()
        raise


if __name__ == "__main__":
    raise SystemExit("이 파일은 cogs/records_roster.py로 저장해 기존 bot.py에서 불러오는 확장 모듈입니다. 별도 봇으로 실행하지 않습니다.")
