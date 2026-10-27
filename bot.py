# bot.py
"""
Discord + Firebase (Firestore) Baseball Player Manager Bot
- Python 3.11+ (AutoMod Cog)
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
INTENTS.guilds = True
INTENTS.guild_messages = True
INTENTS.message_content = True
INTENTS.members = True

# 마인크래프트 닉네임 검증을 끄고 싶으면 VERIFY_MC=false 환경변수 설정
VERIFY_MC = os.getenv("VERIFY_MC", "true").lower() not in ("0", "false", "no", "off")

# 구종에 숫자 없을때 기본 수치
raw_pitch_power = os.getenv("DEFAULT_PITCH_POWER", "D")
try:
    DEFAULT_PITCH_POWER = int(raw_pitch_power)
except ValueError:
    DEFAULT_PITCH_POWER = raw_pitch_power

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=INTENTS, help_command=None)

# ---------- Firebase 초기화 ----------
def init_firebase():
    # 이름 있는 AutoMod 앱의 존재와 기본 앱의 존재를 구분합니다.
    try:
        default_app = firebase_admin.get_app()
    except ValueError:
        default_app = None
    if default_app is not None:
        return firestore.client(app=default_app)

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

def int_to_innings(outs: int) -> float:
    """아웃카운트를 이닝 소수점 표기법으로 변환 (ex: 5아웃 -> 1.2)"""
    return (outs // 3) + (outs % 3) / 10

def innings_to_outs(inning_val: float) -> int:
    """이닝 소수점 표기법을 총 아웃카운트로 변환 (ex: 1.2 -> 5아웃)"""
    try:
        val = float(inning_val)
        inn = int(val)
        frac = round((val - inn) * 10)
        return inn * 3 + frac
    except:
        return 0

def update_google_sheet(match_type: str, sheet_name: str, records: list, is_pitcher=False):
    if not gc: return False
    try:
        spreadsheet_id = SPREADSHEET_MAPPING.get(match_type)
        doc = gc.open_by_key(spreadsheet_id)
        
        try:
            worksheet = doc.worksheet(sheet_name)
        except:
            worksheet = doc.get_worksheet(0)
            
        all_values = worksheet.get_all_values()
        if not all_values: return False
        
        header = [h.strip() for h in all_values[0]]
        if "선수명" not in header: return False
        name_col_idx = header.index("선수명")
        
        for row_data in records:
            player_name = row_data.get("선수명").strip()
            player_row_idx = None
            first_empty_row = None
            
            # 1. 기존 선수 행 찾기 또는 빈 행 탐색
            for idx, row in enumerate(all_values):
                if idx == 0: continue
                current_name = row[name_col_idx].strip() if len(row) > name_col_idx else ""
                
                if current_name == player_name:
                    player_row_idx = idx + 1
                    break
                if not current_name and first_empty_row is None:
                    first_empty_row = idx + 1
            
            # 2. 업데이트 혹은 삽입 위치 확정
            if player_row_idx:
                current_row_values = all_values[player_row_idx - 1]
                for key, val in row_data.items():
                    if key == "선수명" or key not in header: continue
                    col_idx = header.index(key)
                    
                    # 기존 값 가졌오기
                    orig_str = current_row_values[col_idx] if col_idx < len(current_row_values) else "0"
                    
                    if is_pitcher and key == "이닝":
                        # 야구 이닝 특수 누적법 적용 (아웃카운트 변환 후 합산)
                        total_outs = innings_to_outs(orig_str) + innings_to_outs(val)
                        new_val = int_to_innings(total_outs)
                    else:
                        # 일반 수치형 데이터 합산
                        try:
                            new_val = (float(orig_str) if orig_str else 0) + float(val)
                            if new_val.is_integer(): new_val = int(new_val)
                        except:
                            new_val = val
                            
                    worksheet.update_cell(player_row_idx, col_idx + 1, new_val)
            else:
                # 선수가 등록 안 되어 있다면 양식의 빈 곳에 채우거나 append
                target_row = first_empty_row if first_empty_row else len(all_values) + 1
                worksheet.update_cell(target_row, name_col_idx + 1, player_name)
                
                for key, val in row_data.items():
                    if key in header and key != "선수명":
                        worksheet.update_cell(target_row, header.index(key) + 1, val)
                        
        return True
    except Exception as e:
        print(f"구글 시트 업데이트 에러: {e}")
        return False

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
    return bool(re.search(r'\(\s*\w+\s*\)$', pitch))

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
    구종 라인 파싱 & 정규화 (개선된 토크나이저)
    - 쉼표(,) 또는 공백으로 분리
    - 구종에 이미 숫자(예: 포심(40))가 있으면 그대로 사용
    - 숫자 없으면 DEFAULT_PITCH_POWER 부여
    """
    if not pitch_line:
        return []
    # 구종토큰: "포심(40)", "슬라이더(40)", "포심", "커브( 30 )" 등 잡아냄
    tokens = re.findall(r'([^\s,]+(?:\(\s*\w+\s*\))?|[^\s,]+)', pitch_line.strip())
    out = []
    for tok in tokens:
        tok = tok.strip().rstrip(",")
        if not tok:
            continue
        norm = normalize_pitch_token(tok)
        if norm:
            out.append(norm)
    return out

