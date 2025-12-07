# bot.py
"""
Discord + Firebase (Firestore) Baseball Player Manager Bot
- Python 3.8+
- discord.py based commands
- Firestore collections: players, teams, records, aliases
- Features included:
  * 블록/파이프 기반 등록/추가/수정/파일가져오기
  * Mojang username 검사(옵션)
  * Minotar 스킨(avatar, body) 임베드 포함
  * 닉변(aliases) 지원
  * !추가: 기존 선수에 구종 append(중복 제거, 기본값 부여)
  * 구종 숫자 없을 경우 기본값 자동 부여 (DEFAULT_PITCH_POWER)
  * 임베드 시 등록자 아바타, 깔끔한 필드 재배치, 팀 기반 색상 매핑
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

# firebase admin
import firebase_admin
from firebase_admin import credentials, firestore

# dotenv (개발 환경에서 사용)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------- 설정 ----------
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
INTENTS = discord.Intents.default()
INTENTS.message_content = True

# 마인크래프트 닉네임 검증을 끄고 싶으면 VERIFY_MC=false 환경변수 설정
VERIFY_MC = os.getenv("VERIFY_MC", "true").lower() not in ("0", "false", "no", "off")

# 구종에 숫자 없을때 기본 수치
DEFAULT_PITCH_POWER = int(os.getenv("DEFAULT_PITCH_POWER", "20"))

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=INTENTS, help_command=None)

# ---------- Firebase 초기화 ----------
def init_firebase():
    # 이미 초기화 되어 있으면 기존 client 반환
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
    db = None

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

# ---------- 유틸리티 ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def short_time(ts_iso: str) -> str:
    try:
        return ts_iso.replace("T", " ").split(".")[0]
    except Exception:
        return ts_iso

def normalize_nick(nick: str) -> str:
    return nick.strip().lower()

def normalize_team_name(team: str) -> str:
    if not team:
        return "Free"
    return " ".join(team.strip().split())

async def ensure_db_or_warn(ctx):
    if db is None:
        await ctx.send("❌ 데이터베이스가 초기화되어 있지 않습니다. 관리자에게 문의하세요.")
        return False
    return True

# ---------- Alias (닉변 이전 이름 -> 현재 닉네임) ----------
def resolve_nick(nick: str) -> str:
    """
    닉네임 또는 이전 닉네임(aliases)에 대해 canonical(정규화된) 닉 반환.
    """
    try:
        norm = normalize_nick(nick)
        alias_ref = db.collection("aliases").document(norm)
        doc = alias_ref.get()
        if doc.exists:
            d = doc.to_dict()
            cur = d.get("current")
            if cur:
                return normalize_nick(cur)
        return norm
    except Exception:
        return normalize_nick(nick)

# ---------- Firestore 참조 헬퍼 ----------
def player_doc_ref(nick: str):
    canonical = resolve_nick(nick)
    return db.collection("players").document(canonical)

def team_doc_ref(teamname: str):
    return db.collection("teams").document(normalize_team_name(teamname))

def records_doc_ref(nick: str):
    canonical = resolve_nick(nick)
    return db.collection("records").document(canonical)

# ---------- Minecraft username validation (Mojang API) ----------
async def is_mc_username(nick: str) -> bool:
    if not VERIFY_MC:
        return True
    key = nick.strip().lower()
    if not key:
        return False
    if key in mc_cache:
        return mc_cache[key]
    session = await get_http_session()
    url = f"https://api.mojang.com/users/profiles/minecraft/{quote_plus(nick)}"
    try:
        async with session.get(url, timeout=6) as resp:
            if resp.status == 200:
                mc_cache[key] = True
                return True
            if resp.status in (204, 404):
                mc_cache[key] = False
                return False
            mc_cache[key] = False
            return False
    except asyncio.TimeoutError:
        mc_cache[key] = False
        return False
    except Exception:
        mc_cache[key] = False
        return False

# ---------- Minotar skin helper ----------
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

# ---------- 구종(파워) 처리 유틸 ----------
def pitch_base_name(pitch: str) -> str:
    """ '포심(40)' -> '포심', '포심' -> '포심' """
    m = re.match(r'^([^\(]+)', pitch)
    return m.group(1).strip() if m else pitch.strip()

def pitch_has_power(pitch: str) -> bool:
    return bool(re.search(r'\(\s*\d+\s*\)$', pitch))

def normalize_pitch_token(tok: str) -> str:
    """
    입력 토큰을 정규화:
    - 쉼표 제거
    - 숫자 없는 경우 기본값 부여: 포심 -> 포심(20)
    - 이미 숫자 있으면 공백 제거 후 그대로 반환
    """
    if not tok:
        return ""
    t = tok.strip().rstrip(",")
    if pitch_has_power(t):
        return re.sub(r'\s+', '', t)
    # 숫자 없음 -> 기본값 추가
    base = pitch_base_name(t)
    return f"{base}({DEFAULT_PITCH_POWER})"

# ---------- 임베드 컬러 결정 (팀 기반 또는 기본 매핑) ----------
def color_for_team(team: str) -> discord.Color:
    if not team:
        return discord.Color.dark_grey()
    # 간단한 해시로 0xRRGGBB 생성 (밝기 조절)
    h = abs(hash(team)) & 0xFFFFFF
    # discord.Color expects an integer 0..0xFFFFFF
    return discord.Color(h)

# ---------- 임베드 도우미 개선 ----------
def format_registrar_field_and_avatar(created_by: dict) -> (str, Optional[str]):
    if not created_by:
        return "-", None
    uid = created_by.get("id", "-")
    display = created_by.get("display_name") or created_by.get("name") or "-"
    discr = created_by.get("discriminator")
    avatar_url = created_by.get("avatar_url")
    if discr:
        name_repr = f"{display} ({created_by.get('name','')}{('#'+discr)})"
    else:
        name_repr = f"{display}"
    return f"{name_repr}\nID: {uid}", avatar_url

def make_player_embed(data: dict, context: Optional[dict] = None) -> discord.Embed:
    """
    data: player document dict
    context: optional dict, e.g., {"action":"create"/"append"/"edit"} to choose color
    """
    nickname = data.get('nickname', '-')
    team = data.get('team','Free') or "Free"
    form = data.get('form','-') or '-'
    position = data.get('position','-') or '-'
    pitch_types = data.get('pitch_types', []) or []
    # nice human readable pitches: each on new line (limit)
    if pitch_types:
        pitches_display = "\n".join([f"- {p}" for p in (pitch_types[:20])])
    else:
        pitches_display = "-"

    color = color_for_team(team)

    title = f"{nickname}"
    embed = discord.Embed(title=title, description=f"[{team}] {form}", color=color, timestamp=datetime.now(timezone.utc))
    # set author as registrant if available
    reg_text, reg_avatar = format_registrar_field_and_avatar(data.get("created_by", {}))
    if reg_text:
        # author's name shown at top-left with avatar
        embed.set_author(name=f"등록자: {reg_text.splitlines()[0]}", icon_url=reg_avatar)

    # 핵심 정보: left column
    embed.add_field(name="포지션", value=position, inline=True)
    embed.add_field(name="폼", value=form, inline=True)
    # pitch list as its own field
    embed.add_field(name=f"구종 ({len(pitch_types)})", value=pitches_display, inline=False)

    # meta
    created = data.get('created_at', '-')
    updated = data.get('updated_at', '-')
    embed.set_footer(text=f"등록: {short_time(created)}  수정: {short_time(updated)}")

    # thumbnail & image (mc skin)
    try:
        avatar_url, body_url = safe_avatar_urls(nickname)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if body_url:
            embed.set_image(url=body_url)
    except Exception:
        pass

    # add extra small note
    if context and context.get("note"):
        embed.add_field(name="메모", value=context.get("note"), inline=False)

    return embed

# ---------- 헬프 ----------
async def send_help_text(ctx):
    BOT = BOT_PREFIX
    verify_note = " (마인크래프트 닉네임 검증 ON)" if VERIFY_MC else " (마인크래프트 닉네임 검증 OFF)"
    cmds = f"""
