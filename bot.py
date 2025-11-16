# bot.py
"""
Discord + Firebase (Firestore) Baseball Player Manager Bot
- Python 3.8+
- discord.py 기반 명령형 봇
- Firestore collections: players, teams, records, aliases
- 주요 변경:
  - 블록(개행) 기반 선수 입력 지원 (닉네임(팔각도) + 다음줄: 구종 ...)
  - 팔각도(폼) 없어도 등록 가능
  - 닉변 이전 이름으로 입력해도 현재 닉네임으로 등록되는 alias 매핑 지원
  - 기존 기능(마인크래프트 검증, Minotar 스킨, 임베드 요약, 파일가져오기 중복모드 등) 유지
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

# dotenv (개발 환경)
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
    닉네임 또는 이전 닉네임(aliases)에 대해 실제(현재) 닉네임 문서 ID를 반환.
    - aliases 컬렉션에 normalized old nick의 doc이 있으면 그 'current' 값을 사용.
    - 없으면 입력 닉네임(normalized)을 그대로 반환.
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
        # DB 문제나 기타 경우 원래 닉 그대로 반환
        return normalize_nick(nick)

# ---------- Firestore 참조 헬퍼 (resolve_nick 사용) ----------
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
`{BOT}등록` - 여러 블록(개행)으로 붙여넣어 등록. (예: 닉네임 (폼) \\n 구종...)
`{BOT}추가 nick|이름|팀|포지션|구종1,구종2|폼` - 한 명 추가

**파일 가져오기**
`{BOT}가져오기파일 [팀명] [모드]` - 첨부된 .txt/.csv 파일을 블록으로 읽어 등록
  - [팀명]은 다단어 허용
  - [모드]: 빈칸 또는 'skip'/'건너뛰기' (기본) 또는 '덮어쓰기'/'overwrite'

**수정/닉변/삭제/영입/이적**
`{BOT}수정 닉네임 필드 새값`  
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
    """
    텍스트를 빈 줄(하나 이상) 기준으로 블록으로 나눔.
    각 블록은 여러 라인(리스트)로 반환.
    """
    raw_blocks = re.split(r'\n\s*\n', text.strip(), flags=re.MULTILINE)
    blocks = []
    for b in raw_blocks:
        lines = [line.strip() for line in b.splitlines() if line.strip()]
        if lines:
            blocks.append(lines)
    return blocks

def parse_pitch_line(pitch_line: str) -> List[str]:
    """
    구종 라인 파싱:
    - 토큰을 공백으로 분리
    - '커브(20)' 같은 형식 유지
    - '포심' 과 같이 숫자 없는 경우도 허용 (그대로 '포심')
    """
    tokens = [t.strip() for t in pitch_line.split() if t.strip()]
    out = []
    for tok in tokens:
        # allow multiple formats like 포심(40), 포심, 포심(40), 스플리터(30)
        if re.match(r'^[^\s()]+\( ?\d+ ?\)$', tok):
            out.append(tok.replace(" ", ""))
        else:
            out.append(tok)
    return out

def parse_block_to_player(block_lines: List[str]):
    """
    블록(2개 이상의 라인 또는 1라인)을 선수 데이터로 변환.
    반환: dict with keys: nickname, name, team, position, pitch_types(list), form
    """
    # 기본값
    nickname = ""
    name = ""
    team = "Free"
    position = "N/A"
    pitch_types = []
    form = ""

    # 1) 파이프 형식 단일 라인 처리 (nick|이름|팀|포지션|구종...|폼)
    if len(block_lines) == 1 and '|' in block_lines[0]:
        parts = block_lines[0].split("|")
        if len(parts) >= 1:
            nickname = parts[0].strip()
        if len(parts) >= 2:
            name = parts[1].strip()
        if len(parts) >= 3 and parts[2].strip():
            team = normalize_team_name(parts[2].strip())
        if len(parts) >= 4 and parts[3].strip():
            position = parts[3].strip()
        if len(parts) >= 5 and parts[4].strip():
            pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
        if len(parts) >= 6 and parts[5].strip():
            form = parts[5].strip()
        if not name:
            name = nickname
        return {"nickname": nickname, "name": name, "team": team, "position": position, "pitch_types": pitch_types, "form": form}

    # 2) 라인 기반: 첫 라인에 '닉네임 (폼) [팀]' 형태 가능, 이후 라인들은 구종
    first = block_lines[0]
    # 닉네임 추출 (첫 단어 또는 괄호 처리)
    # form: (언더핸드) 같은 괄호
    form_match = re.search(r'\(([^)]*)\)', first)
    team_match = re.search(r'\[([^\]]*)\]', first)
    # nickname is first token (until space) or entire line before '(' or '['
    nick_token = first.split()[0] if first.split() else first
    # if first contains '[' or '(' which might be attached to nickname, do:
    # try extracting nickname via regex: ^([^\s\(\[]+)
    m = re.match(r'^([^\s\(\[]+)', first)
    if m:
        nickname = m.group(1).strip()
    else:
        nickname = first.strip()

    if form_match:
        form = form_match.group(1).strip()
    if team_match:
        team = normalize_team_name(team_match.group(1).strip())

    # name default to nickname
    name = nickname

    # collect pitch lines (all remaining lines concatenated)
    if len(block_lines) >= 2:
        pitch_text = " ".join(block_lines[1:])
        pitch_types = parse_pitch_line(pitch_text)
    else:
        # 간혹 두번째 라인이 없이 한 라인만 있는 경우, 첫라인 안에 구종이 붙어있을 수도 있음.
        # 예: "nick 포심(40) 슬라이더(20)" 형태
        rest = first[len(nickname):].strip()
        if rest:
            # remove form/team parts if present
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

# ---------- 단일 추가 (파이프 형식) ----------
@bot.command(name="추가")
async def add_one_cmd(ctx, *, payload: str):
    if not await ensure_db_or_warn(ctx): return
    parts = payload.split("|")
    if len(parts) < 4:
        await ctx.send("❌ 형식 오류. 예시: `!추가 nick|이름|팀|포지션|구종1,구종2|폼`")
        return
    raw_nick = parts[0].strip()
    # resolve alias -> canonical
    target_norm = resolve_nick(raw_nick)
    nick_to_use = target_norm  # doc id (normalized)
    name = parts[1].strip() or raw_nick
    team = normalize_team_name(parts[2].strip())
    position = parts[3].strip()
    pitch_types = []
    form = ""
    if len(parts) >= 5 and parts[4].strip():
        pitch_types = [p.strip() for p in parts[4].split(",") if p.strip()]
    if len(parts) >= 6:
        form = parts[5].strip()

    if VERIFY_MC:
        valid = await is_mc_username(raw_nick)
        if not valid:
            await ctx.send(f"❌ `{raw_nick}` 는(은) 유효한 마인크래프트 계정명이 아닙니다. 등록이 취소되었습니다.")
            return

    author = ctx.author
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", ""))
    }

    doc_ref = db.collection("players").document(nick_to_use)
    data = {
        "nickname": raw_nick if nick_to_use == normalize_nick(raw_nick) else nick_to_use,
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
            t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick_to_use)])})
        embed = make_player_embed(data, include_body=True)
        embed.colour = discord.Color.green()
        await ctx.send(content="✅ 선수 추가 완료", embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 추가 실패: {e}")

# ---------- 블록(개행) 기반 대량 등록 ----------
@bot.command(name="등록")
async def bulk_register_cmd(ctx, *, bulk_text: str = None):
    """
    본문에 여러 블록(빈줄로 구분)으로 붙여넣기 가능.
    블록 예시:
      Ciel_Tempest (언더핸드)
      포심(20) 슬라이더(40) 너클커브(40)

    또는 파이프 형식: nick|이름|팀|포지션|구종1,구종2|폼
    """
    if not await ensure_db_or_warn(ctx): return
    if not bulk_text:
        await ctx.send("❌ 본문에 등록할 선수 정보를 여러 블록으로 붙여넣어 주세요.")
        return

    author = ctx.author
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", ""))
    }

    blocks = split_into_blocks(bulk_text)
    added = []
    errors = []
    for i, block in enumerate(blocks, start=1):
        try:
            p = parse_block_to_player(block)
            raw_nick = p["nickname"]
            # resolve alias -> canonical
            target_norm = resolve_nick(raw_nick)
            nick_docid = target_norm
            # validate MC name
            if VERIFY_MC:
                valid = await is_mc_username(raw_nick)
                await asyncio.sleep(0.08)
                if not valid:
                    errors.append(f"블록 {i}: `{raw_nick}` 은(는) 마인크래프트 계정 아님")
                    continue
            # prepare data
            data = {
                "nickname": raw_nick if nick_docid == normalize_nick(raw_nick) else nick_docid,
                "name": p.get("name", raw_nick),
                "team": p.get("team","Free") or "Free",
                "position": p.get("position","N/A"),
                "pitch_types": p.get("pitch_types", []),
                "form": p.get("form",""),
                "extra": {},
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "created_by": created_by
            }
            doc_ref = db.collection("players").document(nick_docid)
            doc_ref.set(data)
            # team roster update
            if data["team"]:
                t_ref = team_doc_ref(data["team"])
                t_ref.set({"name": data["team"], "created_at": now_iso()}, merge=True)
                t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick_docid)])})
            added.append(nick_docid)
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
    created_by = {
        "id": getattr(author, "id", None),
        "name": getattr(author, "name", ""),
        "discriminator": getattr(author, "discriminator", None),
        "display_name": getattr(author, "display_name", getattr(author, "name", ""))
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

            # team override
            team = team_override if team_override else p.get("team","Free")

            # MC name check
            if VERIFY_MC:
                valid = await is_mc_username(raw_nick)
                await asyncio.sleep(0.08)
                if not valid:
                    errors.append(f"블록 {i}: `{raw_nick}` 은(는) 마인크래프트 계정 아님")
                    continue

            data_obj = {
                "nickname": raw_nick if target_norm == normalize_nick(raw_nick) else target_norm,
                "name": p.get("name", raw_nick),
                "team": team or "Free",
                "position": p.get("position","N/A"),
                "pitch_types": p.get("pitch_types", []),
                "form": p.get("form",""),
                "extra": {},
                "created_at": created_at_val,
                "updated_at": now_iso(),
                "created_by": created_by if not exists else (old.get("created_by") if old and old.get("created_by") else created_by)
            }

            doc_ref.set(data_obj)
            if team:
                t_ref = team_doc_ref(team)
                t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
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
        # create new document, keep the data
        new_ref.set(data)
        # delete old document
        old_ref.delete()

        # update team roster references
        team = data.get("team")
        if team:
            t_ref = team_doc_ref(team)
            t_ref.update({"roster": firestore.ArrayRemove([normalize_nick(oldnick)])})
            t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(newnick)])})

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

# ---------- 나머지 기존 명령들 (이적/영입/삭제/구종삭제/팀/팀삭제/목록/트레이드/웨이버/방출/기록) ----------
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
        transfer_by = {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", ""),
            "discriminator": getattr(author, "discriminator", None),
            "display_name": getattr(author, "display_name", getattr(author, "name", ""))
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

        embed = discord.Embed(title="선수 이적 완료", timestamp=datetime.now(timezone.utc))
        embed.add_field(name="선수", value=p_ref.id, inline=True)
        embed.add_field(name="이전팀", value=oldteam or "Free", inline=True)
        embed.add_field(name="이적팀", value=newteam_norm, inline=True)
        embed.add_field(name="이적자", value=f"{transfer_by.get('display_name')} (ID: {transfer_by.get('id')})", inline=False)
        avatar_url, body_url = safe_avatar_urls(p_ref.id)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.colour = discord.Color.gold()
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
        updated_by = {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", ""),
            "discriminator": getattr(author, "discriminator", None),
            "display_name": getattr(author, "display_name", getattr(author, "name", ""))
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

        embed = discord.Embed(title="선수 영입 완료", timestamp=datetime.now(timezone.utc))
        embed.add_field(name="선수", value=p_ref.id, inline=True)
        embed.add_field(name="이전팀", value=oldteam or "Free", inline=True)
        embed.add_field(name="영입팀", value=newteam, inline=True)
        embed.add_field(name="영입자", value=f"{updated_by.get('display_name')} (ID: {updated_by.get('id')})", inline=False)
        avatar_url, body_url = safe_avatar_urls(p_ref.id)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.colour = discord.Color.blue()
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
        embed = discord.Embed(title="팀 삭제 완료", description=f"팀 `{team_norm}` 을(를) 삭제하고 해당 선수들을 FA로 이동했습니다.", timestamp=datetime.now(timezone.utc))
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

# 목록/삭제/웨이버/방출/트레이드/기록 등 (기존 구현 유지 - 생략없이 포함)
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
            team_doc_ref(team).update({"roster": firestore.ArrayRemove([normalize_nick(ref.id)])})
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
            t_ref = team_doc_ref(team)
            t_ref.update({"roster": firestore.ArrayRemove([normalize_nick(ref.id)])})
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