def parse_block_to_player(block_lines: List[str]):
    """
    블록(1+ 라인)을 선수 데이터로 변환.
    - nickname, name, team (or None), position, pitch_types(list), form 반환
    - 첫 줄에서 '닉네임 (폼) [팀] ...' 처럼 닉네임 바로 뒤의 (폼)과 [팀]만 추출하고
      그 이후의 텍스트(예: 구종(40) ...)는 제거하지 않고 pitch 파서에 넘깁니다.
    """
    nickname = ""
    name = ""
    team = None  # None => 미기재 (호출부가 기존값 유지 결정)
    position = "N/A"
    pitch_types = []
    form = ""

    # 파이프 형식(한 줄) 우선 처리(기존 로직 유지)
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

    # --- 첫 줄에서 nickname / (form) / [team] 만 캡처, 나머지(구종)는 보존 ---
    first = block_lines[0]
    # 정규식: 닉네임, 선택적 (폼), 선택적 [팀], 그리고 나머지(rest)
    m = re.match(r'^\s*([^\s\(\[]+)(?:\s*\(([^)]*)\))?(?:\s*\[([^\]]*)\])?(.*)$', first)
    if m:
        nickname = m.group(1).strip()
        form = (m.group(2) or "").strip()
        team = normalize_team_name(m.group(3).strip()) if m.group(3) else None
        rest = (m.group(4) or "").strip()
    else:
        # 안전망: 기존 방식 유지
        m2 = re.match(r'^([^\s\(\[]+)', first)
        if m2:
            nickname = m2.group(1).strip()
            rest = first[len(nickname):].strip()
        else:
            nickname = first.strip()
            rest = ""

    name = nickname

    # pitch lines: 두 번째 라인 이후(또는 첫 줄 rest)에 있는 구종 텍스트 합치기
    pitch_text_parts = []
    if rest:
        pitch_text_parts.append(rest)
    if len(block_lines) >= 2:
        # 나머지 라인들은 구종이나 추가 정보로 간주
        pitch_text_parts.append(" ".join(block_lines[1:]).strip())
    pitch_text = " ".join([p for p in pitch_text_parts if p]).strip()

    if pitch_text:
        pitch_types = parse_pitch_line(pitch_text)
    else:
        pitch_types = []

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

# =========================
# AutoMod 명령 등록/검증 (Firestore·OCR와 독립)
# =========================
AUTOMOD_ROOT_NAMES = (
    "자동관리", "금칙어", "허용어", "예외", "위반", "필터테스트", "이미지테스트",
)
AUTOMOD_SLASH_PATCH_VERSION = "2026-10-27.registration-2"


class AutoModRegistrationError(RuntimeError):
    pass