**사용 가능한 명령어 (요약)**{verify_note}

**조회**
`{BOT}정보 닉네임` - 기본 정보 출력  
`{BOT}정보상세 닉네임` - 구종 / 폼 / 팀 / 포지션 등 상세

**등록/추가/대량등록**
`{BOT}등록` - 여러 블록(개행)으로 붙여넣어 등록. (예: 닉네임 (폼) \\n 구종...)
`{BOT}추가 nick|이름|팀|포지션|구종1,구종2|폼` - 한 명 추가 (파이프 형식)
`{BOT}추가 nick\\n구종 구종` - 닉네임 + 다음 라인 구종 형식도 가능. (이미 존재하면 구종을 append)
`{BOT}추가`에 여러 블록을 붙여넣으면 다중 추가 됩니다.

**파일 가져오기**
`{BOT}가져오기파일 [팀명] [모드]` - 첨부된 .txt/.csv 파일을 블록으로 읽어 등록
  - [팀명]은 다단어 허용
  - [모드]: 빈칸 또는 'skip'/'건너뛰기' (기본) 또는 '덮어쓰기'/'overwrite'

**수정/닉변/삭제/영입/이적**
`{BOT}수정 nick field value` - 단일 필드 수정 (기존)
블록형: {BOT}수정 nick (언더핸드) [팀 이름]
구종 구종, 구종
- 블록형으로 보내면 해당 선수의 폼/구종/포지션/팀을 **교체**(단, 팀/폼 미기재 시 기존값 유지).
`{BOT}닉변 옛닉 새닉` - 닉변 시 aliases에 옛닉→새닉 매핑을 남깁니다.
`{BOT}삭제 닉네임`  
`{BOT}영입 닉네임 팀명`  
`{BOT}이적 닉네임 팀명` - 누가 이적시켰는지 DB에 기록

**팀 관리**
`{BOT}팀 팀명` - 팀 생성/조회  
`{BOT}팀삭제 팀명` - 팀의 선수들을 모두 FA로 돌리고 팀문서를 삭제

**기록 (타자/투수)**
`{BOT}기록추가타자 닉네임 날짜 PA AB R H RBI HR SB`  
`{BOT}기록추가투수 닉네임 날짜 IP H R ER BB SO`  
`{BOT}기록보기 닉네임`  
`{BOT}기록리셋 닉네임 type` - type: batting|pitching|all

도움: `{BOT}도움` 또는 `{BOT}도움말`
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

# ---------- 파서 유틸: 블록 기반 파싱 ----------
def split_into_blocks(text: str) -> List[List[str]]:
    raw_blocks = re.split(r'\n\s*\n', text.strip(), flags=re.MULTILINE)
    blocks = []
    for b in raw_blocks:
        lines = [line.strip() for line in b.splitlines() if line.strip()]
        if lines:
            blocks.append(lines)
    return blocks

def parse_pitch_line(pitch_line: str) -> List[str]:
    """
    구종 라인 파싱 & 정규화:
    - 토큰을 공백으로 분리 (쉼표 허용)
    - 숫자 없는 경우 DEFAULT_PITCH_POWER 추가
    - 결과 예: 포심(20), 슬라이더(40)
    """
    tokens = [t.strip().rstrip(",") for t in re.split(r'\s+', pitch_line.strip()) if t.strip()]
    out = []
    for tok in tokens:
        norm = normalize_pitch_token(tok)
        if norm:
            out.append(norm)
    return out

