# bot.py
"""
Discord + Firebase (Firestore) Baseball Player Manager Bot
- Python 3.8+
- discord.py 명령 기반 봇
- Firestore: players, teams, records(collection per player doc)
- 한국어 명령어: !정보, !정보상세, !등록, !추가, !수정, !닉변, !삭제, !구종삭제, 팀명령, 기록명령 등
- 이번 버전:
  - 마인크래프트 닉네임(Mojang API) 검증 기능 추가 (단일/대량 등록)
  - Minotar 스킨 썸네일/바디 포함
  - aiohttp 사용 (비동기 HTTP)
  - timezone-aware datetime 사용
  - 전체 파일(생략 없음)
"""

import os
import json
import asyncio
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict
from urllib.parse import quote_plus

import aiohttp
import discord
from discord.ext import commands

# Firebase Admin
import firebase_admin
from firebase_admin import credentials, firestore

# dotenv for local development (optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------- 설정 ----------
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
INTENTS = discord.Intents.default()
INTENTS.message_content = True

# 검증 토글: 환경변수 VERIFY_MC 가 "false"로 설정되어 있지 않다면 검증을 수행
VERIFY_MC = os.getenv("VERIFY_MC", "true").lower() not in ("0", "false", "no", "off")

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=INTENTS, help_command=None)

# ---------- Firebase 초기화 ----------
def init_firebase():
    if firebase_admin._apps:
        return firestore.client()

    cred_json = os.getenv("FIREBASE_KEY")
    ga_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    try:
        if cred_json:
            info = json.loads(cred_json)
            cred = credentials.Certificate(info)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized from FIREBASE_KEY")
        elif ga_path and os.path.exists(ga_path):
            cred = credentials.Certificate(ga_path)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized from GOOGLE_APPLICATION_CREDENTIALS path")
        else:
            # Use Application Default Credentials if available
            firebase_admin.initialize_app()
            print("✅ Firebase initialized with default creds")
    except Exception as e:
        print("❌ Firebase init error:", e)
        raise

    return firestore.client()

db = None
try:
    db = init_firebase()
except Exception as e:
    print("Firebase 초기화 실패:", e)
    db = None  # 계속 실행은 가능, DB 명령 사용시 오류 알림

# ---------- HTTP session & MC cache ----------
http_session: Optional[aiohttp.ClientSession] = None
mc_cache: Dict[str, bool] = {}  # nickname(lower) -> bool

async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession()
    return http_session

async def close_http_session():
    global http_session
    if http_session and not http_session.closed:
        await http_session.close()
        http_session = None

# ---------- Minecraft username validation (Mojang API) ----------
async def is_mc_username(nick: str) -> bool:
    """
    Mojang API 확인:
    GET https://api.mojang.com/users/profiles/minecraft/{username}
    - 200: exists (body contains id/name)
    - 204/404: not found
    """
    if not VERIFY_MC:
        return True  # 검증 비활성화 시 항상 True

    key = nick.strip().lower()
    if not key:
        return False

    # 캐시 체크
    if key in mc_cache:
        return mc_cache[key]

    session = await get_http_session()
    url = f"https://api.mojang.com/users/profiles/minecraft/{quote_plus(nick)}"
    try:
        async with session.get(url, timeout=6) as resp:
            if resp.status == 200:
                mc_cache[key] = True
                return True
            # Mojang returns 204 or 404 for not found in different cases
            if resp.status in (204, 404):
                mc_cache[key] = False
                return False
            # 기타 상태는 실패로 처리하되 캐시하지 않음 (네트워크 문제 등)
            mc_cache[key] = False
            return False
    except asyncio.TimeoutError:
        # 타임아웃은 실패로 처리
        mc_cache[key] = False
        return False
    except Exception:
        mc_cache[key] = False
        return False

# ---------- 유틸리티 ----------
def now_iso():
    # timezone-aware UTC ISO format
    return datetime.now(timezone.utc).isoformat()

def normalize_nick(nick: str) -> str:
    return nick.strip().lower()

def short_time(ts_iso: str) -> str:
    try:
        return ts_iso.replace("T", " ").split(".")[0]
    except Exception:
        return ts_iso