def _slash_error(error):
    """토큰, 인증 JSON, HTTP 원문을 출력하지 않는 진단."""
    cause = getattr(error, "original", error)
    status = getattr(cause, "status", None)
    code = getattr(cause, "code", None)
    if isinstance(cause, AutoModRegistrationError):
        return str(cause)
    return f"{type(cause).__name__} (HTTP={status}, Discord code={code})"


def _command_key(payload):
    return int(payload.get("type", 1)), payload["name"]


def _command_schema(payload, *, guild):
    """Discord 응답의 ID/버전과 생략 가능한 기본값을 제외한 명령 스키마."""
    def option(item):
        out = {key: value for key, value in item.items()
               if key not in {"options", "choices", "name_localizations", "description_localizations"}
               and value is not None}
        out["required"] = bool(out.get("required", False))
        out["autocomplete"] = bool(out.get("autocomplete", False))
        out["options"] = [option(child) for child in item.get("options", [])]
        out["choices"] = [{**choice, "name_localizations": choice.get("name_localizations") or {}}
                          for choice in item.get("choices", [])]
        out["name_localizations"] = item.get("name_localizations") or {}
        out["description_localizations"] = item.get("description_localizations") or {}
        if out.get("channel_types"):
            # This is a membership restriction, not an ordered parameter list.
            # Thread unions can serialize [10, 12, 11]; the API may return [10, 11, 12].
            out["channel_types"] = sorted({int(value) for value in out["channel_types"]})
        else:
            out.pop("channel_types", None)
        return out

    result = {
        "type": int(payload.get("type", 1)), "name": payload["name"],
        "description": payload.get("description") or "",
        "options": [option(item) for item in payload.get("options", [])],
        "name_localizations": payload.get("name_localizations") or {},
        "description_localizations": payload.get("description_localizations") or {},
        "default_member_permissions": (str(payload["default_member_permissions"])
                                       if payload.get("default_member_permissions") is not None else None),
        "nsfw": bool(payload.get("nsfw", False)),
    }
    # Global-only fields: compare explicit restrictions without assuming a
    # guild permission grant. Guild endpoints do not accept these fields.
    if not guild:
        if payload.get("contexts") is not None:
            result["contexts"] = sorted(payload["contexts"])
        if payload.get("integration_types") is not None:
            result["integration_types"] = sorted(payload["integration_types"])
    return result


def _matches_command(expected, actual, *, guild):
    if actual is None:
        return False
    left = _command_schema(expected, guild=guild)
    right = _command_schema(actual, guild=guild)
    # For non-AutoMod global commands, preserve application-level defaults
    # when the local definition does not explicitly constrain them.
    for key in ("contexts", "integration_types"):
        if key not in left:
            right.pop(key, None)
    return left == right



def _command_differences(expected, actual, *, guild):
    """Only command definition paths, not tokens, HTTP headers or user content."""
    if actual is None:
        return ["command: Discord 응답에 없음"]
    left = _command_schema(expected, guild=guild)
    right = _command_schema(actual, guild=guild)
    for key in ("contexts", "integration_types"):
        if key not in left:
            right.pop(key, None)
    differences = []

    def walk(a, b, path):
        if len(differences) >= 8 or a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    differences.append(f"{path}.{key}: 필드 누락 또는 추가")
                else:
                    walk(a[key], b[key], f"{path}.{key}")
                if len(differences) >= 8:
                    return
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                differences.append(f"{path}: 개수 {len(a)} != {len(b)}")
            for index, (av, bv) in enumerate(zip(a, b)):
                walk(av, bv, f"{path}[{index}]")
        else:
            av = json.dumps(a, ensure_ascii=False)[:100]
            bv = json.dumps(b, ensure_ascii=False)[:100]
            differences.append(f"{path}: expected={av}, actual={bv}")
    walk(left, right, expected.get("name", "command"))
    return differences[:8]