def parse_block_to_player(block_lines: List[str]):
    """
    블록(2개 이상의 라인 또는 1라인)을 선수 데이터로 변환.
    반환: dict with keys: nickname, name, team (or None), position, pitch_types(list), form
    """
    nickname = ""
    name = ""
    team = None  # None => 미기재 (호출부가 기존값 유지 결정)
    position = "N/A"
    pitch_types = []
    form = ""

    # 파이프 형식(한 줄) 처리
    if len(block_lines) == 1 and '|' in block_lines[0]:
        parts = block_lines[0].split("|")
        if len(parts) >= 1:
            nickname = parts[0].strip()
        if len(parts) >= 2:
            name = parts[1].strip()
        if len(parts) >= 3:
            t = parts[2].strip()
            team = normalize_team_name(t) if t else None
        if len(parts) >= 4 and parts[3].strip():
            position = parts[3].strip()
        if len(parts) >= 5 and parts[4].strip():
            pitch_types = [normalize_pitch_token(p.strip()) for p in parts[4].split(",") if p.strip()]
        if len(parts) >= 6 and parts[5].strip():
            form = parts[5].strip()
        if not name:
            name = nickname
        return {"nickname": nickname, "name": name, "team": team, "position": position, "pitch_types": pitch_types, "form": form}

    # 라인 기반 파싱
    first = block_lines[0]
    form_match = re.search(r'\(([^)]*)\)', first)
    team_match = re.search(r'\[([^\]]*)\]', first)
    m = re.match(r'^([^\s\(\[]+)', first)
    if m:
        nickname = m.group(1).strip()
    else:
        nickname = first.strip()
    if form_match:
        form = form_match.group(1).strip()
    if team_match:
        team = normalize_team_name(team_match.group(1).strip())

    name = nickname

    # pitch lines: 나머지 라인 전부 합쳐서 파싱
    if len(block_lines) >= 2:
        pitch_text = " ".join(block_lines[1:])
        pitch_types = parse_pitch_line(pitch_text)
    else:
        # 한 라인에 구종이 함께 있는 경우(예: nick 포심(40) 슬라이더(20) )
        rest = first[len(nickname):].strip()
        # 제거: 폼, 팀 표기
        rest = re.sub(r'\([^\)]*\)', '', rest)
        rest = re.sub(r'\[[^\]]*\]', '', rest)
        rest = rest.strip()
        if rest:
            pitch_types = parse_pitch_line(rest)

    return {"nickname": nickname, "name": name, "team": team, "position": position, "pitch_types": pitch_types, "form": form}

# ---------- 조회 ----------
@bot.command(name="정보")
async def info_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    doc = player_doc_ref(nick).get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수가 존재하지 않습니다.")
        return
    d = doc.to_dict()
    embed = make_player_embed(d)
    await ctx.send(embed=embed)

@bot.command(name="정보상세")
async def info_detail_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    doc = player_doc_ref(nick).get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수가 존재하지 않습니다.")
        return
    d = doc.to_dict()
    embed = make_player_embed(d)
    # 상세 필드 추가
    extra = d.get("extra", {})
    if extra:
        embed.add_field(name="추가정보", value=json.dumps(extra, ensure_ascii=False), inline=False)
    await ctx.send(embed=embed)