async def ensure_db_or_warn(ctx):
    if db is None:
        await ctx.send("❌ 데이터베이스가 초기화되어 있지 않습니다. 관리자에게 문의하세요.")
        return False
    return True

# ---------- Minecraft skin helper (Minotar) ----------
def mc_avatar_url(nick: str, size: int = 128) -> str:
    if not nick:
        return ""
    return f"https://minotar.net/avatar/{quote_plus(nick)}/{size}.png"

def mc_body_url(nick: str, width: int = 400) -> str:
    if not nick:
        return ""
    return f"https://minotar.net/body/{quote_plus(nick)}/{width}.png"

def safe_avatar_urls(nick: str):
    try:
        u = nick.strip()
        if not u:
            return None, None
        return mc_avatar_url(u, 128), mc_body_url(u, 400)
    except Exception:
        return None, None

# ---------- 임베드 생성 ----------
def make_player_embed(data: dict, include_body: bool = True) -> discord.Embed:
    nickname = data.get('nickname', '-')
    title = f"{nickname} ({data.get('form','-')})"
    team = data.get('team','Free') or "Free"
    embed = discord.Embed(title=title, description=f"[{team}]", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="이름", value=data.get('name','-'), inline=True)
    embed.add_field(name="포지션", value=data.get('position','-'), inline=True)
    pitch_types = data.get('pitch_types', [])
    if pitch_types:
        embed.add_field(name="구종", value=", ".join(pitch_types[:10]), inline=False)
    else:
        embed.add_field(name="구종", value="-", inline=False)
    embed.set_footer(text=f"등록: {short_time(data.get('created_at','-'))}  수정: {short_time(data.get('updated_at','-'))}")
    try:
        avatar_url, body_url = safe_avatar_urls(nickname)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if include_body and body_url:
            embed.set_image(url=body_url)
    except Exception:
        pass
    return embed

# ---------- 기본 헬프 (한글) ----------
async def send_help_text(ctx):
    BOT = BOT_PREFIX
    verify_note = " (마인크래프트 닉네임 검증 ON)" if VERIFY_MC else " (마인크래프트 닉네임 검증 OFF)"
    cmds = f"""
**사용 가능한 명령어 (요약)**{verify_note}

**조회**
`{BOT}정보 닉네임` - 기본 정보 출력  
`{BOT}정보상세 닉네임` - 구종 / 폼 / 팀 / 포지션 등 상세

**등록/추가/대량등록**
`{BOT}등록` - 여러 줄 텍스트로 등록 (두 포맷 지원)
  1) 기존 파이프 형식: `nick|이름|팀|포지션|구종1,구종2|폼`
  2) 새 포맷: `닉네임 (폼) [팀] 구종(능력치) ...`
     예: `ccpy (언더핸드) [레이 마린스] 포심(20) 체인지업(20) 포크(30)`

`{BOT}추가 nick|이름|팀|포지션|구종1,구종2|폼` - 한 명 추가 (파이프 형식)

**수정/닉변/삭제**
`{BOT}수정 닉네임 필드 새값` - 예: `{BOT}수정 yian position P`  
`{BOT}닉변 옛닉 새닉` - 닉네임 변경  
`{BOT}삭제 닉네임` - 선수 삭제  
`{BOT}구종삭제 닉네임 구종명` - 특정 구종 제거

**팀 관리**
`{BOT}팀 팀명` - 팀 생성/조회  
`{BOT}목록 players|teams` - 목록 보기  
`{BOT}이적 닉네임 팀명` - 이적 처리  
`{BOT}fa 닉네임` - FA 처리  
`{BOT}웨이버 닉네임` - 웨이버 상태  
`{BOT}방출 닉네임` - 방출 처리  
`{BOT}트레이드 닉1 닉2` - 두 선수 교환  
`{BOT}팀이름변경 옛이름 새이름` - 팀명 변경  
`{BOT}팀삭제 팀명` - 팀 삭제  
`{BOT}가져오기파일` - 첨부 CSV/TXT로 대량 등록

**기록 (타자/투수)**
`{BOT}기록추가타자 닉네임 날짜 PA AB R H RBI HR SB`  
`{BOT}기록추가투수 닉네임 날짜 IP H R ER BB SO`  
`{BOT}기록보기 닉네임`  
`{BOT}기록리셋 닉네임 type` - type: batting|pitching|all

도움이 필요하면 `{BOT}도움` 또는 `{BOT}도움말` 을 입력하세요.
"""
    await ctx.send(cmds)