def _normalise_command_payload(payload):
    """Send stable channel-type sets without reordering options or choices."""
    import copy
    value = copy.deepcopy(payload)
    def walk(item):
        if item.get("channel_types"):
            item["channel_types"] = sorted({int(v) for v in item["channel_types"]})
        for child in item.get("options", []):
            walk(child)
    walk(value)
    return value


class AutoModSlashRegistrar:
    """Non-destructive command deployment using Discord's documented POST upsert.

    Reuses discord.py's HTTP transport (auth/rate limits) and command serializer.
    No PUT bulk overwrite, DELETE, clear_commands, or DB/OCR calls are used.
    Guild AutoMod commands dispatch through CommandTree.fallback_to_global;
    no extra alias is retained after a Cog is unloaded.
    """
    def __init__(self, client):
        self.bot = client
        self.lock = asyncio.Lock()
        self.last_results = {}

    def automod_commands(self):
        cog = self.bot.get_cog("AutoMod")
        if cog is None:
            raise AutoModRegistrationError("AutoMod Cog 미등록: 먼저 automod.py 로드 실패 로그를 확인하세요.")
        owned = {command.name: command for command in cog.get_app_commands()}
        missing = [name for name in AUTOMOD_ROOT_NAMES if name not in owned]
        if missing:
            raise AutoModRegistrationError("AutoMod 명령 정의 누락: " + ", ".join(missing))
        result = []
        for name in AUTOMOD_ROOT_NAMES:
            command = owned[name]
            current = self.bot.tree.get_command(name)
            if current is None:
                # Safe repair of this Cog's missing LOCAL registration only.
                self.bot.tree.add_command(command)
            elif current is not command:
                raise AutoModRegistrationError(f"/{name} 이름을 다른 로컬 명령이 사용 중입니다. 덮어쓰지 않았습니다.")
            result.append(command)
        return result

    async def _payload(self, command, *, guild):
        translator = self.bot.tree.translator
        if translator is None:
            payload = command.to_dict(self.bot.tree)
        else:
            payload = await command.get_translated_payload(self.bot.tree, translator)
        payload = dict(payload)
        if guild:
            for key in ("dm_permission", "contexts", "integration_types"):
                payload.pop(key, None)
        elif command.name in AUTOMOD_ROOT_NAMES:
            # AutoMod is a server-installed app command, never a user/DM tool.
            payload.pop("dm_permission", None)
            payload["contexts"] = [0]
            payload["integration_types"] = [0]
        return _normalise_command_payload(payload)

    async def _api(self, method, *, guild_id=None, payload=None):
        app_id = self.bot.application_id
        if app_id is None:
            raise AutoModRegistrationError("Discord application_id가 없습니다. 봇 로그인 이후 실행하세요.")
        params = {"application_id": int(app_id)}
        path = "/applications/{application_id}/commands"
        if guild_id is not None:
            params["guild_id"] = int(guild_id)
            path = "/applications/{application_id}/guilds/{guild_id}/commands"
        # Internal transport only; endpoint/body follow the official Discord API.
        from discord.http import Route
        route = Route(method, path, **params)
        if payload is None:
            return await self.bot.http.request(route)
        return await self.bot.http.request(route, json=payload)

    async def publish(self, *, guild_id=None):
        """Global: all loaded commands. Guild: only seven AutoMod roots."""
        async with self.lock:
            automod = self.automod_commands()
            guild = guild_id is not None
            if guild:
                if not self.bot.tree.fallback_to_global:
                    raise AutoModRegistrationError("CommandTree.fallback_to_global=True가 필요합니다.")
                target = discord.Object(id=int(guild_id))
                for command in automod:
                    scoped = self.bot.tree.get_command(command.name, guild=target)
                    if scoped is not None and scoped is not command:
                        raise AutoModRegistrationError(
                            f"이 서버의 /{command.name} 로컬 명령이 다른 Cog와 충돌합니다. 덮어쓰지 않았습니다.")
                selected = automod
            else:
                selected = list(self.bot.tree.get_commands())
            desired = [await self._payload(command, guild=guild) for command in selected]
            if not desired:
                raise AutoModRegistrationError("명령 트리가 비어 있어 등록하지 않았습니다.")
            control = next((p for p in desired if p["name"] == "자동관리"), None)
            children = {option["name"] for option in (control or {}).get("options", [])}
            if not {"연결점검", "저장상태", "상태"} <= children:
                raise AutoModRegistrationError("/자동관리 하위 진단 명령이 누락되어 등록을 중단했습니다.")
            before = await self._api("GET", guild_id=guild_id)
            known = {_command_key(item): item for item in before}
            writes = 0
            for payload in desired:
                if _matches_command(payload, known.get(_command_key(payload)), guild=guild):
                    continue
                # Upsert one command only; unknown remote commands remain intact.
                await self._api("POST", guild_id=guild_id, payload=payload)
                writes += 1
            after = await self._api("GET", guild_id=guild_id)
            registered = {_command_key(item): item for item in after}
            mismatch = [p["name"] for p in desired
                        if not _matches_command(p, registered.get(_command_key(p)), guild=guild)]
            if mismatch:
                details = {p["name"]: _command_differences(
                    p, registered.get(_command_key(p)), guild=guild)
                    for p in desired if p["name"] in mismatch}
                present = sum((1, name) in registered for name in AUTOMOD_ROOT_NAMES)
                print("[AutoMod 명령 차이] " + json.dumps(details, ensure_ascii=False), flush=True)
                raise AutoModRegistrationError(
                    f"Discord 재조회 스키마 불일치: {', '.join(mismatch)} "
                    f"(명령 존재 {present}/7; Railway의 [AutoMod 명령 차이] 확인)")
            verified = {name: str(registered[(1, name)]["id"]) for name in AUTOMOD_ROOT_NAMES}
            result = {"application_id": self.bot.application_id, "guild_id": guild_id,
                      "verified": verified, "writes": writes, "checked": len(desired)}
            self.last_results[guild_id] = result
            label = f"guild={guild_id}" if guild else "global"
            print(f"[AutoMod 등록확인] {label} app={self.bot.application_id} "
                  f"자동관리 7/7 · 서버 재조회 검증 완료 · 변경 {writes}개", flush=True)
            print("[AutoMod 명령ID] " + json.dumps(verified, ensure_ascii=False), flush=True)
            return result

    async def inspect(self, guild_id):
        """Read-only remote registration check; no Firebase access or punishment."""
        cog = self.bot.get_cog("AutoMod")
        local = {command.name for command in self.bot.tree.get_commands()}
        result = {"application_id": self.bot.application_id, "guild_id": guild_id,
                  "cog_loaded": cog is not None,
                  "local_roots": [name for name in AUTOMOD_ROOT_NAMES if name in local]}
        for label, target in (("global", None), ("guild", guild_id)):
            try:
                rows = await self._api("GET", guild_id=target)
                result[label] = {
                    item["name"]: {"id": str(item["id"]), "children": [v["name"] for v in item.get("options", [])],
                                   "default_member_permissions": item.get("default_member_permissions")}
                    for item in rows if item.get("type", 1) == 1 and item["name"] in AUTOMOD_ROOT_NAMES
                }
            except Exception as exc:
                result[label] = {"error": _slash_error(exc)}
        return result