# ---------- 단일/다중 추가 (파이프 or 멀티라인 지원, append 동작 when existing)
@bot.command(name="추가")
async def add_one_cmd(ctx, *, payload: str):
    """
    지원 형식:
    1) 파이프: nick|이름|팀|포지션|구종1,구종2|폼
    2) 멀티라인: 첫 줄에 nick (또는 nick (폼) [팀]), 다음줄에 구종들
       - 예: "Summ3r_ (언더핸드) [웨어 울브스]\n포심(20) 슬라이더(40)"
       - 기존 선수 문서가 있으면 구종을 append(덧붙임), 숫자 없는 구종은 DEFAULT_PITCH_POWER 부여
    * 이제 payload에 여러 블록(빈줄로 구분)을 넣으면 다중으로 처리합니다.
    """
    if not await ensure_db_or_warn(ctx): return
    if not payload or not payload.strip():
        await ctx.send("❌ 형식 오류. 예: `!추가 nick|이름|팀|포지션|구종1,구종2|폼` 또는 멀티라인 형식.")
        return

    author = ctx.author
    avatar_url = None
    try:
        avatar_url = getattr(author, "display_avatar").url
    except Exception:
        try:
            avatar_url = author.avatar.url
        except Exception:
            avatar_url = None
    created_by_template = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", "")),
        "avatar_url": avatar_url
    }

    # split payload into blocks (빈줄로 구분) — 단일 블록이면 기존 동작과 동일
    blocks = split_into_blocks(payload)
    # 단일 블록이지만 파이프가 아닌 경우에도 블록으로 취급되어 처리됨
    added_new = []
    appended_existing = []
    failed = []

    for i, block_lines in enumerate(blocks, start=1):
        try:
            # if this block is single-line and contains '|', parse as pipe
            if len(block_lines) == 1 and '|' in block_lines[0]:
                parts = block_lines[0].split("|")
                if len(parts) < 4:
                    failed.append(f"블록 {i}: 파이프 형식 오류")
                    continue
                raw_nick = parts[0].strip()
                target_norm = resolve_nick(raw_nick)
                nick_docid = target_norm
                name = parts[1].strip() or raw_nick
                team_val = parts[2].strip()
                team = normalize_team_name(team_val) if team_val else None
                position = parts[3].strip()
                pitch_types = []
                form = ""
                if len(parts) >= 5 and parts[4].strip():
                    pitch_types = [normalize_pitch_token(p.strip()) for p in parts[4].split(",") if p.strip()]
                if len(parts) >= 6:
                    form = parts[5].strip()

                doc_ref = db.collection("players").document(nick_docid)
                exists = doc_ref.get().exists

                # MC 검증: 신규 생성의 경우만 검증
                if VERIFY_MC and not exists:
                    valid = await is_mc_username(raw_nick)
                    if not valid:
                        failed.append(f"블록 {i}: `{raw_nick}` 은(는) 마인크래프트 계정 아님")
                        continue

                # if exists -> append pitches unique; else create
                if exists:
                    existing = doc_ref.get().to_dict() or {}
                    existing_pitches = existing.get("pitch_types", [])
                    appended = existing_pitches[:]
                    existing_bases = [pitch_base_name(p) for p in appended]
                    for p in pitch_types:
                        base = pitch_base_name(p)
                        if base not in existing_bases:
                            appended.append(p)
                            existing_bases.append(base)
                    updates = {"pitch_types": appended, "updated_at": now_iso()}
                    # if team provided in pipe, update it (overwrite)
                    if team is not None:
                        updates["team"] = team or "Free"
                    if form:
                        updates["form"] = form
                    doc_ref.update(updates)
                    # ensure roster contains player
                    team_now = (team or existing.get("team") or "Free")
                    t_ref = team_doc_ref(team_now)
                    t_ref.set({"name": team_now, "created_at": now_iso()}, merge=True)
                    t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(target_norm)])})
                    appended_existing.append(target_norm)
                else:
                    # create new
                    created_by = created_by_template.copy()
                    data = {
                        "nickname": raw_nick,
                        "name": name,
                        "team": team or "Free",
                        "position": position,
                        "pitch_types": pitch_types,
                        "form": form,
                        "extra": {},
                        "created_at": now_iso(),
                        "updated_at": now_iso(),
                        "created_by": created_by
                    }
                    doc_ref.set(data)
                    if data["team"]:
                        t_ref = team_doc_ref(data["team"])
                        t_ref.set({"name": data["team"], "created_at": now_iso()}, merge=True)
                        t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(target_norm)])})
                    added_new.append(target_norm)
                continue  # next block

            # otherwise parse as normal block (multi-line)
            parsed = parse_block_to_player(block_lines)
            raw_nick = parsed["nickname"]
            target_norm = resolve_nick(raw_nick)
            doc_ref = db.collection("players").document(target_norm)
            exists = doc_ref.get().exists

            if exists:
                # append new pitches uniquely
                existing = doc_ref.get().to_dict() or {}
                existing_pitches = existing.get("pitch_types", [])
                new_pitches = parsed.get("pitch_types", [])
                appended = existing_pitches[:]
                existing_bases = [pitch_base_name(p) for p in appended]
                for p in new_pitches:
                    base = pitch_base_name(p)
                    if base not in existing_bases:
                        appended.append(p)
                        existing_bases.append(base)
                updates = {"pitch_types": appended, "updated_at": now_iso()}
                # parsed includes team explicitly? (None => keep old)
                if parsed.get("team") is not None:
                    updates["team"] = parsed.get("team") or "Free"
                if parsed.get("form"):
                    updates["form"] = parsed.get("form")
                if parsed.get("position"):
                    updates["position"] = parsed.get("position")
                if parsed.get("name"):
                    updates["name"] = parsed.get("name")
                doc_ref.update(updates)
                team_now = (parsed.get("team") or existing.get("team") or "Free")
                t_ref = team_doc_ref(team_now)
                t_ref.set({"name": team_now, "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(target_norm)])})
                appended_existing.append(target_norm)
            else:
                # 신규 생성: MC검증
                if VERIFY_MC:
                    valid = await is_mc_username(raw_nick)
                    await asyncio.sleep(0.05)
                    if not valid:
                        failed.append(f"블록 {i}: `{raw_nick}` 은(는) 마인크래프트 계정 아님")
                        continue
                created_by = created_by_template.copy()
                data = {
                    "nickname": raw_nick,
                    "name": parsed.get("name", raw_nick),
                    "team": parsed.get("team") or "Free",
                    "position": parsed.get("position", "N/A"),
                    "pitch_types": parsed.get("pitch_types", []),
                    "form": parsed.get("form", ""),
                    "extra": {},
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "created_by": created_by
                }
                doc_ref.set(data)
                if data["team"]:
                    t_ref = team_doc_ref(data["team"])
                    t_ref.set({"name": data["team"], "created_at": now_iso()}, merge=True)
                    t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(target_norm)])})
                added_new.append(target_norm)
        except Exception as e:
            failed.append(f"블록 {i}: {e}")

    # 요약 임베드 전송
    summary = discord.Embed(title="!추가 처리 요약", timestamp=datetime.now(timezone.utc))
    summary.add_field(name="요청자", value=f"{created_by_template.get('display_name')} (ID: {created_by_template.get('id')})", inline=False)
    summary.add_field(name="총 블록", value=str(len(blocks)), inline=True)
    summary.add_field(name="신규 생성", value=str(len(added_new)), inline=True)
    summary.add_field(name="기존에 구종 추가(append)", value=str(len(appended_existing)), inline=True)
    summary.add_field(name="오류", value=str(len(failed)), inline=True)
    if added_new:
        summary.add_field(name="신규 목록 (최대 30)", value=", ".join(added_new[:30]), inline=False)
    if appended_existing:
        summary.add_field(name="구종 추가된 선수 (최대 30)", value=", ".join(appended_existing[:30]), inline=False)
    if failed:
        summary.add_field(name="오류 예시 (최대 10)", value="\n".join(failed[:10]), inline=False)
        summary.colour = discord.Color.red()
    else:
        summary.colour = discord.Color.green()

    await ctx.send(embed=summary)