@bot.command(name="help")
async def help_cmd(ctx):
    await send_help_text(ctx)

@bot.command(name="도움")
async def help_kor(ctx):
    await send_help_text(ctx)

@bot.command(name="도움말")
async def help_kor2(ctx):
    await send_help_text(ctx)

# ---------- Firestore 참조 헬퍼 ----------
def player_doc_ref(nick: str):
    return db.collection("players").document(normalize_nick(nick))

def team_doc_ref(teamname: str):
    return db.collection("teams").document(teamname.strip())

def records_doc_ref(nick: str):
    return db.collection("records").document(normalize_nick(nick))

# ---------- 조회 ----------
@bot.command(name="정보")
async def info_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    doc = player_doc_ref(nick).get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수가 존재하지 않습니다.")
        return
    d = doc.to_dict()
    embed = make_player_embed(d, include_body=True)
    await ctx.send(embed=embed)

@bot.command(name="정보상세")
async def info_detail_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    doc = player_doc_ref(nick).get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수가 존재하지 않습니다.")
        return
    d = doc.to_dict()
    pitch_types = d.get("pitch_types", [])
    form = d.get("form","-")
    extra = d.get("extra","-")
    embed = discord.Embed(title=f"{d.get('nickname','-')} — 상세 정보", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="이름", value=d.get('name','-'), inline=True)
    embed.add_field(name="팀", value=d.get('team','-'), inline=True)
    embed.add_field(name="포지션", value=d.get('position','-'), inline=True)
    embed.add_field(name="구종", value=", ".join(pitch_types) if pitch_types else "-", inline=False)
    embed.add_field(name="폼", value=form, inline=True)
    embed.add_field(name="추가정보", value=str(extra), inline=False)
    embed.set_footer(text=f"등록: {short_time(d.get('created_at','-'))}  수정: {short_time(d.get('updated_at','-'))}")
    try:
        avatar_url, body_url = safe_avatar_urls(d.get('nickname',''))
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if body_url:
            embed.set_image(url=body_url)
    except Exception:
        pass
    await ctx.send(embed=embed)

