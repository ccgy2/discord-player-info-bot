# bot.py
"""
Discord + Firebase (Firestore) Baseball Player Manager Bot
- Python 3.8+
- discord.py 기반 명령형 봇
- Firestore collection: players, teams, records
- 주요 기능: 마인크래프트 닉검증(Mojang), Minotar 스킨 임베드, created_by 저장,
  대량등록, 파일가져오기(중복 처리 옵션), 이적/영입 시 수행자 기록, 팀명 자동 정규화
- 환경변수:
  - DISCORD_TOKEN (필수)
  - FIREBASE_KEY (JSON-string) 또는 GOOGLE_APPLICATION_CREDENTIALS (파일 경로)
  - VERIFY_MC (옵션, default true) -> false로 설정하면 Mojang 검증 비활성화
  - BOT_PREFIX (옵션, 기본 "!")
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
    """
    팀명 자동 정규화:
      - 앞뒤 공백 제거
      - 연속 공백을 단일 공백으로 축소
      - (필요시 추가 규칙을 넣을 수 있음)
    """
    if not team:
        return "Free"
    # collapse multiple whitespace into single space, strip edges
    return " ".join(team.strip().split())

async def ensure_db_or_warn(ctx):
    if db is None:
        await ctx.send("❌ 데이터베이스가 초기화되어 있지 않습니다. 관리자에게 문의하세요.")
        return False
    return True

# ---------- Firestore 참조 헬퍼 (팀명은 정규화) ----------
def player_doc_ref(nick: str):
    return db.collection("players").document(normalize_nick(nick))

def team_doc_ref(teamname: str):
    return db.collection("teams").document(normalize_team_name(teamname))

def records_doc_ref(nick: str):
    return db.collection("records").document(normalize_nick(nick))

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

# ---------- 임베드 도우미 ----------
def format_registrar_field(created_by: dict) -> str:
    if not created_by:
        return "-"
    uid = created_by.get("id", "-")
    display = created_by.get("display_name") or created_by.get("name") or "-"
    discr = created_by.get("discriminator")
    if discr:
        name_repr = f"{display} ({created_by.get('name','')}{('#'+discr)})"
    else:
        name_repr = f"{display}"
    return f"{name_repr}\nID: {uid}"

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
    reg_info = format_registrar_field(data.get("created_by", {}))
    embed.add_field(name="등록자", value=reg_info, inline=True)
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
`{BOT}등록` - 여러 줄 텍스트로 등록 (파이프 또는 라인 포맷 지원)
`{BOT}추가 nick|이름|팀|포지션|구종1,구종2|폼` - 한 명 추가

**파일 가져오기**
`{BOT}가져오기파일 [팀명] [모드]` - 첨부된 .txt/.csv 파일을 읽어 등록
  - [팀명]은 다단어 허용(공백 포함)
  - [모드]: 빈칸 또는 'skip'/'건너뛰기' (기본) 또는 '덮어쓰기'/'overwrite' (기존 문서 덮어씀)

**수정/닉변/삭제/영입/이적**
`{BOT}수정 닉네임 필드 새값`  
`{BOT}닉변 옛닉 새닉`  
`{BOT}삭제 닉네임`  
`{BOT}영입 닉네임 팀명` - 방출된 선수를 팀에 영입 (다단어 팀명 허용)  
`{BOT}이적 닉네임 팀명` - 선수 이적 (다단어 팀명 허용) — 누가 이적시켰는지 임베드에 표기됩니다.

**팀 관리**
`{BOT}팀 팀명` - 팀 생성/조회  
`{BOT}목록 players|teams` - 목록 보기  
`{BOT}팀삭제 팀명` - 해당 팀의 선수들을 모두 FA로 돌리고 팀 문서를 삭제합니다.

**기록 (타자/투수)**
`{BOT}기록추가타자 닉네임 날짜 PA AB R H RBI HR SB`  
`{BOT}기록추가투수 닉네임 날짜 IP H R ER BB SO`  
`{BOT}기록보기 닉네임`  
`{BOT}기록리셋 닉네임 type` - type: batting|pitching|all

도움이 필요하면 `{BOT}도움` 또는 `{BOT}도움말`
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

# ---------- 기본 명령들 ----------
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
    reg_info = format_registrar_field(d.get("created_by", {}))
    embed.add_field(name="등록자", value=reg_info, inline=True)
    try:
        avatar_url, body_url = safe_avatar_urls(d.get('nickname',''))
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if body_url:
            embed.set_image(url=body_url)
    except Exception:
        pass
    await ctx.send(embed=embed)

# ---------- 단일 추가 ----------
@bot.command(name="추가")
async def add_one_cmd(ctx, *, payload: str):
    if not await ensure_db_or_warn(ctx): return
    parts = payload.split("|")
    if len(parts) < 4:
        await ctx.send("❌ 형식 오류. 예시: `!추가 nick|이름|팀|포지션|구종1,구종2|폼`")
        return
    nick = parts[0].strip()
    name = parts[1].strip()
    team = normalize_team_name(parts[2].strip())
    position = parts[3].strip()
    pitch_types = []
    form = ""
    if len(parts) >= 5 and parts[4].strip():
        pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
    if len(parts) >= 6:
        form = parts[5].strip()

    if VERIFY_MC:
        valid = await is_mc_username(nick)
        if not valid:
            await ctx.send(f"❌ `{nick}` 는(은) 유효한 마인크래프트 계정명이 아닙니다. 등록이 취소되었습니다.")
            return

    author = ctx.author
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", ""))
    }

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
        "updated_at": now_iso(),
        "created_by": created_by
    }
    try:
        doc_ref.set(data)
        if team:
            t_ref = team_doc_ref(team)
            t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
            t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})
        embed = make_player_embed(data, include_body=True)
        embed.colour = discord.Color.green()
        await ctx.send(content="✅ 선수 추가 완료", embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 추가 실패: {e}")

# ---------- 대량 등록 ----------
@bot.command(name="등록")
async def bulk_register_cmd(ctx, *, bulk_text: str = None):
    if not await ensure_db_or_warn(ctx): return
    if not bulk_text:
        await ctx.send("❌ 본문에 등록할 선수 정보를 여러 줄로 붙여넣어 주세요. (또는 첨부 파일 사용: `!가져오기파일 [팀명] [모드]`)")
        return

    author = ctx.author
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", ""))
    }

    lines = [l.strip() for l in bulk_text.splitlines() if l.strip()]
    added = []
    errors = []
    pitch_pattern = re.compile(r'([^\s,()]+)\s*\(\s*(\d+)\s*\)')

    for i, line in enumerate(lines, start=1):
        try:
            if '|' in line:
                parts = line.split("|")
                if len(parts) < 4:
                    errors.append(f"라인 {i}: 파이프 형식 오류")
                    continue
                nick = parts[0].strip()
                name = parts[1].strip()
                team = normalize_team_name(parts[2].strip())
                position = parts[3].strip()
                pitch_types = []
                form = ""
                if len(parts) >= 5 and parts[4].strip():
                    pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
                if len(parts) >= 6:
                    form = parts[5].strip()
            else:
                tokens = line.split()
                if not tokens:
                    errors.append(f"라인 {i}: 빈 줄")
                    continue
                nick = tokens[0].strip()
                rest = line[len(tokens[0]):].strip()
                form_match = re.search(r'\(([^)]*)\)', rest)
                team_match = re.search(r'\[([^\]]*)\]', rest)
                form = form_match.group(1).strip() if form_match else ""
                team = normalize_team_name(team_match.group(1).strip()) if team_match else "Free"
                pitch_types = []
                for pm in pitch_pattern.finditer(line):
                    pname = pm.group(1).strip()
                    pval = pm.group(2).strip()
                    pitch_types.append(f"{pname}({pval})")
                name = nick
                position = "N/A"

            if VERIFY_MC:
                valid = await is_mc_username(nick)
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
                "updated_at": now_iso(),
                "created_by": created_by
            }
            doc_ref.set(data)
            if team:
                t_ref = team_doc_ref(team)
                t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})
            added.append(nick)
        except Exception as e:
            errors.append(f"라인 {i}: {e}")

    summary_embed = discord.Embed(title="등록 요약", timestamp=datetime.now(timezone.utc))
    summary_embed.add_field(name="요청자", value=f"{created_by.get('display_name')} (ID: {created_by.get('id')})", inline=False)
    summary_embed.add_field(name="총 입력", value=str(len(lines)), inline=True)
    summary_embed.add_field(name="성공", value=str(len(added)), inline=True)
    summary_embed.add_field(name="오류", value=str(len(errors)), inline=True)

    if added:
        summary_embed.add_field(name="성공 목록 (최대 20)", value=", ".join(added[:20]), inline=False)
        if len(added) > 20:
            summary_embed.add_field(name="(생략)", value=f"...외 {len(added)-20}명", inline=False)
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
    사용법:
      1) 채널에 .txt/.csv 파일 첨부
      2) 메시지에 명령: !가져오기파일 [팀명] [모드]
         - [팀명]: 다단어 허용, 주면 파일 내 모든 선수의 팀을 덮어씀
         - [모드]: 없으면 기본 'skip' (기존 문서가 있으면 건너뜀)
             '덮어쓰기' / 'overwrite' : 기존 문서 덮어쓰기 (created_at 보존, updated_at 갱신)
             'skip' / '건너뛰기' : 기존 문서가 있으면 스킵 (기본)
    예: !가져오기파일 레이 마린스 덮어쓰기
    """
    if not await ensure_db_or_warn(ctx): return

    # 지원 모드
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
        # 마지막 토큰이 모드인지 확인
        last = tokens[-1].lower()
        if last in mode_aliases:
            mode = mode_aliases[last]
            team_override = " ".join(tokens[:-1]).strip() if len(tokens) > 1 else None
        else:
            team_override = args.strip()

    # normalize override team
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
        await ctx.send(f"❌ 파일을 읽는 중 오류가 발생했습니다: {e}")
        return

    author = ctx.author
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", ""))
    }

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    added = []
    overwritten = []
    skipped = []
    errors = []
    pitch_pattern = re.compile(r'([^\s,()]+)\s*\(\s*(\d+)\s*\)')

    for i, line in enumerate(lines, start=1):
        try:
            if '|' in line:
                parts = line.split("|")
                if len(parts) < 4:
                    errors.append(f"파일 라인 {i}: 파이프 형식 오류")
                    continue
                nick = parts[0].strip()
                name = parts[1].strip()
                team = normalize_team_name(parts[2].strip())
                position = parts[3].strip()
                pitch_types = []
                form = ""
                if len(parts) >= 5 and parts[4].strip():
                    pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
                if len(parts) >= 6:
                    form = parts[5].strip()
            else:
                tokens_line = line.split()
                if not tokens_line:
                    errors.append(f"파일 라인 {i}: 빈 줄")
                    continue
                nick = tokens_line[0].strip()
                rest = line[len(tokens_line[0]):].strip()
                form_match = re.search(r'\(([^)]*)\)', rest)
                team_match = re.search(r'\[([^\]]*)\]', rest)
                form = form_match.group(1).strip() if form_match else ""
                team = normalize_team_name(team_match.group(1).strip()) if team_match else "Free"
                pitch_types = []
                for pm in pitch_pattern.finditer(line):
                    pname = pm.group(1).strip()
                    pval = pm.group(2).strip()
                    pitch_types.append(f"{pname}({pval})")
                name = nick
                position = "N/A"

            # 팀 오버라이드가 주어졌다면 덮어쓰기 (다단어 허용)
            if team_override:
                team = team_override

            if VERIFY_MC:
                valid = await is_mc_username(nick)
                await asyncio.sleep(0.12)
                if not valid:
                    errors.append(f"파일 라인 {i}: `{nick}` 은(는) 마인크래프트 계정 아님")
                    continue

            # 중복 처리
            doc_ref = player_doc_ref(nick)
            exists = doc_ref.get().exists

            if exists and mode == MODE_SKIP:
                skipped.append(nick)
                continue

            # prepare data_obj, try to preserve created_at when overwriting
            created_at_val = now_iso()
            old = None
            if exists:
                old = doc_ref.get().to_dict()
                if old and old.get("created_at"):
                    created_at_val = old.get("created_at")

            data_obj = {
                "nickname": nick,
                "name": name,
                "team": team or "Free",
                "position": position,
                "pitch_types": pitch_types,
                "form": form,
                "extra": {},
                "created_at": created_at_val,
                "updated_at": now_iso(),
                "created_by": created_by if not exists else (old.get("created_by") if old and old.get("created_by") else created_by)
            }

            doc_ref.set(data_obj)  # overwrite or set
            if team:
                t_ref = team_doc_ref(team)
                t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})

            if exists and mode == MODE_OVERWRITE:
                overwritten.append(nick)
            else:
                added.append(nick)
        except Exception as e:
            errors.append(f"파일 라인 {i}: {e}")

    # 요약 임베드
    summary_embed = discord.Embed(title="파일 가져오기 요약", timestamp=datetime.now(timezone.utc))
    summary_embed.add_field(name="파일", value=f"{att.filename}", inline=False)
    summary_embed.add_field(name="요청자", value=f"{created_by.get('display_name')} (ID: {created_by.get('id')})", inline=False)
    if team_override:
        summary_embed.add_field(name="팀 오버라이드", value=team_override, inline=False)
    summary_embed.add_field(name="총 입력", value=str(len(lines)), inline=True)
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