_automod_registrar = AutoModSlashRegistrar(bot)
_automod_registration_task = None


async def _automod_register_after_ready():
    # Never await wait_until_ready() directly inside setup_hook: Gateway has
    # not connected yet. This single tracked task is created by setup_hook.
    await bot.wait_until_ready()
    try:
        await asyncio.wait_for(_automod_registrar.publish(), timeout=180)
    except Exception as exc:
        print("[AutoMod 전역등록 실패] " + _slash_error(exc), flush=True)
    for guild in tuple(bot.guilds):
        try:
            await asyncio.wait_for(_automod_registrar.publish(guild_id=guild.id), timeout=120)
        except Exception as exc:
            print(f"[AutoMod 서버등록 실패] guild={guild.id} " + _slash_error(exc), flush=True)
    print(f"[AutoMod 등록작업 종료] 목록 미표시 시 서버 관리자: {BOT_PREFIX}자동관리점검", flush=True)


async def _automod_repair_permission(ctx):
    if ctx.guild is None:
        return False
    if ctx.author.id == ctx.guild.owner_id:
        return True
    try:
        member = await ctx.guild.fetch_member(ctx.author.id)
        roles = {role.id: role for role in await ctx.guild.fetch_roles()}
        return any(roles[role.id].permissions.administrator for role in member.roles if role.id in roles)
    except discord.HTTPException:
        return False