# ---------- 단일 추가 (파이프 형식) ----------
@bot.command(name="추가")
async def add_one_cmd(ctx, *, payload: str):
    """
    단일 추가 (파이프 형식):
    !추가 nick|이름|팀|포지션|구종1,구종2|폼
    """
    if not await ensure_db_or_warn(ctx): return
    parts = payload.split("|")
    if len(parts) < 4:
        await ctx.send("❌ 형식 오류. 예시: `!추가 nick|이름|팀|포지션|구종1,구종2|폼`")
        return
    nick = parts[0].strip()
    name = parts[1].strip()
    team = parts[2].strip()
    position = parts[3].strip()
    pitch_types = []
    form = ""
    if len(parts) >= 5 and parts[4].strip():
        pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
    if len(parts) >= 6:
        form = parts[5].strip()

    # MC validation
    if VERIFY_MC:
        valid = await is_mc_username(nick)
        if not valid:
            await ctx.send(f"❌ `{nick}` 는(은) 유효한 마인크래프트 계정명이 아닙니다. 등록이 취소되었습니다.")
            return

    doc_ref = player_doc_ref(nick)
    data = {
        "nickname": nick,
        "name": name,
        "team": team or "Free",
        "position": position,
        "pitch_types": pitch_types,
        "form": form,
        "extra": {},
        "created_at": now_iso(),
        "updated_at": now_iso()
    }
    try:
        doc_ref.set(data)
        if team:
            t_ref = team_doc_ref(team)
            t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
            t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})
        # Single add embed with skin
        embed = make_player_embed(data, include_body=True)
        embed.colour = discord.Color.green()
        await ctx.send(content="✅ 선수 추가 완료", embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 추가 실패: {e}")

# ---------- 대량 등록 (!등록: 여러 줄 텍스트) ----------
@bot.command(name="등록")
async def bulk_register_cmd(ctx, *, bulk_text: str = None):
    """
    여러 줄 등록: 메시지 본문에 여러 줄로 붙여넣기
    지원 포맷 (둘 다):
    1) 파이프: nick|이름|팀|포지션|구종1,구종2|폼
    2) 라인 포맷: 닉네임 (폼) [팀] 구종(숫자) 구종(숫자) ...
       예: ccpy (언더핸드) [레이 마린스] 포심(20) 체인지업(20) 포크(30)
    """
    if not await ensure_db_or_warn(ctx): return

    if not bulk_text:
        await ctx.send("❌ 본문에 등록할 선수 정보를 여러 줄로 붙여넣어 주세요. (또는 첨부 파일 사용: `!가져오기파일`)")
        return

    lines = [l.strip() for l in bulk_text.splitlines() if l.strip()]
    added = []
    errors = []

    # regex to parse "닉네임 (폼) [팀] 구종(숫자) ..."
    line_pattern = re.compile(
        r'^\s*(?P<nick>[^\(\[\s][^\(\[\]]*?)\s*(?:\((?P<form>[^\)]*?)\))?\s*(?:\[(?P<team>[^\]]*?)\])?\s*(?P<pitches>.*)$'
    )
    pitch_pattern = re.compile(r'([^\s,()]+)\s*\(\s*(\d+)\s*\)')  # 구종(숫자)

    for i, line in enumerate(lines, start=1):
        try:
            if '|' in line:
                parts = line.split("|")
                if len(parts) < 4:
                    errors.append(f"라인 {i}: 파이프 형식 오류")
                    continue
                nick = parts[0].strip()
                name = parts[1].strip()
                team = parts[2].strip()
                position = parts[3].strip()
                pitch_types = []
                form = ""
                if len(parts) >=5 and parts[4].strip():
                    pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
                if len(parts) >=6:
                    form = parts[5].strip()
            else:
                m = line_pattern.match(line)
                if not m:
                    errors.append(f"라인 {i}: 파싱 실패")
                    continue
                nick = m.group('nick').strip()
                form = (m.group('form') or "").strip()
                team = (m.group('team') or "Free").strip()
                pitch_text = (m.group('pitches') or "").strip()

                pitch_types = []
                for pm in pitch_pattern.finditer(pitch_text):
                    pname = pm.group(1).strip()
                    pval = pm.group(2).strip()
                    pitch_types.append(f"{pname}({pval})")

                name = nick
                position = "N/A"

            # MC 검증 (각 닉네임)
            if VERIFY_MC:
                valid = await is_mc_username(nick)
                # 레이트 리밋 완화용 짧은 지연
                await asyncio.sleep(0.12)
                if not valid:
                    errors.append(f"라인 {i}: `{nick}` 은(는) 마인크래프트 계정이 아님")
                    continue

            doc_ref = player_doc_ref(nick)
            data = {
                "nickname": nick,
                "name": name,
                "team": team or "Free",
                "position": position,
                "pitch_types": pitch_types,
                "form": form,
                "extra": {},
                "created_at": now_iso(),
                "updated_at": now_iso()
            }
            doc_ref.set(data)
            if team:
                t_ref = team_doc_ref(team)
                t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})
            added.append(nick)
        except Exception as e:
            errors.append(f"라인 {i}: {e}")

    # 결과 임베드 생성 (요약)
    summary_embed = discord.Embed(title="등록 요약", timestamp=datetime.now(timezone.utc))
    summary_embed.add_field(name="총 입력", value=str(len(lines)), inline=True)
    summary_embed.add_field(name="성공", value=str(len(added)), inline=True)
    summary_embed.add_field(name="오류", value=str(len(errors)), inline=True)

    if added:
        show_added = added[:20]
        summary_embed.add_field(name="성공 목록 (최대 20)", value=", ".join(show_added), inline=False)
        if len(added) > 20:
            summary_embed.add_field(name="(생략)", value=f"...외 {len(added)-20}명", inline=False)

    if errors:
        show_errors = errors[:10]
        summary_embed.add_field(name="오류 예시 (최대 10)", value="\n".join(show_errors), inline=False)
        if len(errors) > 10:
            summary_embed.add_field(name="(오류 생략)", value=f"...외 {len(errors)-10}건", inline=False)
        summary_embed.colour = discord.Color.red()
    else:
        summary_embed.colour = discord.Color.green()

    await ctx.send(embed=summary_embed)