# ---------- 영입 (방출->팀 배치) ----------
@bot.command(name="영입")
async def recruit_cmd(ctx, nick: str, *, teamname: str):
    """
    사용법: !영입 닉네임 팀명
    팀명 다단어 허용. 영입 수행자 정보를 DB에 남기고 임베드로 요약 출력.
    """
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수를 찾을 수 없습니다.")
        return
    try:
        data = doc.to_dict()
        oldteam = data.get("team")
        newteam = normalize_team_name(teamname)
        if oldteam == newteam:
            await ctx.send(f"⚠️ `{nick}` 은(는) 이미 `{newteam}` 소속입니다.")
            return

        author = ctx.author
        updated_by = {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", ""),
            "discriminator": getattr(author, "discriminator", None),
            "display_name": getattr(author, "display_name", getattr(author, "name", ""))
        }

        ref.update({"team": newteam, "status": None, "updated_at": now_iso(), "last_transfer_by": updated_by})

        if oldteam:
            try:
                team_doc_ref(oldteam).update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
            except Exception:
                pass
        t_ref = team_doc_ref(newteam)
        t_ref.set({"name": newteam, "created_at": now_iso()}, merge=True)
        t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})

        # 임베드 출력: 누가 영입했는지 포함
        embed = discord.Embed(title="선수 영입 완료", timestamp=datetime.now(timezone.utc))
        embed.add_field(name="선수", value=nick, inline=True)
        embed.add_field(name="이전팀", value=oldteam or "Free", inline=True)
        embed.add_field(name="영입팀", value=newteam, inline=True)
        embed.add_field(name="영입자", value=f"{updated_by.get('display_name')} (ID: {updated_by.get('id')})", inline=False)
        avatar_url, body_url = safe_avatar_urls(nick)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.colour = discord.Color.blue()
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 영입 실패: {e}")