@bot.command(name="자동관리등록")
@commands.guild_only()
@commands.check(_automod_repair_permission)
@commands.cooldown(1, 60, commands.BucketType.guild)
async def automod_register_command(ctx):
    """슬래시 목록이 없어도 서버 소유자/Administrator가 현재 서버를 복구."""
    await ctx.send("현재 서버의 자동관리 명령을 등록·재조회합니다. 위반 횟수와 처벌은 변경하지 않습니다.",
                   allowed_mentions=discord.AllowedMentions.none())
    try:
        result = await asyncio.wait_for(_automod_registrar.publish(guild_id=ctx.guild.id), timeout=120)
        message = (f"자동관리 명령 7/7 등록 확인. 변경 {result['writes']}개.\n"
                   f"앱 ID: {result['application_id']} / 서버 ID: {ctx.guild.id}\n"
                   "Discord 명령 목록에서 /자동관리 연결점검을 선택하세요.\n"
                   "계속 안 보이면 서버 설정 → 연동의 명령 권한과 채널의 애플리케이션 명령어 사용 권한을 확인하세요.")
    except Exception as exc:
        message = "등록 완료를 확인하지 못했습니다: " + _slash_error(exc)
        print("[AutoMod 수동등록 실패] " + _slash_error(exc), flush=True)
    await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="자동관리점검")