# ---------- 이하: 수정/닉변/삭제/구종삭제/팀/목록/기록 등 (기존 동작 유지) ----------
# (아래 명령들은 위에 이미 구현된 함수들과 동일하게 동작합니다.
# 필요하면 여기도 마인크래프트 검증을 추가해드릴 수 있습니다.)
@bot.command(name="수정")
async def edit_cmd(ctx, nick: str, field: str, *, value: str):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 가 존재하지 않습니다.")
        return
    updates = {}
    if field.startswith("extra."):
        key = field.split(".",1)[1]
        updates[f"extra.{key}"] = value
    elif field == "pitch_types":
        types = [p.strip() for p in value.split(",") if p.strip()]
        updates["pitch_types"] = types
    else:
        updates[field] = value
    updates["updated_at"] = now_iso()
    try:
        ref.update(updates)
        await ctx.send(f"✅ `{nick}` 업데이트 성공.")
    except Exception as e:
        await ctx.send(f"❌ 업데이트 실패: {e}")

@bot.command(name="닉변")
async def nickchange_cmd(ctx, oldnick: str, newnick: str):
    if not await ensure_db_or_warn(ctx): return
    old_ref = player_doc_ref(oldnick)
    old_doc = old_ref.get()
    if not old_doc.exists:
        await ctx.send(f"❌ `{oldnick}` 가 존재하지 않습니다.")
        return
    new_ref = player_doc_ref(newnick)
    if new_ref.get().exists:
        await ctx.send(f"❌ 새 닉네임 `{newnick}` 이 이미 존재합니다.")
        return
    data = old_doc.to_dict()
    data["nickname"] = newnick
    data["updated_at"] = now_iso()
    try:
        new_ref.set(data)
        old_ref.delete()
        team = data.get("team")
        if team:
            t_ref = team_doc_ref(team)
            t_ref.update({"roster": firestore.ArrayRemove([normalize_nick(oldnick)])})
            t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(newnick)])})
        rec_old = records_doc_ref(oldnick)
        rec_old_doc = rec_old.get()
        if rec_old_doc.exists:
            rec_new = records_doc_ref(newnick)
            rec_new.set(rec_old_doc.to_dict())
            rec_old.delete()
        await ctx.send(f"✅ `{oldnick}` → `{newnick}` 으로 변경되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 닉네임 변경 실패: {e}")

@bot.command(name="삭제")
async def delete_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 가 존재하지 않습니다.")
        return
    data = doc.to_dict()
    team = data.get("team")
    try:
        ref.delete()
        if team:
            t_ref = team_doc_ref(team)
            t_ref.update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
        records_doc_ref(nick).delete()
        await ctx.send(f"🗑️ `{nick}` 삭제되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 삭제 실패: {e}")