# ---------- 이적 (다단어 팀명 허용) ----------
@bot.command(name="이적")
async def transfer_cmd(ctx, nick: str, *, newteam: str):
    """
    사용법: !이적 닉네임 팀명
    팀명 다단어 허용. 수행자 정보(last_transfer_by) DB에 기록하고 임베드 출력.
    """
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
        transfer_by = {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", ""),
            "discriminator": getattr(author, "discriminator", None),
            "display_name": getattr(author, "display_name", getattr(author, "name", ""))
        }

        p_ref.update({"team": newteam_norm, "updated_at": now_iso(), "last_transfer_by": transfer_by})

        # roster updates
        if oldteam:
            try:
                team_doc_ref(oldteam).update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
            except Exception:
                pass
        t_ref = team_doc_ref(newteam_norm)
        t_ref.set({"name": newteam_norm, "created_at": now_iso()}, merge=True)
        t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})

        # 임베드: 누가 이적시켰는지 포함
        embed = discord.Embed(title="선수 이적 완료", timestamp=datetime.now(timezone.utc))
        embed.add_field(name="선수", value=nick, inline=True)
        embed.add_field(name="이전팀", value=oldteam or "Free", inline=True)
        embed.add_field(name="이적팀", value=newteam_norm, inline=True)
        embed.add_field(name="이적자", value=f"{transfer_by.get('display_name')} (ID: {transfer_by.get('id')})", inline=False)
        avatar_url, body_url = safe_avatar_urls(nick)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.colour = discord.Color.gold()
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 이적 실패: {e}")