# ---------- 블록(개행) 기반 대량 등록 (동작 유지) ----------
@bot.command(name="등록")
async def bulk_register_cmd(ctx, *, bulk_text: str = None):
    """
    본문에 여러 블록(빈줄로 구분)으로 붙여넣기 가능.
    블록 예시:
      Ciel_Tempest (언더핸드)
      포심(20) 슬라이더(40) 너클커브(40)
    """
    if not await ensure_db_or_warn(ctx): return
    if not bulk_text:
        await ctx.send("❌ 본문에 등록할 선수 정보를 여러 블록으로 붙여넣어 주세요.")
        return

    author = ctx.author
    avatar_url = None
    try:
        avatar_url = getattr(author, "display_avatar").url
    except Exception:
        try:
            avatar_url = author.avatar.url
        except Exception:
            avatar_url = None
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", "")),
        "avatar_url": avatar_url
    }

    blocks = split_into_blocks(bulk_text)
    added = []
    errors = []
    for i, block in enumerate(blocks, start=1):
        try:
            p = parse_block_to_player(block)
            raw_nick = p["nickname"]
            target_norm = resolve_nick(raw_nick)
            doc_ref = db.collection("players").document(target_norm)
            exists = doc_ref.get().exists

            # MC validation only if new
            if VERIFY_MC and not exists:
                valid = await is_mc_username(raw_nick)
                await asyncio.sleep(0.08)
                if not valid:
                    errors.append(f"블록 {i}: `{raw_nick}` 은(는) 마인크래프트 계정 아님")
                    continue

            # determine team: if p['team'] is None -> if exists keep old team, else Free
            if exists:
                old = doc_ref.get().to_dict() or {}
                team_val = p.get("team") if p.get("team") is not None else old.get("team", "Free")
                created_at_val = old.get("created_at", now_iso())
                created_by_val = old.get("created_by", created_by)
            else:
                team_val = p.get("team") or "Free"
                created_at_val = now_iso()
                created_by_val = created_by

            data = {
                "nickname": raw_nick if target_norm == normalize_nick(raw_nick) else target_norm,
                "name": p.get("name", raw_nick),
                "team": team_val,
                "position": p.get("position","N/A"),
                "pitch_types": p.get("pitch_types", []),
                "form": p.get("form",""),
                "extra": {},
                "created_at": created_at_val,
                "updated_at": now_iso(),
                "created_by": created_by_val
            }
            doc_ref.set(data)
            if data["team"]:
                t_ref = team_doc_ref(data["team"])
                t_ref.set({"name": data["team"], "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(target_norm)])})
            added.append(target_norm)
        except Exception as e:
            errors.append(f"블록 {i}: {e}")

    summary_embed = discord.Embed(title="대량 등록 요약", timestamp=datetime.now(timezone.utc))
    summary_embed.add_field(name="요청자", value=f"{created_by.get('display_name')} (ID: {created_by.get('id')})", inline=False)
    summary_embed.add_field(name="총 블록", value=str(len(blocks)), inline=True)
    summary_embed.add_field(name="성공", value=str(len(added)), inline=True)
    summary_embed.add_field(name="오류", value=str(len(errors)), inline=True)
    if added:
        summary_embed.add_field(name="성공 목록 (최대 30)", value=", ".join(added[:30]), inline=False)
    if errors:
        summary_embed.add_field(name="오류 예시 (최대 10)", value="\n".join(errors[:10]), inline=False)
        summary_embed.colour = discord.Color.red()
    else:
        summary_embed.colour = discord.Color.green()
    await ctx.send(embed=summary_embed)

# ---------- 파일 가져오기 (첨부된 .txt/.csv) ----------
@bot.command(name="가져오기파일")
async def import_file_cmd(ctx, *, args: str = ""):
    """
    파일 첨부 후: !가져오기파일 [팀명] [모드]
    모드: skip(기본), 덮어쓰기/overwrite
    파일은 블록(빈줄)으로 구분된 형태를 파싱합니다.
    """
    if not await ensure_db_or_warn(ctx): return

    MODE_SKIP = "skip"
    MODE_OVERWRITE = "overwrite"
    mode_aliases = {
        "skip": MODE_SKIP, "건너뛰기": MODE_SKIP,
        "덮어쓰기": MODE_OVERWRITE, "overwrite": MODE_OVERWRITE, "덮": MODE_OVERWRITE
    }

    team_override = None
    mode = MODE_SKIP

    args = args or ""
    tokens = args.strip().split()
    if tokens:
        last = tokens[-1].lower()
        if last in mode_aliases:
            mode = mode_aliases[last]
            team_override = " ".join(tokens[:-1]).strip() if len(tokens) > 1 else None
        else:
            team_override = args.strip()
    if team_override:
        team_override = normalize_team_name(team_override)

    if not ctx.message.attachments:
        await ctx.send("❌ 첨부된 파일이 없습니다. .txt 또는 .csv 파일을 첨부해 주세요.")
        return
    att = ctx.message.attachments[0]
    fname = att.filename.lower()
    if not (fname.endswith(".txt") or fname.endswith(".csv")):
        await ctx.send("❌ 지원되는 파일 형식이 아닙니다. .txt 또는 .csv 파일을 첨부하세요.")
        return
    try:
        data = await att.read()
        text = data.decode("utf-8").strip()
    except Exception as e:
        await ctx.send(f"❌ 파일 읽기 오류: {e}")
        return

    author = ctx.author
    avatar_url = None
    try:
        avatar_url = getattr(author, "display_avatar").url
    except Exception:
        try:
            avatar_url = author.avatar.url
        except Exception:
            avatar_url = None
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", "")),
        "avatar_url": avatar_url
    }

    blocks = split_into_blocks(text)
    added = []
    overwritten = []
    skipped = []
    errors = []
    for i, block in enumerate(blocks, start=1):
        try:
            p = parse_block_to_player(block)
            raw_nick = p["nickname"]
            target_norm = resolve_nick(raw_nick)
            doc_ref = db.collection("players").document(target_norm)
            exists = doc_ref.get().exists
            if exists and mode == MODE_SKIP:
                skipped.append(target_norm)
                continue

            # preserve created_at if exists
            created_at_val = now_iso()
            old = None
            if exists:
                old = doc_ref.get().to_dict()
                if old and old.get("created_at"):
                    created_at_val = old.get("created_at")

            # team override or p['team'] None => if exists keep old team else default Free
            if team_override:
                team_val = team_override
            else:
                if exists:
                    team_val = p.get("team") if p.get("team") is not None else (old.get("team", "Free") if old else "Free")
                else:
                    team_val = p.get("team") or "Free"

            # MC name check only on new creation
            if VERIFY_MC and not exists:
                valid = await is_mc_username(raw_nick)
                await asyncio.sleep(0.08)
                if not valid:
                    errors.append(f"블록 {i}: `{raw_nick}` 은(는) 마인크래프트 계정 아님")
                    continue

            data_obj = {
                "nickname": raw_nick if target_norm == normalize_nick(raw_nick) else target_norm,
                "name": p.get("name", raw_nick),
                "team": team_val or "Free",
                "position": p.get("position","N/A"),
                "pitch_types": p.get("pitch_types", []),
                "form": p.get("form",""),
                "extra": {},
                "created_at": created_at_val,
                "updated_at": now_iso(),
                "created_by": created_by if not exists else (old.get("created_by") if old and old.get("created_by") else created_by)
            }

            doc_ref.set(data_obj)
            if team_val:
                t_ref = team_doc_ref(team_val)
                t_ref.set({"name": team_val, "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(target_norm)])})

            if exists and mode == MODE_OVERWRITE:
                overwritten.append(target_norm)
            else:
                added.append(target_norm)
        except Exception as e:
            errors.append(f"블록 {i}: {e}")

    summary_embed = discord.Embed(title="파일 가져오기 요약", timestamp=datetime.now(timezone.utc))
    summary_embed.add_field(name="파일", value=f"{att.filename}", inline=False)
    summary_embed.add_field(name="요청자", value=f"{created_by.get('display_name')} (ID: {created_by.get('id')})", inline=False)
    if team_override:
        summary_embed.add_field(name="팀 오버라이드", value=team_override, inline=False)
    summary_embed.add_field(name="총 블록", value=str(len(blocks)), inline=True)
    summary_embed.add_field(name="추가", value=str(len(added)), inline=True)
    summary_embed.add_field(name="덮어씀", value=str(len(overwritten)), inline=True)
    summary_embed.add_field(name="스킵(중복)", value=str(len(skipped)), inline=True)
    summary_embed.add_field(name="오류", value=str(len(errors)), inline=True)
    if added:
        summary_embed.add_field(name="추가 목록 (최대 20)", value=", ".join(added[:20]), inline=False)
    if overwritten:
        summary_embed.add_field(name="덮어쓴 목록 (최대 20)", value=", ".join(overwritten[:20]), inline=False)
    if skipped:
        summary_embed.add_field(name="스킵된 목록 (중복, 최대 20)", value=", ".join(skipped[:20]), inline=False)
    if errors:
        summary_embed.add_field(name="오류 예시 (최대 10)", value="\n".join(errors[:10]), inline=False)
        summary_embed.colour = discord.Color.red()
    else:
        summary_embed.colour = discord.Color.green()
    await ctx.send(embed=summary_embed)

# ---------- 닉변: aliases에 이전 닉네임 매핑 추가 ----------
@bot.command(name="닉변")
async def nickchange_cmd(ctx, oldnick: str, newnick: str):
    if not await ensure_db_or_warn(ctx): return
    old_ref = db.collection("players").document(normalize_nick(oldnick))
    old_doc = old_ref.get()
    if not old_doc.exists:
        await ctx.send(f"❌ `{oldnick}` 가 존재하지 않습니다.")
        return
    new_ref = db.collection("players").document(normalize_nick(newnick))
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
            try:
                team_doc_ref(team).update({"roster": firestore.ArrayRemove([normalize_nick(oldnick)])})
            except Exception:
                pass
            team_doc_ref(team).update({"roster": firestore.ArrayUnion([normalize_nick(newnick)])})

        # move records
        rec_old = records_doc_ref(oldnick)
        rec_old_doc = rec_old.get()
        if rec_old_doc.exists:
            rec_new = records_doc_ref(newnick)
            rec_new.set(rec_old_doc.to_dict())
            rec_old.delete()

        # aliases에 옛 닉 추가 (문서 id = normalized oldnick)
        alias_ref = db.collection("aliases").document(normalize_nick(oldnick))
        alias_ref.set({"current": normalize_nick(newnick), "created_at": now_iso()}, merge=True)

        await ctx.send(f"✅ `{oldnick}` → `{newnick}` 으로 변경되었습니다. (aliases에 이전 닉네임이 기록됨)")
    except Exception as e:
        await ctx.send(f"❌ 닉네임 변경 실패: {e}")

# ---------- 수정: 단일필드 또는 블록형(전체 교체) ----------
@bot.command(name="수정")
async def edit_cmd(ctx, *, payload: str):
    """
    사용법:
    1) 단일필드: !수정 nick field value
    2) 블록형 교체:
       !수정 nick (언더핸드) [팀 이름]
       구종 구종, 구종
       - 블록에서 팀/폼 미기재 시 기존값 유지
       - 구종은 블록 파싱 결과로 **완전 교체**
    """
    if not await ensure_db_or_warn(ctx): return
    if not payload or not payload.strip():
        await ctx.send("❌ 사용법: `!수정 nick field value` 또는 블록형으로 보내세요.")
        return

    # 블록형 판단: payload에 newline이 있거나 괄호/대괄호 포함되어 블록으로 생각
    if "\n" in payload or "(" in payload or "[" in payload:
        # split into lines; ensure first token is the nick
        lines = [l for l in payload.splitlines() if l.strip()]
        if not lines:
            await ctx.send("❌ 블록 형식 오류.")
            return
        parsed = parse_block_to_player(lines)
        raw_nick = parsed["nickname"]
        doc_ref = db.collection("players").document(resolve_nick(raw_nick))
        doc = doc_ref.get()
        if not doc.exists:
            await ctx.send(f"❌ `{raw_nick}` 선수가 존재하지 않습니다.")
            return
        old = doc.to_dict() or {}

        # prepare update: replace fields (form/team/pitch_types/position/name)
        updates = {}
        # team: if parsed['team'] is None -> keep old team; else set to parsed team (or Free)
        if parsed.get("team") is None:
            updates["team"] = old.get("team", "Free")
        else:
            updates["team"] = parsed.get("team") or "Free"
        # form: if parsed form empty -> keep old form else replace
        if parsed.get("form"):
            updates["form"] = parsed.get("form")
        else:
            updates["form"] = old.get("form", "")
        # position/name
        updates["name"] = parsed.get("name", old.get("name", raw_nick))
        updates["position"] = parsed.get("position", old.get("position", "N/A"))
        # pitch_types: replace entirely with parsed list (even if empty)
        updates["pitch_types"] = parsed.get("pitch_types", [])
        updates["updated_at"] = now_iso()

        try:
            doc_ref.update(updates)
            # team roster fix: if team changed, move roster entries
            old_team = old.get("team")
            new_team = updates.get("team")
            if old_team and old_team != new_team:
                try:
                    team_doc_ref(old_team).update({"roster": firestore.ArrayRemove([normalize_nick(doc_ref.id)])})
                except Exception:
                    pass
                t_ref = team_doc_ref(new_team)
                t_ref.set({"name": new_team, "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(doc_ref.id)])})
            embed = make_player_embed(doc_ref.get().to_dict(), context={"note": "정보가 블록형으로 수정됨"})
            await ctx.send(content=f"✅ `{doc_ref.id}` 정보가 업데이트 되었습니다.", embed=embed)
        except Exception as e:
            await ctx.send(f"❌ 수정 실패: {e}")
        return

    # 아니면 단일 필드 방식: nick field value
    parts = payload.strip().split(maxsplit=2)
    if len(parts) < 3:
        await ctx.send("❌ 단일 필드 수정 형식: `!수정 nick field value`")
        return
    nick, field, value = parts[0], parts[1], parts[2]
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
        types = [normalize_pitch_token(p.strip()) for p in value.split(",") if p.strip()]
        updates["pitch_types"] = types
    else:
        updates[field] = value
    updates["updated_at"] = now_iso()
    try:
        ref.update(updates)
        await ctx.send(f"✅ `{nick}` 업데이트 성공.")
    except Exception as e:
        await ctx.send(f"❌ 업데이트 실패: {e}")

# ---------- 나머지 명령들 (이적/영입/삭제/구종삭제/팀/팀삭제/목록/트레이드/웨이버/방출/기록) ----------
@bot.command(name="이적")
async def transfer_cmd(ctx, nick: str, *, newteam: str):
    if not await ensure_db_or_warn(ctx): return
    p_ref = player_doc_ref(nick)
    p_doc = p_ref.get()
    if not p_doc.exists:
        await ctx.send(f"❌ `{nick}` 가 존재하지 않습니다.")
        return
    data = p_doc.to_dict()
    oldteam = data.get("team")
    newteam_norm = normalize_team_name(newteam)
    try:
        author = ctx.author
        avatar_url = None
        try:
            avatar_url = getattr(author, "display_avatar").url
        except Exception:
            try:
                avatar_url = author.avatar.url
            except Exception:
                avatar_url = None
        transfer_by = {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", ""),
            "discriminator": getattr(author, "discriminator", None),
            "display_name": getattr(author, "display_name", getattr(author, "name", "")),
            "avatar_url": avatar_url
        }

        p_ref.update({"team": newteam_norm, "updated_at": now_iso(), "last_transfer_by": transfer_by})

        if oldteam:
            try:
                team_doc_ref(oldteam).update({"roster": firestore.ArrayRemove([normalize_nick(p_ref.id)])})
            except Exception:
                pass
        t_ref = team_doc_ref(newteam_norm)
        t_ref.set({"name": newteam_norm, "created_at": now_iso()}, merge=True)
        t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(p_ref.id)])})

        embed = discord.Embed(title="선수 이적 완료", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="선수", value=p_ref.id, inline=True)
        embed.add_field(name="이전팀", value=oldteam or "Free", inline=True)
        embed.add_field(name="이적팀", value=newteam_norm, inline=True)
        embed.add_field(name="이적자", value=f"{transfer_by.get('display_name')} (ID: {transfer_by.get('id')})", inline=False)
        avatar_url_mc, _ = safe_avatar_urls(p_ref.id)
        if avatar_url_mc:
            embed.set_thumbnail(url=avatar_url_mc)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 이적 실패: {e}")

@bot.command(name="영입")
async def recruit_cmd(ctx, nick: str, *, teamname: str):
    if not await ensure_db_or_warn(ctx): return
    p_ref = player_doc_ref(nick)
    p_doc = p_ref.get()
    if not p_doc.exists:
        await ctx.send(f"❌ `{nick}` 선수를 찾을 수 없습니다.")
        return
    data = p_doc.to_dict()
    oldteam = data.get("team")
    newteam = normalize_team_name(teamname)
    try:
        author = ctx.author
        avatar_url = None
        try:
            avatar_url = getattr(author, "display_avatar").url
        except Exception:
            try:
                avatar_url = author.avatar.url
            except Exception:
                avatar_url = None
        updated_by = {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", ""),
            "discriminator": getattr(author, "discriminator", None),
            "display_name": getattr(author, "display_name", getattr(author, "name", "")),
            "avatar_url": avatar_url
        }

        p_ref.update({"team": newteam, "status": None, "updated_at": now_iso(), "last_transfer_by": updated_by})
        if oldteam:
            try:
                team_doc_ref(oldteam).update({"roster": firestore.ArrayRemove([normalize_nick(p_ref.id)])})
            except Exception:
                pass
        t_ref = team_doc_ref(newteam)
        t_ref.set({"name": newteam, "created_at": now_iso()}, merge=True)
        t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(p_ref.id)])})

        embed = discord.Embed(title="선수 영입 완료", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="선수", value=p_ref.id, inline=True)
        embed.add_field(name="이전팀", value=oldteam or "Free", inline=True)
        embed.add_field(name="영입팀", value=newteam, inline=True)
        embed.add_field(name="영입자", value=f"{updated_by.get('display_name')} (ID: {updated_by.get('id')})", inline=False)
        avatar_url_mc, _ = safe_avatar_urls(p_ref.id)
        if avatar_url_mc:
            embed.set_thumbnail(url=avatar_url_mc)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 영입 실패: {e}")

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
        newlist = [p for p in current if not (p == pitch or pitch_base_name(p) == pitch_base_name(pitch))]
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
    team_norm = normalize_team_name(teamname)
    t_ref = team_doc_ref(team_norm)
    t_doc = t_ref.get()
    if not t_doc.exists:
        t_ref.set({"name": team_norm, "created_at": now_iso(), "roster": []})
        await ctx.send(f"✅ 팀 `{team_norm}` 이(가) 생성되었습니다.")
        return
    t = t_doc.to_dict()
    roster = t.get("roster", [])
    if roster:
        await ctx.send(f"**{team_norm}** — 로스터 ({len(roster)}):\n" + ", ".join(roster[:200]))
    else:
        await ctx.send(f"**{team_norm}** — 로스터가 비어있습니다.")