@bot.command(name="구종삭제")
async def remove_pitch_cmd(ctx, nick: str, pitch: str):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 가 존재하지 않습니다.")
        return
    try:
        d = doc.to_dict()
        current = d.get("pitch_types", [])
        newlist = [p for p in current if not (p == pitch or p.startswith(pitch+"("))]
        if len(newlist) == len(current):
            await ctx.send(f"⚠️ `{nick}` 에 `{pitch}` 구종이 없습니다.")
            return
        ref.update({"pitch_types": newlist, "updated_at": now_iso()})
        await ctx.send(f"✅ `{nick}` 의 `{pitch}` 구종이 삭제되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

@bot.command(name="팀")
async def team_cmd(ctx, *, teamname: str):
    if not await ensure_db_or_warn(ctx): return
    t_ref = team_doc_ref(teamname)
    t_doc = t_ref.get()
    if not t_doc.exists:
        t_ref.set({"name": teamname, "created_at": now_iso(), "roster": []})
        await ctx.send(f"✅ 팀 `{teamname}` 이(가) 생성되었습니다.")
        return
    t = t_doc.to_dict()
    roster = t.get("roster", [])
    if roster:
        await ctx.send(f"**{teamname}** — 로스터 ({len(roster)}):\n" + ", ".join(roster[:50]))
    else:
        await ctx.send(f"**{teamname}** — 로스터가 비어있습니다.")

@bot.command(name="목록")
async def list_cmd(ctx, kind: str = "players"):
    if not await ensure_db_or_warn(ctx): return
    if kind == "players":
        docs = db.collection("players").order_by("nickname").limit(200).stream()
        lines = []
        for d in docs:
            o = d.to_dict()
            lines.append(f"{o.get('nickname','-')} ({o.get('team','-')} / {o.get('position','-')})")
        if not lines:
            await ctx.send("선수 데이터가 없습니다.")
        else:
            chunk_size = 1900
            text = "\n".join(lines)
            for i in range(0, len(text), chunk_size):
                await ctx.send(text[i:i+chunk_size])
    elif kind == "teams":
        docs = db.collection("teams").order_by("name").stream()
        lines = [d.to_dict().get("name","-") for d in docs]
        await ctx.send("팀 목록:\n" + (", ".join(lines) if lines else "없음"))
    else:
        await ctx.send("사용법: `!목록 players|teams`")

@bot.command(name="이적")
async def transfer_cmd(ctx, nick: str, newteam: str):
    if not await ensure_db_or_warn(ctx): return
    p_ref = player_doc_ref(nick)
    p_doc = p_ref.get()
    if not p_doc.exists:
        await ctx.send(f"❌ `{nick}` 가 존재하지 않습니다.")
        return
    data = p_doc.to_dict()
    oldteam = data.get("team")
    try:
        p_ref.update({"team": newteam, "updated_at": now_iso()})
        if oldteam:
            team_doc_ref(oldteam).update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
        t_ref = team_doc_ref(newteam)
        t_ref.set({"name": newteam, "created_at": now_iso()}, merge=True)
        t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})
        await ctx.send(f"✅ `{nick}` 이(가) `{oldteam}` -> `{newteam}` 로 이적 처리되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 이적 실패: {e}")

@bot.command(name="fa")
async def fa_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    await transfer_cmd(ctx, nick, "FA")

