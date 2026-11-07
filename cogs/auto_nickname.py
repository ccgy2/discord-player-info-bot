"""역할 조건에 따라 서버 별명을 변경하는 discord.py 2.x 확장 코그.

기존 cogs/auto_nickname.py를 교체합니다. 저장 JSON(v1), 명령 및 복구 기록 호환.
메인 봇/토큰/자동 tree.sync는 포함하지 않습니다. Python 3.10+, discord.py >=2.4,<3.
절약판: auto-nickname-low-usage-20261107-1

환경변수(모두 선택):
  AUTO_NICK_RECONCILE_SECONDS=21600 : 안전 재검사 6시간. 0이면 정기 검사만 끔.
  AUTO_NICK_QUEUE_LIMIT=256         : 대기 ID 개수 상한(16~4096).
  AUTO_NICK_SCAN_DEBOUNCE_SECONDS=2 : 중복 전체 검사 요청 합치기(0~30초).
역할/별명 이벤트와 관리자 명령은 정기 검사 간격과 관계없이 처리합니다.
기존 봇의 멤버 인텐트/공유 캐시는 변경하지 않습니다.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
import weakref
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, TypeVar

import discord
from discord import app_commands
from discord.ext import commands

ALLOWED_GUILD_IDS: set[int] = set()
DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "auto_nickname.json"
MAX_RULES = 100
MAX_ROLES = 20
EDIT_INTERVAL = 0.8
RECONCILE_INTERVAL = 21600
VERSION = "auto-nickname-low-usage-20261107-1"
LOG = logging.getLogger(__name__)
T = TypeVar("T")


class StoreError(RuntimeError):
    """기존 파일을 초기화하거나 조용히 덮어써서는 안 되는 저장 오류."""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}은 {minimum}~{maximum} 사이 정수여야 합니다.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}은 {minimum}~{maximum} 사이 정수여야 합니다.")
    return value


def parse_guild_ids(raw: str) -> frozenset[int]:
    tokens = re.split(r"[\s,]+", raw.strip())
    if not raw.strip() or any(not re.fullmatch(r"[0-9]+", item) for item in tokens):
        raise ValueError("허용 서버 ID를 숫자로 지정하세요. 여러 ID는 쉼표로 구분합니다.")
    ids = frozenset(int(item) for item in tokens)
    if any(not 0 < value < 2**64 for value in ids):
        raise ValueError("서버 ID는 0보다 큰 Discord ID여야 합니다.")
    return ids


def configured_guild_ids() -> frozenset[int]:
    raw = os.environ.get("AUTO_NICK_GUILD_IDS")
    if raw is None:
        raw = ",".join(str(value) for value in ALLOWED_GUILD_IDS)
    return parse_guild_ids(raw)


def _clean_text(value: str) -> str:
    if not isinstance(value, str) or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise ValueError("내용에 줄바꿈이나 제어 문자를 사용할 수 없습니다.")
    value = " ".join(value.split())
    if not value or not any(not unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("별명 내용을 입력하세요.")
    return value


def render_nickname(rule: dict[str, Any], base_name: str) -> str:
    text = _clean_text(rule["text"])
    tag = f"[ {text} ]"
    mode = rule.get("mode", "fixed")
    if mode == "fixed":
        if len(tag) > 32:
            raise ValueError("고정 별명 내용은 대괄호/공백을 제외하고 최대 28자입니다.")
        return tag
    if mode != "prefix":
        raise ValueError("형식은 fixed 또는 prefix여야 합니다.")
    room = 32 - len(tag) - 1
    if room < 1:
        raise ValueError("이름 앞에 붙일 내용은 최대 26자입니다.")
    name = _clean_text(base_name)
    if len(name) > room:
        name = name[:room] if room == 1 else name[: room - 1] + "…"
    return f"{tag} {name}"


def choose_rule(rules: list[dict[str, Any]], role_ids: set[int], available_role_ids: set[int]) -> Optional[dict[str, Any]]:
    # 후보 목록과 규칙별 임시 set 두 개를 만들지 않습니다. 우선순위는 원본과 동일.
    return max(
        (r for r in rules if r.get("enabled", True) and r["roles"]
         and all(rid in role_ids and rid in available_role_ids for rid in r["roles"])),
        key=lambda r: (r["priority"], len(r["roles"]), -r["id"]), default=None,
    )


def _default_guild() -> dict[str, Any]:
    return {"enabled": False, "rules": [], "next_rule_id": 1, "members": {}}


def _integer(value: Any, minimum: int = 1, maximum: int = 2**64 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _validate_record(uid: str, record: Any) -> None:
    if not isinstance(uid, str) or not uid.isdecimal() or not _integer(int(uid)):
        raise ValueError("멤버 ID가 올바르지 않습니다.")
    if not isinstance(record, dict) or "original_nick" not in record:
        raise ValueError("원래 별명 기록이 누락됐습니다.")
    for key in ("original_nick", "last_applied_nick", "requested_nick", "pending_nick"):
        value = record.get(key)
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 32):
            raise ValueError("원래/적용 별명 기록이 올바르지 않습니다.")
    if record.get("joined_at") is not None and not isinstance(record["joined_at"], str):
        raise ValueError("가입 시각 기록이 올바르지 않습니다.")
    if "restore_confirmed" in record and type(record["restore_confirmed"]) is not bool:
        raise ValueError("복구 확인 상태가 올바르지 않습니다.")


def _validate_data(data: Any) -> None:
    try:
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("지원하지 않는 데이터 형식/버전입니다.")
        if not isinstance(data.get("guilds"), dict):
            raise ValueError("서버 설정이 올바르지 않습니다.")
        for gid, config in data["guilds"].items():
            if not isinstance(gid, str) or not gid.isdecimal() or not _integer(int(gid)):
                raise ValueError("서버 ID가 올바르지 않습니다.")
            if type(config["enabled"]) is not bool:
                raise ValueError("사용 설정이 올바르지 않습니다.")
            if not isinstance(config["rules"], list) or len(config["rules"]) > MAX_RULES:
                raise ValueError("규칙 목록이 올바르지 않습니다.")
            seen: set[int] = set()
            for rule in config["rules"]:
                rid = rule["id"]
                if not _integer(rid) or rid in seen:
                    raise ValueError("규칙 번호가 중복되거나 올바르지 않습니다.")
                seen.add(rid)
                roles = rule["roles"]
                if (not isinstance(roles, list) or not 1 <= len(roles) <= MAX_ROLES
                    or any(not _integer(role) for role in roles)
                    or len(set(roles)) != len(roles) or int(gid) in roles):
                    raise ValueError("역할 조건이 올바르지 않습니다.")
                if not _integer(rule["priority"], -1000, 1000) or type(rule["enabled"]) is not bool:
                    raise ValueError("규칙 우선순위/사용 설정이 올바르지 않습니다.")
                render_nickname(rule, "이름")
            if not _integer(config["next_rule_id"]) or config["next_rule_id"] <= max(seen, default=0):
                raise ValueError("다음 규칙 번호가 올바르지 않습니다.")
            if not isinstance(config["members"], dict):
                raise ValueError("원래 별명 기록이 올바르지 않습니다.")
            for uid, record in config["members"].items():
                _validate_record(uid, record)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"저장 데이터 검증 실패: {exc}") from exc


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"중복된 JSON 키: {key}")
        result[key] = value
    return result


class JsonStore:
    """v1 원자적 JSON 저장소. 공개된 내부 데이터는 직접 수정하지 않습니다.

    변경 시 필요한 가지에만 새 사본을 만들고, 파일 저장 성공 후 교체합니다.
    원본 별명은 Discord 편집 전에 fsync/replace로 저장합니다(지연 저장 안 함).
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {"version": 1, "guilds": {}}
        self.lock = asyncio.Lock()
        self.fault: Optional[str] = None

    def _read_validated(self) -> Optional[dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise StoreError(f"설정 파일을 읽을 수 없습니다: {self.path}") from exc
        try:
            candidate = json.loads(raw, object_pairs_hook=_unique_json_object)
            _validate_data(candidate)
        except (ValueError, StoreError) as exc:
            raise StoreError(f"손상된 설정을 보존하고 로드를 중단했습니다: {self.path}. 백업 파일을 확인하세요.") from exc
        return candidate

    async def load(self) -> None:
        async with self.lock:
            candidate = await asyncio.to_thread(self._read_validated)
            if candidate is not None:
                self.data = candidate

    def snapshot(self, guild_id: int, *, member_id: Optional[int] = None, include_members: bool = True) -> dict[str, Any]:
        source = self.data["guilds"].get(str(guild_id))
        if source is None:
            return _default_guild()
        result = copy.deepcopy({k: v for k, v in source.items() if k != "members"})
        if not include_members:
            result["members"] = {}
        elif member_id is not None:
            uid = str(member_id)
            record = source["members"].get(uid)
            result["members"] = {uid: copy.deepcopy(record)} if record is not None else {}
        else:
            result["members"] = copy.deepcopy(source["members"])
        return result

    def record(self, guild_id: int, member_id: int) -> Optional[dict[str, Any]]:
        config = self.data["guilds"].get(str(guild_id))
        return copy.deepcopy(config["members"].get(str(member_id))) if config is not None else None

    def has_record(self, guild_id: int, member_id: int) -> bool:
        config = self.data["guilds"].get(str(guild_id))
        return config is not None and str(member_id) in config["members"]

    def tracked_ids(self, guild_id: int) -> tuple[int, ...]:
        config = self.data["guilds"].get(str(guild_id))
        return tuple(map(int, config["members"])) if config is not None else ()

    def member_count(self, guild_id: int) -> int:
        config = self.data["guilds"].get(str(guild_id))
        return len(config["members"]) if config is not None else 0

    def _write_atomic(self, candidate: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(candidate, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _commit(self, guild_id: int, config: dict[str, Any]) -> None:
        # 다른 서버와 수정하지 않은 레코드는 공유하지만, 모든 갱신은 copy-on-write.
        candidate = dict(self.data)
        candidate["guilds"] = dict(self.data["guilds"])
        candidate["guilds"][str(guild_id)] = config
        writing = asyncio.create_task(asyncio.to_thread(self._write_atomic, candidate))
        cancelled = False
        try:
            while not writing.done():
                try:
                    await asyncio.shield(writing)
                except asyncio.CancelledError:
                    cancelled = True
            writing.result()
        except OSError as exc:
            self.fault = str(exc)
            raise StoreError("설정을 저장하지 못했습니다. 파일 권한과 남은 디스크 공간을 확인하세요.") from exc
        self.data = candidate
        self.fault = None
        if cancelled:
            raise asyncio.CancelledError

    async def mutate(self, guild_id: int, updater: Callable[[dict[str, Any]], T]) -> T:
        """규칙/설정 명령용. 다른 서버까지 deepcopy/재검증하지 않습니다."""
        async with self.lock:
            old = self.data["guilds"].get(str(guild_id), _default_guild())
            config = copy.deepcopy(old)
            result = updater(config)
            if config == old:
                return copy.deepcopy(result)
            _validate_data({"version": 1, "guilds": {str(guild_id): config}})
            # updater가 가진 참조로 저장 후 데이터를 수정하지 못하게 소유권 분리.
            config = copy.deepcopy(config)
            await self._commit(guild_id, config)
            return copy.deepcopy(result)

    async def mutate_member(self, guild_id: int, member_id: int,
                            updater: Callable[[Optional[dict[str, Any]]], Optional[dict[str, Any]]]) -> None:
        """멤버 한 명만 복사/검증. None 반환은 해당 기록 삭제입니다."""
        if not _integer(guild_id) or not _integer(member_id):
            raise StoreError("서버/멤버 ID가 올바르지 않습니다.")
        async with self.lock:
            old = self.data["guilds"].get(str(guild_id), _default_guild())
            uid = str(member_id)
            previous = old["members"].get(uid)
            record = copy.deepcopy(updater(copy.deepcopy(previous)))
            if record == previous:
                return
            if record is not None:
                try:
                    _validate_record(uid, record)
                except (ValueError, TypeError, KeyError) as exc:
                    raise StoreError(f"저장 데이터 검증 실패: {exc}") from exc
            config = dict(old)
            config["members"] = dict(old["members"])
            if record is None:
                config["members"].pop(uid, None)
            else:
                config["members"][uid] = record
            await self._commit(guild_id, config)


@dataclass(frozen=True)
class ApplyResult:
    kind: str
    detail: str = ""


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AutoNickname(commands.GroupCog, group_name="별명", group_description="역할 조건에 따른 자동 별명 관리"):
    def __init__(self, bot: commands.Bot, guild_ids: frozenset[int], data_path: Path = DEFAULT_DATA_PATH):
        self.bot = bot
        self.guild_ids = guild_ids
        self.store = JsonStore(data_path)
        self.reconcile_seconds = _env_int("AUTO_NICK_RECONCILE_SECONDS", RECONCILE_INTERVAL, 0, 604800)
        # 0(명시적 중지) 이외 1~299초 간격은 잘못된 과도한 조회로 간주합니다.
        if 0 < self.reconcile_seconds < 300:
            raise ValueError("AUTO_NICK_RECONCILE_SECONDS는 0 또는 300초 이상이어야 합니다.")
        self.queue_limit = _env_int("AUTO_NICK_QUEUE_LIMIT", 256, 16, 4096)
        self.scan_debounce = _env_int("AUTO_NICK_SCAN_DEBOUNCE_SECONDS", 2, 0, 30)
        self._queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue(maxsize=self.queue_limit)
        self._pending: set[tuple[int, int]] = set()
        self._overflow: set[int] = set()
        self._active: Optional[tuple[int, int]] = None
        self._member_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
        self._worker_task: Optional[asyncio.Task] = None
        self._maintenance_task: Optional[asyncio.Task] = None
        self._scans: dict[int, asyncio.Task] = {}
        self._scan_slot = asyncio.Semaphore(1)
        self._rescan: set[int] = set()
        self._fresh_scans: set[int] = set()
        self._scan_info: dict[int, str] = {}
        self._counters: dict[int, Counter] = defaultdict(Counter)
        self._failures: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))
        self._last_edit = 0.0
        self._closing = False
        self._initial_scan_done = False

    async def cog_load(self) -> None:
        if not self.guild_ids:
            raise RuntimeError("AUTO_NICK_GUILD_IDS 또는 ALLOWED_GUILD_IDS에 허용 서버를 지정하세요.")
        if not self.bot.intents.members or not self.bot.intents.guilds:
            raise RuntimeError("기존 봇에서 intents.members=True 및 intents.guilds=True가 필요합니다.")
        await self.store.load()
        self._worker_task = asyncio.create_task(self._worker(), name="auto-nickname-worker")
        self._maintenance_task = asyncio.create_task(self._maintenance(), name="auto-nickname-reconcile")

    async def cog_unload(self) -> None:
        self._closing = True
        tasks = [t for t in [self._worker_task, self._maintenance_task, *self._scans.values()] if t]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._pending.clear()
        self._overflow.clear()
        self._rescan.clear()
        self._fresh_scans.clear()
        self._scans.clear()
        for gid in self.guild_ids:
            guild = discord.Object(id=gid)
            if self.bot.tree.get_command("별명", guild=guild) is self.app_command:
                self.bot.tree.remove_command("별명", guild=guild)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild_id not in self.guild_ids:
            await self._reply(interaction, "이 서버는 자동 별명 기능을 사용할 수 있는 서버가 아닙니다.")
            return False
        if not interaction.permissions.manage_guild:
            await self._reply(interaction, "이 명령은 **서버 관리** 또는 **관리자** 권한이 필요합니다.")
            return False
        return True

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, app_commands.CheckFailure) and interaction.response.is_done():
            return
        if isinstance(original, (ValueError, StoreError)):
            message = str(original)
        elif isinstance(original, app_commands.CheckFailure):
            message = "이 명령을 실행할 권한이 없거나 허용된 서버가 아닙니다."
        else:
            LOG.error("자동 별명 명령 오류", exc_info=(type(original), original, original.__traceback__))
            message = "명령 처리 중 오류가 발생했습니다. 봇 로그와 /별명 상태를 확인하세요."
        if not interaction.response.is_done() or not interaction.is_expired():
            await self._reply(interaction, message)

    @staticmethod
    async def _reply(interaction: discord.Interaction, content: Optional[str] = None, *, embed: Optional[discord.Embed] = None) -> None:
        kwargs: dict[str, Any] = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    def _member_lock(self, key: tuple[int, int]) -> asyncio.Lock:
        lock = self._member_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._member_locks[key] = lock
        return lock

    def enqueue(self, guild_id: int, member_id: int) -> bool:
        key = (guild_id, member_id)
        if self._closing or guild_id not in self.guild_ids:
            return False
        if key in self._pending:
            return True
        try:
            self._queue.put_nowait(key)
        except asyncio.QueueFull:
            # 이벤트별 Task/Member 객체를 쌓지 않고 서버 ID만 남겨 나중에 재검사.
            self._overflow.add(guild_id)
            return False
        self._pending.add(key)
        return True

    async def _enqueue_wait(self, guild_id: int, member_id: int) -> bool:
        key = (guild_id, member_id)
        if self._closing or guild_id not in self.guild_ids:
            return False
        if key in self._pending:
            return True
        # 단일 스캔 생산자만 최대 1개의 추가 예약을 가진 채 공간을 기다립니다.
        self._pending.add(key)
        try:
            await self._queue.put(key)
        except BaseException:
            self._pending.discard(key)
            raise
        return True

    def request_scan(self, guild_id: int, *, fresh: bool = False) -> None:
        if self._closing or guild_id not in self.guild_ids:
            return
        self._rescan.add(guild_id)
        if fresh:
            self._fresh_scans.add(guild_id)
        current = self._scans.get(guild_id)
        if current is None or current.done():
            self._scans[guild_id] = asyncio.create_task(self._scan(guild_id), name=f"auto-nickname-scan-{guild_id}")

    def _request_all(self) -> None:
        for gid in self.guild_ids:
            config = self.store.snapshot(gid, include_members=False)
            if config["enabled"] or self.store.member_count(gid):
                self.request_scan(gid, fresh=True)

    async def _maintenance(self) -> None:
        await self.bot.wait_until_ready()
        self._request_all()
        self._initial_scan_done = True
        if self.reconcile_seconds == 0:
            return
        while True:
            await asyncio.sleep(self.reconcile_seconds)
            if self.bot.is_ready():
                self._request_all()

    async def _iter_members(self, guild: discord.Guild, fresh: bool) -> AsyncIterator[discord.Member]:
        if not guild.chunked:
            # on_member_update는 멤버 캐시에 의존합니다. 메인 봇 캐시를 함부로 끄지 않음.
            # 최초 누락 목록은 원본처럼 확보하되 이 코그의 별도 멤버 캐시는 만들지 않음.
            members = await asyncio.wait_for(guild.chunk(cache=True), timeout=120)
            for member in members:
                yield member
        elif fresh:
            # 정기/수동 안전 검사는 최신 REST 목록을 페이지 단위로 소비합니다.
            # 모든 ID를 fetch_member()로 다시 조회하지 않습니다.
            async for member in guild.fetch_members(limit=None):
                yield member
        else:
            for member in guild.members:
                yield member

    def _needs_work(self, config: dict[str, Any], member: discord.Member) -> bool:
        if member.bot:
            return False
        record = self.store.record(member.guild.id, member.id)
        joined = self._joined_at(member)
        if record:
            if record.get("joined_at") and joined and record["joined_at"] != joined:
                return True
            if record.get("pending_nick") is not None or record.get("restore_confirmed", False):
                return True
        rule = self._selected(config, member)
        if rule is None:
            return record is not None
        desired = render_nickname(rule, self._base_name(member, record))
        return not (member.nick == desired or (
            record and record.get("requested_nick") == desired
            and member.nick == record.get("last_applied_nick")
            and record.get("last_applied_nick") is not None
        ))

    async def _scan(self, guild_id: int) -> None:
        try:
            await self.bot.wait_until_ready()
            while guild_id in self._rescan and not self._closing:
                await asyncio.sleep(self.scan_debounce)
                async with self._scan_slot:
                    self._rescan.discard(guild_id)
                    fresh = guild_id in self._fresh_scans
                    self._fresh_scans.discard(guild_id)
                    guild = self.bot.get_guild(guild_id)
                    if guild is None or guild.unavailable:
                        self._scan_info[guild_id] = "서버에 연결되지 않아 대기 중"
                        return
                    config = self.store.snapshot(guild_id, include_members=False)
                    remaining = set(self.store.tracked_ids(guild_id))
                    seen = scheduled = skipped = 0
                    self._scan_info[guild_id] = "변경/복원 필요한 멤버 확인 중"
                    available = {role.id for role in guild.roles}
                    active_rules = config["enabled"] and any(
                        rule["enabled"] and set(rule["roles"]).issubset(available) for rule in config["rules"]
                    )
                    if active_rules:
                        async for member in self._iter_members(guild, fresh):
                            seen += 1
                            remaining.discard(member.id)
                            if self._needs_work(config, member):
                                scheduled += int(await self._enqueue_wait(guild_id, member.id))
                            else:
                                skipped += 1
                            if seen % 128 == 0:
                                await asyncio.sleep(0)
                    # 탈퇴/미발견 멤버와 꺼진 규칙의 복원 기록도 놓치지 않습니다.
                    for member_id in remaining:
                        scheduled += int(await self._enqueue_wait(guild_id, member_id))
                    self._counters[guild_id]["filtered"] += skipped
                    self._scan_info[guild_id] = f"목록 {seen}명 확인 · 변경/복원 {scheduled}명 예약 · 불필요 조회 {skipped}명 생략"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._scan_info[guild_id] = "멤버 목록 확인 실패: 인텐트/연결/봇 로그 확인 후 다시 적용하세요."
            self._failure(guild_id, 0, type(exc).__name__)
            LOG.exception("자동 별명 멤버 목록 조회 실패: guild=%s", guild_id)
            # 읽기 실패로 기존 원본을 지우지 않습니다. 복원 대상은 bounded queue로 재시도.
            async with self._scan_slot:
                for uid in self.store.tracked_ids(guild_id):
                    await self._enqueue_wait(guild_id, uid)
        finally:
            if self._scans.get(guild_id) is asyncio.current_task():
                self._scans.pop(guild_id, None)
                # 실패 도중 새 규칙/수동 요청이 들어온 경우 그 요청을 잃지 않습니다.
                if guild_id in self._rescan and not self._closing:
                    self.request_scan(guild_id)

    def _failure(self, guild_id: int, member_id: int, detail: str) -> None:
        self._failures[guild_id].append(f"{member_id or '서버'}: {detail}")

    async def _worker(self) -> None:
        await self.bot.wait_until_ready()
        while not self._closing:
            key = await self._queue.get()
            self._pending.discard(key)
            self._active = key
            try:
                await self.bot.wait_until_ready()
                result = await self._process_member(*key)
                self._counters[key[0]][result.kind] += 1
                if result.kind in {"blocked", "error", "uncertain"}:
                    self._failure(*key, result.detail)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._counters[key[0]]["error"] += 1
                self._failure(*key, str(exc) if isinstance(exc, StoreError) else type(exc).__name__)
                LOG.exception("자동 별명 처리 실패: guild=%s member=%s", *key)
            finally:
                self._active = None
                self._queue.task_done()
                if not self._closing and self._queue.empty() and self._overflow:
                    overflow = tuple(self._overflow)
                    self._overflow.clear()
                    for gid in overflow:
                        self.request_scan(gid, fresh=True)

    @staticmethod
    def _selected(config: dict[str, Any], member: discord.Member) -> Optional[dict[str, Any]]:
        if not config["enabled"]:
            return None
        return choose_rule(config["rules"], {r.id for r in member.roles}, {r.id for r in member.guild.roles})

    @staticmethod
    def _base_name(member: discord.Member, record: Optional[dict[str, Any]]) -> str:
        original = record["original_nick"] if record is not None else member.nick
        return original if original is not None else (member.global_name or member.name)

    @staticmethod
    def _blocked_reason(member: discord.Member) -> Optional[str]:
        me = member.guild.me
        if member.bot:
            return "봇 계정은 자동 변경 대상에서 제외됩니다."
        if member.id == member.guild.owner_id:
            return "서버 소유자의 별명은 봇으로 변경할 수 없습니다."
        if me is None or not me.guild_permissions.manage_nicknames:
            return "봇에 별명 관리 권한이 없습니다."
        if me.top_role <= member.top_role:
            return "대상의 최고 역할이 봇의 최고 역할과 같거나 높습니다."
        return None

    @staticmethod
    def _joined_at(member: discord.Member) -> Optional[str]:
        return member.joined_at.isoformat() if member.joined_at else None

    async def _drop_record(self, guild_id: int, member_id: int) -> None:
        if self.store.has_record(guild_id, member_id):
            await self.store.mutate_member(guild_id, member_id, lambda record: None)

    async def _edit_wait(self) -> None:
        await asyncio.sleep(max(0.0, EDIT_INTERVAL - (time.monotonic() - self._last_edit)))
        self._last_edit = time.monotonic()

    async def _process_member(self, guild_id: int, member_id: int) -> ApplyResult:
        if guild_id not in self.guild_ids or self._closing:
            return ApplyResult("ignored")
        async with self._member_lock((guild_id, member_id)):
            guild = self.bot.get_guild(guild_id)
            if guild is None or guild.unavailable:
                return ApplyResult("blocked", "서버 연결을 확인할 수 없습니다.")
            try:
                # 실제 편집/복원 작업은 원본과 같이 REST로 현재 상태를 확인합니다.
                member = await guild.fetch_member(member_id)
            except discord.NotFound:
                await self._drop_record(guild_id, member_id)
                return ApplyResult("left")
            except discord.HTTPException as exc:
                return ApplyResult("error", f"멤버 조회 HTTP {exc.status}; 다음 검사에서 재시도")
            if member.bot:
                return ApplyResult("ignored")
            config = self.store.snapshot(guild_id, member_id=member_id)
            record = config["members"].get(str(member_id))
            joined_at = self._joined_at(member)
            if record and record.get("joined_at") and joined_at and record["joined_at"] != joined_at:
                await self._drop_record(guild_id, member_id)
                record = None
                config = self.store.snapshot(guild_id, member_id=member_id)
            rule = self._selected(config, member)
            if rule is None:
                return await self._restore_member(member, record)
            desired = render_nickname(rule, self._base_name(member, record))
            if member.nick == desired or (
                record and record.get("requested_nick") == desired
                and member.nick == record.get("last_applied_nick")
                and record.get("last_applied_nick") is not None
            ):
                if record and record.get("pending_nick"):
                    await self._mark_applied(guild_id, member_id, desired, member.nick)
                return ApplyResult("unchanged")
            blocked = self._blocked_reason(member)
            if blocked:
                return ApplyResult("blocked", blocked)
            if record is None:
                record = {"original_nick": member.nick, "joined_at": joined_at, "requested_nick": None,
                          "last_applied_nick": None, "pending_nick": None}
            record["pending_nick"] = desired
            record.pop("restore_confirmed", None)
            await self.store.mutate_member(guild_id, member_id, lambda previous: record)
            await self._edit_wait()
            try:
                latest = await guild.fetch_member(member_id)
            except discord.NotFound:
                await self._drop_record(guild_id, member_id)
                return ApplyResult("left")
            except discord.HTTPException as exc:
                return ApplyResult("error", f"편집 전 멤버 재확인 HTTP {exc.status}; 원본 기록 보관")
            if joined_at and self._joined_at(latest) and self._joined_at(latest) != joined_at:
                self.enqueue(guild_id, member_id)
                return ApplyResult("superseded")
            latest_config = self.store.snapshot(guild_id, include_members=False)
            latest_rule = self._selected(latest_config, latest)
            if latest_rule is None or render_nickname(latest_rule, self._base_name(latest, record)) != desired:
                self.enqueue(guild_id, member_id)
                return ApplyResult("superseded")
            blocked = self._blocked_reason(latest)
            if blocked:
                return ApplyResult("blocked", blocked)
            try:
                updated = await latest.edit(nick=desired, reason=f"자동 별명 규칙 #{latest_rule['id']}")
            except discord.NotFound:
                await self._drop_record(guild_id, member_id)
                return ApplyResult("left")
            except discord.Forbidden:
                return ApplyResult("blocked", "Discord가 별명 변경을 거부했습니다. 권한과 역할 순서를 확인하세요.")
            except discord.HTTPException as exc:
                return ApplyResult("error", f"별명 변경 HTTP {exc.status}; /별명 적용 또는 다음 자동 검사에서 재시도")
            actual = updated.nick if updated is not None and updated.nick is not None else desired
            await self._mark_applied(guild_id, member_id, desired, actual)
            return ApplyResult("changed", f"규칙 #{latest_rule['id']}")

    async def _mark_applied(self, guild_id: int, member_id: int, requested: str, actual: Optional[str]) -> None:
        def update(record: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
            if record is not None:
                record.update(requested_nick=requested, last_applied_nick=actual, pending_nick=None)
            return record
        await self.store.mutate_member(guild_id, member_id, update)

    async def _restore_member(self, member: discord.Member, record: Optional[dict[str, Any]]) -> ApplyResult:
        if record is None:
            return ApplyResult("unchanged")
        gid, uid = member.guild.id, member.id
        original = record["original_nick"]
        observed_nick = member.nick
        observed_join = self._joined_at(member)
        if member.nick == original:
            await self._drop_record(gid, uid)
            return ApplyResult("unchanged")
        managed_names = {v for v in (record.get("last_applied_nick"), record.get("pending_nick")) if v is not None}
        if member.nick not in managed_names and not record.get("restore_confirmed", False):
            if record.get("pending_nick") is not None:
                return ApplyResult("uncertain", "적용 도중 중단된 기록: /별명 복구에서 원래 별명 복원 또는 현재 별명 유지를 선택하세요.")
            await self._drop_record(gid, uid)
            return ApplyResult("preserved", "수동으로 변경된 별명 유지")
        blocked = self._blocked_reason(member)
        if blocked:
            return ApplyResult("blocked", blocked)
        await self._edit_wait()
        try:
            latest = await member.guild.fetch_member(uid)
        except discord.NotFound:
            await self._drop_record(gid, uid)
            return ApplyResult("left")
        except discord.HTTPException as exc:
            return ApplyResult("error", f"복원 전 멤버 재확인 HTTP {exc.status}; 원본 기록 보관")
        if observed_join and self._joined_at(latest) and self._joined_at(latest) != observed_join:
            self.enqueue(gid, uid)
            return ApplyResult("superseded")
        if self._selected(self.store.snapshot(gid, include_members=False), latest) is not None:
            self.enqueue(gid, uid)
            return ApplyResult("superseded")
        if latest.nick != observed_nick:
            self.enqueue(gid, uid)
            return ApplyResult("superseded")
        blocked = self._blocked_reason(latest)
        if blocked:
            return ApplyResult("blocked", blocked)
        try:
            await latest.edit(nick=original, reason="자동 별명 조건 해제: 원래 별명 복원")
        except discord.NotFound:
            await self._drop_record(gid, uid)
            return ApplyResult("left")
        except discord.Forbidden:
            return ApplyResult("blocked", "원래 별명 복원 권한이 없습니다. 원본 기록은 보관했습니다.")
        except discord.HTTPException as exc:
            return ApplyResult("error", f"별명 복원 HTTP {exc.status}; 원본 기록 보관")
        await self._drop_record(gid, uid)
        return ApplyResult("restored")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._initial_scan_done:
            self._request_all()

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        self._request_all()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        gid = after.guild.id
        if gid not in self.guild_ids:
            return
        roles_changed = {r.id for r in before.roles} != {r.id for r in after.roles}
        if after.bot:
            if self.bot.user and after.id == self.bot.user.id and roles_changed:
                self.request_scan(gid)
            return
        if roles_changed or before.nick != after.nick:
            config = self.store.snapshot(gid, include_members=False)
            if not self._needs_work(config, after):
                return
            # 자신이 편집한 별명 이벤트는 편집 작업이 성공 기록을 남기므로 중복 조회 제외.
            record = self.store.record(gid, after.id)
            if (not roles_changed and self._active == (gid, after.id) and record
                and record.get("pending_nick") == after.nick):
                return
            self.enqueue(gid, after.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        gid = member.guild.id
        if gid in self.guild_ids and not member.bot:
            config = self.store.snapshot(gid, include_members=False)
            if self._needs_work(config, member):
                self.enqueue(gid, member.id)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        if payload.guild_id in self.guild_ids and self.store.has_record(payload.guild_id, payload.user.id):
            self.enqueue(payload.guild_id, payload.user.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        # 규칙에 직접 쓰이지 않은 역할 삭제도 봇/멤버의 역할 순위를 바꿀 수 있습니다.
        self.request_scan(role.guild.id)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if before.permissions != after.permissions or before.position != after.position:
            self.request_scan(after.guild.id)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        if self._initial_scan_done:
            self.request_scan(guild.id, fresh=True)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        if before.name == after.name and before.global_name == after.global_name:
            return
        for gid in self.guild_ids:
            config = self.store.snapshot(gid, include_members=False)
            record = self.store.record(gid, after.id)
            if config["enabled"] and record and record["original_nick"] is None:
                self.enqueue(gid, after.id)

    @staticmethod
    def _role_ids(guild: discord.Guild, roles: list[Optional[discord.Role]]) -> list[int]:
        selected = [role for role in roles if role is not None]
        if not 1 <= len(selected) <= MAX_ROLES:
            raise ValueError("역할을 1개 이상, 최대 5개 선택하세요.")
        if any(role.guild.id != guild.id or role.id == guild.id for role in selected):
            raise ValueError("현재 서버의 역할만 선택할 수 있으며 @everyone은 조건으로 사용할 수 없습니다.")
        ids = [role.id for role in selected]
        if len(set(ids)) != len(ids):
            raise ValueError("같은 역할을 두 번 선택할 수 없습니다.")
        return sorted(ids)

    @staticmethod
    def _rule_by_id(config: dict[str, Any], rule_id: int) -> dict[str, Any]:
        rule = next((rule for rule in config["rules"] if rule["id"] == rule_id), None)
        if rule is None:
            raise ValueError("해당 번호의 규칙이 없습니다. /별명 규칙목록에서 번호를 확인하세요.")
        return rule

    @app_commands.command(name="사용", description="자동 별명을 켜거나, 끄고 원래 별명을 복원합니다")
    @app_commands.rename(state="상태")
    @app_commands.choices(state=[
        app_commands.Choice(name="켜기", value="on"),
        app_commands.Choice(name="끄기 · 원래 별명 복원", value="off"),
    ])
    async def set_enabled(self, interaction: discord.Interaction, state: str) -> None:
        if state not in {"on", "off"}:
            raise ValueError("켜기 또는 끄기를 선택하세요.")
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id
        await self.store.mutate(gid, lambda config: config.__setitem__("enabled", state == "on"))
        self.request_scan(gid)
        if state == "on":
            await self._reply(interaction, "자동 별명을 **켰습니다**. 기존 멤버부터 차례로 확인합니다.\n규칙이 없다면 `/별명 규칙추가`로 등록하세요. 결과는 `/별명 상태`에서 확인할 수 있습니다.")
        else:
            await self._reply(interaction, "자동 별명을 **껐습니다**. 이 코그가 변경한 별명은 원래대로 복원하도록 예약했습니다.\n권한 때문에 복원하지 못한 원본은 보관합니다. `/별명 상태`로 진행 상황을 확인하세요.")

    @app_commands.command(name="규칙추가", description="선택한 역할을 모두 가진 멤버에게 적용할 규칙을 추가합니다")
    @app_commands.rename(text="내용", role1="역할1", role2="역할2", role3="역할3", role4="역할4", role5="역할5", mode="형식", priority="우선순위")
    @app_commands.describe(
        text="대괄호 안에 들어갈 내용. 대괄호는 자동으로 붙습니다.",
        role1="필수 역할", role2="추가 필수 역할 (선택)", role3="추가 필수 역할 (선택)",
        role4="추가 필수 역할 (선택)", role5="추가 필수 역할 (선택)",
        mode="기본: 별명 전체를 [ 내용 ]으로 변경", priority="큰 숫자가 우선. 같으면 역할 조건이 많은 규칙 우선 (기본 0)",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="별명 전체 변경: [ 내용 ]", value="fixed"),
        app_commands.Choice(name="이름 앞에 붙이기: [ 내용 ] 기존이름", value="prefix"),
    ])
    async def add_rule(
        self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 28], role1: discord.Role,
        role2: Optional[discord.Role] = None, role3: Optional[discord.Role] = None,
        role4: Optional[discord.Role] = None, role5: Optional[discord.Role] = None,
        mode: str = "fixed", priority: app_commands.Range[int, -1000, 1000] = 0,
    ) -> None:
        roles = self._role_ids(interaction.guild, [role1, role2, role3, role4, role5])
        template = {"text": _clean_text(text), "roles": roles, "mode": mode, "priority": priority, "enabled": True}
        example = render_nickname(template, "민수")
        await interaction.response.defer(ephemeral=True)
        def update(config: dict[str, Any]) -> dict[str, Any]:
            if len(config["rules"]) >= MAX_RULES:
                raise ValueError("규칙은 서버당 최대 100개입니다.")
            rule = dict(template, id=config["next_rule_id"])
            config["next_rule_id"] += 1
            config["rules"].append(rule)
            return rule
        rule = await self.store.mutate(interaction.guild_id, update)
        self.request_scan(interaction.guild_id)
        state = self.store.snapshot(interaction.guild_id, include_members=False)["enabled"]
        await self._reply(interaction,
            f"**규칙 #{rule['id']} 등록 완료**\n"
            f"조건: {' + '.join(f'<@&{rid}>' for rid in roles)} **모두 보유**\n"
            f"별명 예시: {discord.utils.escape_markdown(example)}\n우선순위: {priority}\n"
            + ("기존 멤버도 다시 확인합니다." if state else "현재 자동 별명이 꺼져 있습니다. `/별명 사용`에서 **켜기**를 선택하세요.")
        )

    @app_commands.command(name="규칙수정", description="규칙 내용을 수정합니다. 역할을 바꾸면 선택한 역할들로 조건 전체를 교체합니다")
    @app_commands.rename(rule_id="번호", text="내용", mode="형식", priority="우선순위", enabled="사용", role1="역할1", role2="역할2", role3="역할3", role4="역할4", role5="역할5")
    @app_commands.describe(rule_id="규칙목록에 표시된 번호", role1="조건을 교체할 때의 첫 역할. 역할1만 넣으면 단일 역할 조건으로 교체됩니다.", enabled="False면 이 규칙만 사용 중지")
    @app_commands.choices(mode=[
        app_commands.Choice(name="별명 전체 변경: [ 내용 ]", value="fixed"),
        app_commands.Choice(name="이름 앞에 붙이기: [ 내용 ] 기존이름", value="prefix"),
    ])
    async def edit_rule(
        self, interaction: discord.Interaction, rule_id: app_commands.Range[int, 1],
        text: Optional[app_commands.Range[str, 1, 28]] = None,
        mode: Optional[str] = None, priority: Optional[app_commands.Range[int, -1000, 1000]] = None,
        enabled: Optional[bool] = None,
        role1: Optional[discord.Role] = None, role2: Optional[discord.Role] = None,
        role3: Optional[discord.Role] = None, role4: Optional[discord.Role] = None, role5: Optional[discord.Role] = None,
    ) -> None:
        role_inputs = [role1, role2, role3, role4, role5]
        if any(role is not None for role in role_inputs) and role1 is None:
            raise ValueError("역할 조건을 교체하려면 역할1부터 선택하세요.")
        roles = self._role_ids(interaction.guild, role_inputs) if role1 is not None else None
        if all(value is None for value in (text, mode, priority, enabled, roles)):
            raise ValueError("변경할 내용을 하나 이상 입력하세요.")
        await interaction.response.defer(ephemeral=True)
        def update(config: dict[str, Any]) -> dict[str, Any]:
            rule = self._rule_by_id(config, rule_id)
            for key, value in (("text", _clean_text(text) if text is not None else None), ("mode", mode), ("priority", priority), ("enabled", enabled), ("roles", roles)):
                if value is not None:
                    rule[key] = value
            render_nickname(rule, "민수")
            return rule
        rule = await self.store.mutate(interaction.guild_id, update)
        self.request_scan(interaction.guild_id)
        await self._reply(interaction, f"**규칙 #{rule_id} 수정 완료**\n예시: {discord.utils.escape_markdown(render_nickname(rule, '민수'))}\n사용: {'켜짐' if rule['enabled'] else '꺼짐'} · 우선순위: {rule['priority']}\n기존 멤버도 현재 설정에 맞춰 다시 확인합니다.")

    @app_commands.command(name="규칙삭제", description="규칙 하나를 삭제하고 해당 멤버의 별명을 다시 판단합니다")
    @app_commands.rename(rule_id="번호")
    async def delete_rule(self, interaction: discord.Interaction, rule_id: app_commands.Range[int, 1]) -> None:
        await interaction.response.defer(ephemeral=True)
        def update(config: dict[str, Any]) -> None:
            self._rule_by_id(config, rule_id)
            config["rules"] = [rule for rule in config["rules"] if rule["id"] != rule_id]
        await self.store.mutate(interaction.guild_id, update)
        self.request_scan(interaction.guild_id)
        await self._reply(interaction, f"규칙 **#{rule_id}**을 삭제했습니다. 다른 일치 규칙이 있으면 그 규칙을 적용하고, 없으면 원래 별명을 복원합니다.")

    @app_commands.command(name="규칙목록", description="규칙 번호, 역할 조건, 우선순위와 삭제된 역할을 확인합니다")
    @app_commands.rename(page="페이지")
    async def list_rules(self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1) -> None:
        config = self.store.snapshot(interaction.guild_id, include_members=False)
        rules = sorted(config["rules"], key=lambda rule: (-rule["priority"], -len(rule["roles"]), rule["id"]))
        pages = max(1, (len(rules) + 7) // 8)
        if page > pages:
            raise ValueError(f"페이지는 1~{pages} 사이에서 선택하세요.")
        embed = discord.Embed(title="자동 별명 규칙", color=discord.Color.teal())
        embed.description = f"자동 별명: **{'켜짐' if config['enabled'] else '꺼짐'}**\n선정 순서: 높은 우선순위 → 많은 필수 역할 → 작은 규칙 번호"
        for rule in rules[(page - 1) * 8: page * 8]:
            missing = [rid for rid in rule["roles"] if interaction.guild.get_role(rid) is None]
            state = "꺼짐" if not rule["enabled"] else ("역할 삭제로 적용 불가" if missing else "사용 중")
            conditions = " + ".join(f"<@&{rid}>" if rid not in missing else f"삭제된 역할({rid})" for rid in rule["roles"])
            embed.add_field(name=f"#{rule['id']} · {state} · 우선순위 {rule['priority']}", value=f"조건: {conditions} 모두\n예시: {discord.utils.escape_markdown(render_nickname(rule, '민수'))}", inline=False)
        if not rules:
            embed.add_field(name="등록된 규칙이 없습니다", value="`/별명 규칙추가`에서 내용과 역할을 선택하세요.", inline=False)
        embed.set_footer(text=f"{page}/{pages}페이지 · 총 {len(rules)}개 · 역할은 규칙마다 최대 5개 (모두 충족)")
        await self._reply(interaction, embed=embed)

    @app_commands.command(name="미리보기", description="실제 별명 변경 없이 선택될 규칙과 변경 결과를 확인합니다")
    @app_commands.rename(member="대상")
    async def preview(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        member = await interaction.guild.fetch_member((member or interaction.user).id)
        config = self.store.snapshot(interaction.guild_id, member_id=member.id)
        record = config["members"].get(str(member.id))
        if record and record.get("joined_at") and member.joined_at and record["joined_at"] != member.joined_at.isoformat():
            record = None
        rule = self._selected(config, member)
        desired = member.nick
        detail = "맞는 규칙이 없어 현재 별명을 유지합니다."
        if rule is not None:
            desired = render_nickname(rule, self._base_name(member, record))
            detail = f"규칙 #{rule['id']} · 우선순위 {rule['priority']} · 필수 역할 {len(rule['roles'])}개 모두 충족"
        elif record:
            names = {value for value in (record.get("last_applied_nick"), record.get("pending_nick")) if value is not None}
            if member.nick in names or member.nick == record["original_nick"] or record.get("restore_confirmed", False):
                desired = record["original_nick"]
                detail = "자동 별명이 꺼졌거나 조건이 해제되어 원래 별명을 복원합니다."
            elif record.get("pending_nick") is not None:
                detail = "적용 도중 중단되어 현재 별명의 출처를 확정할 수 없습니다. 별명과 원본 기록을 보존하며 /별명 복구에서 처리 방법을 선택하세요."
            else:
                detail = "조건 해제 후 수동으로 바꾼 별명을 유지하고 관리 기록을 정리합니다."
        fallback = member.global_name or member.name
        shown = desired if desired is not None else f"{fallback} (서버 별명 없음)"
        embed = discord.Embed(title="별명 미리보기", description=detail, color=discord.Color.teal())
        embed.add_field(name="대상", value=f"{member.mention} · {member.id}", inline=False)
        embed.add_field(name="현재", value=discord.utils.escape_markdown(member.display_name), inline=True)
        embed.add_field(name="적용 결과", value=discord.utils.escape_markdown(shown), inline=True)
        if not config["enabled"]:
            hypothetical = choose_rule(config["rules"], {role.id for role in member.roles}, {role.id for role in member.guild.roles})
            if hypothetical:
                example = render_nickname(hypothetical, self._base_name(member, record))
                embed.add_field(name=f"기능을 켜면 적용될 결과 · 규칙 #{hypothetical['id']}", value=discord.utils.escape_markdown(example), inline=False)
            else:
                embed.add_field(name="기능을 켜면", value="현재 역할과 일치하는 사용 중 규칙이 없습니다.", inline=False)
        blocked = self._blocked_reason(member)
        if blocked:
            embed.add_field(name="변경 제한", value=blocked, inline=False)
        embed.set_footer(text="미리보기는 별명이나 설정을 변경하지 않습니다. 이름 부분이 길면 32자에 맞춰 줄입니다.")
        await self._reply(interaction, embed=embed)

    @app_commands.command(name="적용", description="지정 멤버 또는 서버 전체를 현재 규칙으로 다시 확인합니다")
    @app_commands.rename(member="대상")
    @app_commands.describe(member="생략하면 서버 전체. 자동 별명이 꺼져 있으면 관리 중이던 별명만 복원합니다.")
    async def apply_now(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        if member is not None:
            queued = self.enqueue(interaction.guild_id, member.id)
            if queued:
                await self._reply(interaction, f"{member.mention}의 별명 확인을 예약했습니다. `/별명 상태`에서 처리 상황을 확인하세요.")
            else:
                await self._reply(interaction, "대기열이 가득 차 기존 작업이 끝나면 서버를 다시 확인합니다. `/별명 상태`에서 진행 상황을 확인하세요.")
        else:
            self.request_scan(interaction.guild_id, fresh=True)
            await self._reply(interaction, "서버 전체 별명 확인을 예약했습니다. 순차 처리하며, 진행 상황과 실패 이유는 `/별명 상태`에서 확인하세요.")

    @app_commands.command(name="원래이름", description="관리 중인 멤버의 원래 이름을 변경합니다. 접두어의 이름 및 복원에 사용됩니다")
    @app_commands.rename(member="대상", name="이름", use_account_name="계정이름사용")
    @app_commands.describe(name="새 원래 서버 별명 (최대 32자)", use_account_name="True: 서버 별명 없이 Discord 표시 이름/사용자명을 사용")
    async def set_original(
        self, interaction: discord.Interaction, member: discord.Member,
        name: Optional[app_commands.Range[str, 1, 32]] = None, use_account_name: bool = False,
    ) -> None:
        if (name is None and not use_account_name) or (name is not None and use_account_name):
            raise ValueError("이름을 입력하거나 계정이름사용=True를 선택하세요. 둘을 동시에 선택할 수 없습니다.")
        original = None if use_account_name else _clean_text(name)
        await interaction.response.defer(ephemeral=True)
        def update(record: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
            if record is None:
                raise ValueError("현재 관리 중인 별명이 아닙니다. 규칙을 적용한 뒤 사용하세요.")
            record["original_nick"] = original
            return record
        async with self._member_lock((interaction.guild_id, member.id)):
            await self.store.mutate_member(interaction.guild_id, member.id, update)
        self.enqueue(interaction.guild_id, member.id)
        await self._reply(interaction, "원래 이름을 저장했습니다. 현재 규칙의 이름 부분과 추후 원래 별명 복원에 이 값을 사용합니다.")

    @app_commands.command(name="복구", description="조건 해제 후 중단된 별명 복구를 명시적으로 결정합니다")
    @app_commands.rename(member="대상", action="처리")
    @app_commands.choices(action=[
        app_commands.Choice(name="보관한 원래 별명으로 복원", value="restore"),
        app_commands.Choice(name="현재 별명 유지 · 보관 기록 정리", value="keep"),
    ])
    async def recover(self, interaction: discord.Interaction, member: discord.Member, action: str) -> None:
        if action not in {"restore", "keep"}:
            raise ValueError("복원 또는 현재 별명 유지를 선택하세요.")
        await interaction.response.defer(ephemeral=True)
        gid, uid = interaction.guild_id, member.id
        async with self._member_lock((gid, uid)):
            latest = await interaction.guild.fetch_member(uid)
            config = self.store.snapshot(gid, member_id=uid)
            if self._selected(config, latest) is not None:
                raise ValueError("아직 적용할 규칙이 있습니다. 역할 조건을 해제하거나 자동 별명을 끈 뒤 복구하세요.")
            record = config["members"].get(str(uid))
            if record is None:
                raise ValueError("이 멤버에게 보관된 원래 별명 기록이 없습니다.")
            if record.get("joined_at") and self._joined_at(latest) and record["joined_at"] != self._joined_at(latest):
                raise ValueError("이전 가입의 기록입니다. /별명 적용으로 현재 가입 상태를 먼저 확인하세요.")
            if action == "keep":
                await self._drop_record(gid, uid)
            else:
                def confirm(current: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
                    if current is not None:
                        current["restore_confirmed"] = True
                    return current
                await self.store.mutate_member(gid, uid, confirm)
                self.enqueue(gid, uid)
        await self._reply(interaction, "현재 별명을 유지하고 보관 기록을 정리했습니다." if action == "keep" else "보관한 원래 별명으로 복원을 예약했습니다. 결과는 /별명 상태에서 확인하세요.")

    @app_commands.command(name="상태", description="설정, 대기 작업, 최근 처리 결과와 오류를 확인합니다")
    async def status(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        config = self.store.snapshot(gid, include_members=False)
        counts = self._counters[gid]
        pending = sum(key[0] == gid for key in self._pending)
        active = int(self._active is not None and self._active[0] == gid)
        embed = discord.Embed(title="자동 별명 상태", color=discord.Color.teal())
        embed.description = f"자동 별명 **{'켜짐' if config['enabled'] else '꺼짐'}** · 등록 규칙 {len(config['rules'])}개\n원래 별명 보관 {self.store.member_count(gid)}명 · 대기 {pending}명 · 처리 중 {active}명"
        embed.add_field(name="최근 목록 검사", value=self._scan_info.get(gid, "아직 전체 검사를 실행하지 않았습니다."), inline=False)
        embed.add_field(name="이 코그를 시작한 뒤 처리 횟수", value=f"변경 {counts['changed']} · 복원 {counts['restored']} · 변경 불필요 {counts['unchanged']}\n수동 별명 유지 {counts['preserved']} · 권한/연결 제한 {counts['blocked']} · 오류 {counts['error']} · 확인 필요 {counts['uncertain']}", inline=False)
        me = interaction.guild.me
        embed.add_field(name="봇 별명 관리 권한", value="있음 (대상보다 봇 역할이 높아야 합니다)" if me and me.guild_permissions.manage_nicknames else "없음 — 봇 역할에 별명 관리 권한을 주세요.", inline=False)
        cadence = f"{self.reconcile_seconds / 3600:g}시간" if self.reconcile_seconds else "정기 검사 꺼짐"
        embed.add_field(name="사용량 절약", value=f"버전: {VERSION}\n안전 전체 검사: {cadence} · 대기열 상한: {self.queue_limit}\n목록 검사에서 불필요 개별 조회 생략: {counts['filtered']}회 · 역할/별명 변경은 이벤트 처리", inline=False)
        if self.store.fault:
            embed.add_field(name="저장 오류", value="설정 저장에 실패했습니다. 봇 로그, 파일 쓰기 권한, 디스크 공간을 확인하세요.", inline=False)
        if self._failures[gid]:
            embed.add_field(name="최근 제한/오류 (해결 후에도 최근 이력으로 남습니다)", value="\n".join(self._failures[gid])[:1024], inline=False)
        embed.set_footer(text="누적 횟수는 고유 인원수가 아닙니다. 실패는 /별명 적용 또는 다음 안전 검사로 다시 시도합니다.")
        await self._reply(interaction, embed=embed)

    @app_commands.command(name="도움말", description="자동 별명 명령 사용법과 규칙 선정 방식을 확인합니다")
    async def help_command(self, interaction: discord.Interaction) -> None:
        await self._reply(interaction,
            "**자동 별명 사용 순서**\n"
            "1. `/별명 규칙추가` — 내용과 역할1을 선택합니다. 역할2~5는 선택 사항입니다.\n"
            "2. `/별명 미리보기` — 대상에게 적용될 규칙과 변경 결과를 확인합니다.\n"
            "3. `/별명 사용` → 켜기 — 기존 멤버부터 적용하고 이후 역할 변경을 감지합니다.\n\n"
            "**형식**\n별명 전체 변경: `[ 운영진 ]` (기본)\n이름 앞에 붙이기: `[ 운영진 ] 민수`\n대괄호는 자동으로 붙으므로 내용에는 `운영진`만 입력하세요.\n\n"
            "**규칙**\n한 규칙의 역할은 **모두 충족(AND)**해야 합니다.\n높은 우선순위 → 많은 역할 조건 → 작은 규칙 번호 순서로 하나만 적용합니다.\n역할 A 또는 B에 같은 내용을 적용하려면 규칙을 두 개 만드세요.\n\n"
            "**관리**\n`규칙목록` · `규칙수정` · `규칙삭제` · `적용` · `상태`\n`원래이름` — 접두어의 이름 부분과 나중에 복원할 원래 이름 수정\n`사용` → 끄기 — 원래 별명 복원\n\n"
            "일치 규칙이 있는 동안 수동으로 바꾼 별명도 규칙에 맞게 재적용됩니다.\n봇 역할은 대상보다 높아야 하며 서버 소유자와 봇 계정은 변경하지 않습니다.\n"
            "절약판은 정기 안전 검사를 기본 6시간마다 실행합니다. 오류 해결 후 `/별명 적용`으로 즉시 재확인할 수 있습니다."
        )


async def setup(bot: commands.Bot) -> None:
    """기존 봇에서 await bot.load_extension('cogs.auto_nickname')로 불러옵니다."""
    guild_ids = configured_guild_ids()
    data_path = Path(os.environ.get("AUTO_NICK_DATA_PATH", str(DEFAULT_DATA_PATH))).expanduser().resolve()
    cog = AutoNickname(bot, guild_ids, data_path)
    try:
        await bot.add_cog(cog, guilds=[discord.Object(id=gid) for gid in sorted(guild_ids)])
    except BaseException:
        await cog.cog_unload()
        raise
