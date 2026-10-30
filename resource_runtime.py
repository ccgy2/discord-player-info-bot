"""Low-memory helpers. This module intentionally uses only the standard library."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import signal
import sys
import time

VERSION = "player-info-low-memory-20261030-2"
ROOT = Path(__file__).resolve().parent
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def configure_resource_env() -> None:
    # Set before importing numerical libraries, not after their thread pools exist.
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("MALLOC_ARENA_MAX", "2")


def message_cache_limit():
    """discord.py uses None (not integer zero) to disable its message cache."""
    value = env_int("BOT_MESSAGE_CACHE", 0, 0, 1000)
    return value or None


class HeavyWorkerLease:
    """One process/model owner. Releasing twice is harmless."""
    def __init__(self, gate, label, on_waiter):
        self.gate, self.label, self.on_waiter = gate, str(label), on_waiter
        self.released = False

    def release(self):
        if self.released:
            return
        self.released = True
        if self.gate.owner is self:
            self.gate.owner = None
            self.gate._lock.release()


class HeavyWorkerGate:
    """Serialize OCR and document workers; never kill an active inference.

    A waiter wakes an idle OCR owner so a keep-warm configuration cannot block
    document work indefinitely. asyncio.Lock keeps arrival ordering fair.
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        self.owner = None
        self.waiters = 0

    async def acquire(self, label, *, timeout=120.0, on_waiter=None):
        self.waiters += 1
        try:
            if self.owner is not None and self.owner.on_waiter is not None:
                self.owner.on_waiter()
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
        finally:
            self.waiters -= 1
        lease = HeavyWorkerLease(self, label, on_waiter)
        self.owner = lease
        return lease


def get_heavy_gate():
    # Owned by the running loop, not by a global dictionary that retains closed
    # test/reload loops and their task objects indefinitely.
    loop = asyncio.get_running_loop()
    gate = getattr(loop, "_player_info_heavy_gate", None)
    if gate is None:
        gate = HeavyWorkerGate()
        loop._player_info_heavy_gate = gate
    return gate


class TTLBoolCache(MutableMapping):
    """Small bounded cache; transient API failures no longer live forever."""
    def __init__(self, max_entries=1024, ttl=600.0, clock=time.monotonic):
        self.max_entries = max(1, int(max_entries))
        self.ttl = max(0.0, float(ttl))
        self._clock = clock
        self._items = OrderedDict()

    def _prune(self):
        now = self._clock()
        for key, (_, expires) in tuple(self._items.items()):
            if expires <= now:
                del self._items[key]

    def __getitem__(self, key):
        value, expires = self._items[key]
        if expires <= self._clock():
            del self._items[key]
            raise KeyError(key)
        self._items.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        self._items[key] = (bool(value), self._clock() + self.ttl)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def __delitem__(self, key):
        del self._items[key]

    def __iter__(self):
        self._prune()
        return iter(self._items)

    def __len__(self):
        self._prune()
        return len(self._items)


class WorkerError(RuntimeError):
    pass


class WorkerBusy(WorkerError):
    pass


ERROR_MESSAGES = {
    "too_large": "파일 또는 문서가 처리 한도를 넘었습니다. 파일을 나누어 올려주세요.",
    "invalid_file": "지원되는 정상 PDF/CSV/XLSX 파일인지 확인해주세요.",
    "empty_pdf": "PDF에서 텍스트를 추출하지 못했습니다. 스캔본에는 별도의 문자 인식이 필요합니다.",
    "missing_key": "서버에 GEMINI_API_KEY가 설정되지 않았습니다.",
    "missing_dependency": "처리에 필요한 패키지가 없습니다. 이번 Dockerfile로 다시 빌드해주세요.",
    "failed": "처리를 완료하지 못했습니다. 서버 로그의 오류 종류와 인증·모델 파일을 확인해주세요.",
}


async def _read_limited(stream, maximum):
    result = bytearray()
    while True:
        chunk = await stream.read(min(65536, maximum + 1 - len(result)))
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        if len(result) > maximum:
            raise WorkerError("작업 결과가 허용 크기를 넘었습니다.")


def _kill(proc):
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass


class WorkManager:
    """One PDF/Excel operation at a time, no unbounded wait queue or model cache."""
    def __init__(self):
        self._lock = asyncio.Lock()
        self._processes = set()
        self._closed = False
        self._gate_waiter = None
        self.active = None

    @asynccontextmanager
    async def slot(self, label):
        if self._closed:
            raise WorkerError("봇이 종료 중입니다.")
        if self._lock.locked():
            raise WorkerBusy("다른 PDF·엑셀 작업이 진행 중입니다. 완료된 뒤 다시 실행해주세요.")
        async with self._lock:
            self.active = str(label)
            try:
                yield self
            finally:
                self.active = None

    async def run(self, worker, payload, *, timeout=300):
        if not self._lock.locked() or self._closed:
            raise WorkerError("작업 실행 상태가 올바르지 않습니다.")
        if worker not in {"pdf_job", "excel_job"}:
            raise ValueError("Unknown worker")
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(raw) > MAX_REQUEST_BYTES:
            raise WorkerError("작업 요청이 너무 큽니다.")
        env = dict(os.environ)
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env[name] = "1"
        env.update(TOKENIZERS_PARALLELISM="false", PYTHONUNBUFFERED="1")
        proc = None
        tasks = []
        lease = None
        try:
            self._gate_waiter = asyncio.create_task(get_heavy_gate().acquire(worker))
            try:
                lease = await self._gate_waiter
            except asyncio.TimeoutError as exc:
                raise WorkerBusy("이미지 검사 작업이 진행 중입니다. 잠시 후 다시 실행해주세요.") from exc
            finally:
                self._gate_waiter = None
            if self._closed:
                raise WorkerError("봇이 종료 중입니다.")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "resource_workers." + worker,
                cwd=str(ROOT), env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=65536, start_new_session=(os.name == "posix"),
            )
            self._processes.add(proc)
            if self._closed:
                raise WorkerError("봇이 종료 중입니다.")

            async def send():
                try:
                    proc.stdin.write(raw)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

            tasks = [asyncio.create_task(send()),
                     asyncio.create_task(_read_limited(proc.stdout, MAX_RESPONSE_BYTES)),
                     asyncio.create_task(proc.wait())]
            _, output, code = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
            if code != 0:
                raise WorkerError("작업 프로세스가 종료되었습니다. 메모리 제한과 서버 로그를 확인해주세요.")
            try:
                result = json.loads(output)
            except (ValueError, UnicodeError) as exc:
                raise WorkerError("작업 결과를 읽을 수 없습니다.") from exc
            if not isinstance(result, dict):
                raise WorkerError("잘못된 작업 응답입니다.")
            if not result.get("ok"):
                code = result.get("error", "failed")
                # Worker sends only a class name, never raw exceptions or credentials.
                kind = str(result.get("kind", "unknown"))
                safe_kind = "".join(c for c in kind if c.isalnum() or c == "_")[:80]
                print(f"[절약 작업 오류] {worker}: {code if code in ERROR_MESSAGES else 'failed'} ({safe_kind})", flush=True)
                raise WorkerError(ERROR_MESSAGES.get(code, ERROR_MESSAGES["failed"]))
            return result
        except asyncio.TimeoutError as exc:
            raise WorkerError("처리 시간 제한을 넘겨 작업을 종료했습니다. 등록 작업은 일부 반영됐을 수 있으니 먼저 확인해주세요.") from exc
        except asyncio.CancelledError:
            if self._closed:
                raise WorkerError("봇이 종료 중입니다.") from None
            raise
        finally:
            try:
                if proc is not None:
                    _kill(proc)
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    # Drain any pipe remainder after killing, then reap the child.
                    await proc.communicate()
                    self._processes.discard(proc)
            finally:
                if lease is not None:
                    lease.release()

    async def close(self):
        self._closed = True
        if self._gate_waiter is not None and not self._gate_waiter.done():
            self._gate_waiter.cancel()
        processes = tuple(self._processes)
        for proc in processes:
            _kill(proc)
        # run() owns pipe readers and reaps each process; never read the same
        # pipe from close() while a command is still consuming it.
        for proc in processes:
            await proc.wait()


def get_manager(bot):
    manager = getattr(bot, "_resource_jobs", None)
    if manager is None:
        manager = WorkManager()
        bot._resource_jobs = manager
    return manager


def _rss(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def resource_snapshot():
    def number(path):
        try:
            return int(Path(path).read_text().strip())
        except (OSError, ValueError):
            return None
    children = []
    try:
        ids = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().split()
        children = [{"pid": int(pid), "rss_bytes": _rss(int(pid))} for pid in ids]
    except OSError:
        pass
    return {
        "version": VERSION, "pid": os.getpid(), "bot_rss_bytes": _rss(os.getpid()),
        "children": children,
        "container_bytes": number("/sys/fs/cgroup/memory.current"),
        "heavy_modules_in_bot": [name for name in ("torch", "sentence_transformers", "easyocr", "pandas", "numpy") if name in sys.modules],
    }


def snapshot_text(bot):
    snapshot = resource_snapshot()
    def mib(value):
        return "확인 불가" if value is None else f"{value / 1024**2:.1f} MiB"
    cog = bot.get_cog("AutoMod")
    ocr = getattr(cog, "ocr", None)
    state = getattr(ocr, "readiness", "미로드")
    manager = getattr(bot, "_resource_jobs", None)
    gate = get_heavy_gate()
    return "\n".join([
        f"절약 버전: {VERSION}",
        f"봇 프로세스 RAM: {mib(snapshot['bot_rss_bytes'])}",
        f"컨테이너 RAM(파일 캐시 등 포함): {mib(snapshot['container_bytes'])}",
        f"직접 자식 프로세스: {len(snapshot['children'])}개",
        f"자식 RSS 합계(공유 메모리 중복 가능): {mib(sum(c['rss_bytes'] or 0 for c in snapshot['children']))}",
        f"OCR 상태: {state} (standby는 절약 대기, 검사 비활성화가 아님)",
        f"PDF·엑셀 작업: {getattr(manager, 'active', None) or '없음'}",
        f"공유 작업 슬롯: {gate.owner.label if gate.owner else '없음'} / 대기 {gate.waiters}개",
        f"OCR 대기 유지: {getattr(ocr, 'idle_seconds', '미로드')}초 / 메시지 캐시: {message_cache_limit() or 0}개",
        f"봇에 상주 중인 무거운 모듈: {', '.join(snapshot['heavy_modules_in_bot']) or '없음'}",
        "이 값은 지금의 메모리 진단이며 Railway 누적 청구액이 아닙니다.",
    ])