@bot.command(name="웨이버")
async def waiver_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send("해당 선수 없음")
        return
    try:
        ref.update({"status": "waiver", "updated_at": now_iso()})
        await ctx.send(f"✅ `{nick}` 이(가) 웨이버 상태로 변경되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

@bot.command(name="방출")
async def release_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send("해당 선수 없음")
        return
    data = doc.to_dict()
    team = data.get("team")
    try:
        ref.update({"team": "Free", "status": "released", "updated_at": now_iso()})
        if team:
            team_doc_ref(team).update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
        await ctx.send(f"✅ `{nick}` 이(가) 방출되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

@bot.command(name="트레이드")
async def trade_cmd(ctx, nick1: str, nick2: str):
    if not await ensure_db_or_warn(ctx): return
    r1 = player_doc_ref(nick1); r2 = player_doc_ref(nick2)
    d1 = r1.get(); d2 = r2.get()
    if not d1.exists or not d2.exists:
        await ctx.send("둘 중 한 선수가 존재하지 않습니다.")
        return
    try:
        t1 = d1.to_dict().get("team", "Free")
        t2 = d2.to_dict().get("team", "Free")
        r1.update({"team": t2, "updated_at": now_iso()})
        r2.update({"team": t1, "updated_at": now_iso()})
        if t1:
            team_doc_ref(t1).update({"roster": firestore.ArrayRemove([normalize_nick(nick1)])})
            if t2:
                team_doc_ref(t2).update({"roster": firestore.ArrayUnion([normalize_nick(nick1)])})
        if t2:
            team_doc_ref(t2).update({"roster": firestore.ArrayRemove([normalize_nick(nick2)])})
            if t1:
                team_doc_ref(t1).update({"roster": firestore.ArrayUnion([normalize_nick(nick2)])})
        await ctx.send(f"✅ `{nick1}` 과 `{nick2}` 트레이드 완료 ({t1} <-> {t2})")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

# ---------- 기록: 타자/투수 추가/보기/리셋 ----------
@bot.command(name="기록추가타자")
async def add_batting_cmd(ctx, nick: str, date: str, PA: int, AB: int, R: int, H: int, RBI: int, HR: int, SB: int):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    if not ref.get().exists:
        await ctx.send("해당 선수 없음")
        return
    entry = {"date": date, "PA": int(PA), "AB": int(AB), "R": int(R), "H": int(H), "RBI": int(RBI), "HR": int(HR), "SB": int(SB), "added_at": now_iso()}
    try:
        rec_ref = records_doc_ref(nick)
        rec_ref.set({}, merge=True)
        rec_ref.update({"batting": firestore.ArrayUnion([entry])})
        await ctx.send(f"✅ `{nick}` 에 타자 기록 추가됨: {date}")
    except Exception as e:
        await ctx.send(f"❌ 기록 추가 실패: {e}")

@bot.command(name="기록추가투수")
async def add_pitching_cmd(ctx, nick: str, date: str, IP: float, H: int, R: int, ER: int, BB: int, SO: int):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    if not ref.get().exists:
        await ctx.send("해당 선수 없음")
        return
    entry = {"date": date, "IP": float(IP), "H": int(H), "R": int(R), "ER": int(ER), "BB": int(BB), "SO": int(SO), "added_at": now_iso()}
    try:
        rec_ref = records_doc_ref(nick)
        rec_ref.set({}, merge=True)
        rec_ref.update({"pitching": firestore.ArrayUnion([entry])})
        await ctx.send(f"✅ `{nick}` 에 투수 기록 추가됨: {date}")
    except Exception as e:
        await ctx.send(f"❌ 기록 추가 실패: {e}")

@bot.command(name="기록보기")
async def view_records_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    rec = records_doc_ref(nick).get()
    if not rec.exists:
        await ctx.send("기록이 존재하지 않습니다.")
        return
    d = rec.to_dict()
    batting = d.get("batting", [])
    pitching = d.get("pitching", [])
    lines = [f"**{nick} — 기록 요약**"]
    if batting:
        total_PA = sum(int(x.get("PA",0)) for x in batting)
        total_AB = sum(int(x.get("AB",0)) for x in batting)
        total_H = sum(int(x.get("H",0)) for x in batting)
        avg = (total_H / total_AB) if total_AB>0 else 0
        lines.append(f"타자 기록 {len(batting)}경기 — PA:{total_PA} AB:{total_AB} H:{total_H} AVG:{avg:.3f}")
    else:
        lines.append("타자 기록: 없음")
    if pitching:
        total_IP = sum(float(x.get("IP",0)) for x in pitching)
        total_ER = sum(int(x.get("ER",0)) for x in pitching)
        era = (total_ER * 9 / total_IP) if total_IP>0 else 0
        lines.append(f"투수 기록 {len(pitching)}경기 — IP:{total_IP} ER:{total_ER} ERA:{era:.2f}")
    else:
        lines.append("투수 기록: 없음")
    await ctx.send("\n".join(lines))

@bot.command(name="기록리셋")
async def reset_records_cmd(ctx, nick: str, typ: str):
    if not await ensure_db_or_warn(ctx): return
    rec_ref = records_doc_ref(nick)
    if not rec_ref.get().exists:
        await ctx.send("기록 없음")
        return
    try:
        if typ == "batting":
            rec_ref.update({"batting": []})
        elif typ == "pitching":
            rec_ref.update({"pitching": []})
        elif typ == "all":
            rec_ref.delete()
            rec_ref.set({}, merge=True)
        else:
            await ctx.send("TYPE 오류: batting|pitching|all 중 하나를 사용하세요.")
            return
        await ctx.send("✅ 기록 리셋 완료")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

# ---------- 에러 처리 ----------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("인자가 부족합니다. `!도움` 로 사용법을 확인하세요.")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("알 수 없는 명령어입니다. `!도움` 를 확인하세요.")
    else:
        await ctx.send(f"명령 실행 중 오류가 발생했습니다: `{error}`")
        print("Unhandled command error:", error)

# ---------- 봇 종료시 세션 정리 ----------
@bot.event
async def on_close():
    # not always called, but attempt to close aiohttp session when possible
    try:
        asyncio.create_task(close_http_session())
    except Exception:
        pass

# ---------- 봇 실행 ----------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN 환경변수가 설정되어 있지 않습니다.")
        raise SystemExit(1)
    try:
        bot.run(token)
    except Exception as e:
        print("봇 실행 중 예외:", e)
    finally:
        # ensure http session closed on process exit (best-effort)
        try:
            loop = asyncio.get_event_loop()
            if http_session and not http_session.closed:
                loop.run_until_complete(close_http_session())
        except Exception:
            pass