# ---------- 팀 삭제: 해당 팀의 선수들을 FA로 돌리고 팀 문서를 삭제 ----------
@bot.command(name="팀삭제")
async def delete_team_cmd(ctx, *, teamname: str):
    """
    사용법: !팀삭제 팀명
    - 팀명을 정규화하여 해당 팀 문서를 조회
    - 해당 팀의 로스터에 있는 모든 선수들의 team 필드를 "FA"로 변경하고 updated_at 갱신
    - FA 팀 문서의 roster에 해당 선수들 추가
    - 원래 팀 문서를 삭제
    - 결과 요약 임베드 전송
    """
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

        # ensure FA team exists
        fa_ref = team_doc_ref("FA")
        fa_ref.set({"name": "FA", "created_at": now_iso()}, merge=True)

        for nick_norm in roster:
            try:
                # nick_norm stored is normalized (lowercase). player_doc_ref will normalize again, safe.
                p_ref = player_doc_ref(nick_norm)
                p_doc = p_ref.get()
                if not p_doc.exists:
                    errors.append(f"{nick_norm}: 선수 데이터 없음")
                    continue
                # update player -> team 'FA'
                p_ref.update({"team": "FA", "updated_at": now_iso()})
                # add to FA roster
                fa_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick_norm)])})
                moved.append(nick_norm)
            except Exception as e:
                errors.append(f"{nick_norm}: {e}")

        # delete the team document
        t_ref.delete()

        # compose embed summary
        embed = discord.Embed(title="팀 삭제 완료", description=f"팀 `{team_norm}` 을(를 삭제하고 해당 선수들을 FA로 이동했습니다.", timestamp=datetime.now(timezone.utc))
        embed.add_field(name="원팀", value=team_norm, inline=False)
        embed.add_field(name="이동(FA) 수", value=str(len(moved)), inline=True)
        embed.add_field(name="오류 수", value=str(len(errors)), inline=True)
        if moved:
            embed.add_field(name="이동된 선수 (최대 50)", value=", ".join(moved[:50]), inline=False)
        if errors:
            embed.add_field(name="오류 예시 (최대 10)", value="\n".join(errors[:10]), inline=False)
            embed.colour = discord.Color.red()
        else:
            embed.colour = discord.Color.green()
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 팀 삭제 중 오류 발생: {e}")

# ---------- 나머지 명령들 (수정/닉변/삭제/구종삭제/팀/목록/트레이드/웨이버/방출/기록) ----------
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
        await ctx.send(f"**{team_norm}** — 로스터 ({len(roster)}):\n" + ", ".join(roster[:50]))
    else:
        await ctx.send(f"**{team_norm}** — 로스터가 비어있습니다.")

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

# ---------- 기록 관련 ----------
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