@commands.guild_only()
@commands.check(_automod_repair_permission)
@commands.cooldown(1, 20, commands.BucketType.guild)
async def automod_registration_status_command(ctx):
    try:
        result = await asyncio.wait_for(_automod_registrar.inspect(ctx.guild.id), timeout=60)
        permissions = ctx.channel.permissions_for(ctx.author)
        text = (f"명령 등록 점검 ({AUTOMOD_SLASH_PATCH_VERSION})\n"
                f"앱 ID {bot.application_id} / 서버 ID {ctx.guild.id}\n"
                f"이 채널의 본인 애플리케이션 명령어 사용 권한: {permissions.use_application_commands}\n"
                "아래는 Discord 등록 정보이며 Firebase/OCR 정상 여부가 아닙니다.\n"
                + json.dumps(result, ensure_ascii=False, indent=2))
        if len(text) <= 1900:
            await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())
        else:
            import io
            await ctx.send("등록 점검 결과입니다. 인증 키·원문·위반 기록은 포함하지 않습니다.",
                           file=discord.File(io.BytesIO(text.encode("utf-8")), filename="automod-registration-status.txt"),
                           allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await ctx.send("등록 점검 실패: " + _slash_error(exc), allowed_mentions=discord.AllowedMentions.none())



@bot.command(name="자동관리저장점검")
@commands.guild_only()
@commands.check(_automod_repair_permission)
@commands.cooldown(1, 60, commands.BucketType.guild)
async def automod_storage_diagnostic_command(ctx):
    """Owner/Admin-only fallback; full diagnostics go to the requesting user's DM."""
    cog = bot.get_cog("AutoMod")
    if cog is None or not hasattr(cog, "diagnostic_report"):
        await ctx.send("이번 수정본 cogs/automod.py가 로딩되어야 합니다.",
                       allowed_mentions=discord.AllowedMentions.none())
        return
    await ctx.send("저장소를 읽기 전용으로 점검합니다. 상세 결과는 요청자 DM으로 보냅니다.",
                   allowed_mentions=discord.AllowedMentions.none())
    try:
        report = await asyncio.wait_for(cog.diagnostic_report(ctx.guild, ctx.author.id), timeout=150)
        import io
        await ctx.author.send(
            "AutoMod 저장/OCR 점검 결과입니다. 위반 기록이나 처벌을 만들지 않았습니다.",
            file=discord.File(io.BytesIO(report.encode("utf-8")), filename="automod-health.txt"),
            allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        await ctx.send("DM을 보낼 수 없습니다. 서버 DM 허용 후 재실행하거나 /자동관리 연결점검을 사용하세요.",
                       allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await ctx.send("점검 완료를 확인하지 못했습니다: " + _slash_error(exc),
                       allowed_mentions=discord.AllowedMentions.none())


async def _automod_register_joined_guild(guild):
    if _automod_registration_task is not None and not _automod_registration_task.done():
        # Serialize with startup so repeated creates are not submitted together.
        await asyncio.shield(_automod_registration_task)
    try:
        await asyncio.wait_for(_automod_registrar.publish(guild_id=guild.id), timeout=120)
    except Exception as exc:
        print(f"[AutoMod 새 서버 등록 실패] guild={guild.id} " + _slash_error(exc), flush=True)


bot.add_listener(_automod_register_joined_guild, "on_guild_join")


# =========================
# COG 자동 로드: 실행 위치 독립, 중복 방지, 파일별 오류 격리
# =========================
async def load_cogs():
    from pathlib import Path
    directory = Path(__file__).resolve().parent / "cogs"
    if not directory.is_dir():
        print("❌ cogs 폴더를 찾을 수 없습니다.", flush=True)
        return
    paths = [path for path in directory.glob("*.py") if not path.name.startswith("_")]
    for path in sorted(paths, key=lambda item: (item.stem == "automod", item.name.lower())):
        extension = f"cogs.{path.stem}"
        if extension in bot.extensions:
            continue
        try:
            await bot.load_extension(extension)
            print(f"✅ 성공적으로 로드됨: {path.name}", flush=True)
        except Exception as exc:
            print(f"❌ {path.name} 로드 실패: {_slash_error(exc)}", flush=True)


# ---------- 디스코드 기본 이벤트 ----------
@bot.event
async def setup_hook():
    global db, _automod_registration_task
    print(f"[AutoMod 패치] {AUTOMOD_SLASH_PATCH_VERSION}", flush=True)
    if db is None:
        try:
            db = await asyncio.to_thread(init_firebase)
        except Exception as exc:
            # Keep registration diagnostics alive; AutoMod never bypasses its
            # own Firebase/canonical-counter guards on authentication failure.
            print("Firebase 초기화 실패: " + _slash_error(exc), flush=True)
    await load_cogs()
    if _automod_registration_task is None:
        _automod_registration_task = asyncio.create_task(
            _automod_register_after_ready(), name="automod-register-once")
    print("✅ Cog 로딩 종료. 명령 등록 여부는 [AutoMod 등록확인] 로그로 확인하세요.", flush=True)


@bot.event
async def on_ready():
    # No extension loading or command registration on Gateway reconnect.
    print(f"✅ 봇 로그인 완료: {bot.user} / application_id={bot.application_id}")


# ---------- 에러 처리 ----------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("인자가 부족합니다. `!도움` 으로 사용법을 확인하세요.")
    elif isinstance(error, commands.CommandNotFound):
        return
    elif (ctx.command is not None and ctx.command.name in {"자동관리등록", "자동관리점검"}
          and isinstance(error, commands.CheckFailure)):
        await ctx.send("이 명령은 서버 소유자 또는 Administrator만 실행할 수 있습니다.",
                       allowed_mentions=discord.AllowedMentions.none())
    elif (ctx.command is not None and ctx.command.name in {"자동관리등록", "자동관리점검"}
          and isinstance(error, commands.CommandOnCooldown)):
        await ctx.send(f"명령 재실행까지 {error.retry_after:.0f}초가 남았습니다.",
                       allowed_mentions=discord.AllowedMentions.none())
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
        raise SystemExit
    
    bot.run(token)