@bot.command(name="팀삭제")
async def delete_team_cmd(ctx, *, teamname: str):
    if not await ensure_db_or_warn(ctx): return
    team_norm = normalize_team_name(teamname)
    t_ref = team_doc_ref(team_norm)
    t_doc = t_ref.get()
    if not t_doc.exists:
        await ctx.send(f"❌ 팀 `{team_norm}` 이(가) 존재하지 않습니다.")
        return

    try:
        t_data = t_doc.to_dict() or {}
        roster = t_data.get("roster", []) or []
        moved = []
        errors = []
        fa_ref = team_doc_ref("FA")
        fa_ref.set({"name": "FA", "created_at": now_iso()}, merge=True)
        for nick_norm in roster:
            try:
                p_ref = db.collection("players").document(nick_norm)
                p_doc = p_ref.get()
                if not p_doc.exists:
                    errors.append(f"{nick_norm}: 선수 데이터 없음")
                    continue
                p_ref.update({"team": "FA", "updated_at": now_iso()})
                fa_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick_norm)])})
                moved.append(nick_norm)
            except Exception as e:
                errors.append(f"{nick_norm}: {e}")
        t_ref.delete()
        embed = discord.Embed(title="팀 삭제 완료", description=f"팀 `{team_norm}` 을(를) 삭제하고 해당 선수들을 FA로 이동했습니다.", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="원팀", value=team_norm, inline=False)
        embed.add_field(name="이동(FA) 수", value=str(len(moved)), inline=True)
        embed.add_field(name="오류 수", value=str(len(errors)), inline=True)
        if moved:
            embed.add_field(name="이동된 선수 (최대 50)", value=", ".join(moved[:50]), inline=False)
        if errors:
            embed.add_field(name="오류 예시 (최대 10)", value="\n".join(errors[:10]), inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 팀 삭제 중 오류 발생: {e}")

@bot.command(name="목록")
async def list_cmd(ctx, kind: str = "players"):
    if not await ensure_db_or_warn(ctx): return
    if kind == "players":
        docs = db.collection("players").order_by("nickname").limit(500).stream()
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
            team_doc_ref(t1).update({"roster": firestore.ArrayRemove([normalize_nick(r1.id)])})
            if t2:
                team_doc_ref(t2).update({"roster": firestore.ArrayUnion([normalize_nick(r1.id)])})
        if t2:
            team_doc_ref(t2).update({"roster": firestore.ArrayRemove([normalize_nick(r2.id)])})
            if t1:
                team_doc_ref(t1).update({"roster": firestore.ArrayUnion([normalize_nick(r2.id)])})
        await ctx.send(f"✅ `{r1.id}` 과 `{r2.id}` 트레이드 완료 ({t1} <-> {t2})")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

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
        await ctx.send(f"✅ `{ref.id}` 이(가) 웨이버 상태로 변경되었습니다.")
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
            try:
                team_doc_ref(team).update({"roster": firestore.ArrayRemove([normalize_nick(ref.id)])})
            except Exception:
                pass
        await ctx.send(f"✅ `{ref.id}` 이(가) 방출되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

@bot.command(name="삭제")
async def delete_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ 해당 선수 없음: `{nick}`")
        return
    data = doc.to_dict()
    team = data.get("team")
    try:
        ref.delete()
        if team:
            try:
                t_ref = team_doc_ref(team)
                t_ref.update({"roster": firestore.ArrayRemove([normalize_nick(ref.id)])})
            except Exception:
                pass
        records_doc_ref(nick).delete()
        await ctx.send(f"🗑️ `{ref.id}` 삭제되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 삭제 실패: {e}")

# 기록 관련 명령들 (기존 로직 유지)
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
        await ctx.send(f"✅ `{ref.id}` 에 타자 기록 추가됨: {date}")
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
        await ctx.send(f"✅ `{ref.id}` 에 투수 기록 추가됨: {date}")
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
    lines = [f"**{rec.id} — 기록 요약**"]
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
        # ignore unknown commands to avoid spam
        return
    else:
        await ctx.send(f"명령 실행 중 오류가 발생했습니다: `{error}`")
        print("Unhandled command error:", error)

# ---------- 종료 처리 ----------
@bot.event
async def on_close():
    try:
        asyncio.create_task(close_http_session())
    except Exception:
        pass

# ---------- 실행 ----------
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
        try:
            loop = asyncio.get_event_loop()
            if http_session and not http_session.closed:
                loop.run_until_complete(close_http_session())
        except Exception:
            pass

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
