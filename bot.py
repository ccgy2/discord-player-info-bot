import os
import io
import re
import json
import zipfile
from pathlib import Path
import asyncio
from typing import List, Tuple, Optional, Dict, Any

import discord
from discord.ext import commands
from dotenv import load_dotenv

# ───────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
UNASSIGNED_TEAM_DIR = os.getenv("UNASSIGNED_TEAM_DIR", "_unassigned").strip() or "_unassigned"
UNASSIGNED_ROLE_DIR = os.getenv("UNASSIGNED_ROLE_DIR", "_unassigned_role").strip() or "_unassigned_role"
COMMAND_PREFIX = (os.getenv("COMMAND_PREFIX", "!") or "!").strip()
CASE_INSENSITIVE = os.getenv("CASE_INSENSITIVE", "true").lower() == "true"

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN이 .env에 필요합니다.")

# ───────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

DATA_LOCK = asyncio.Lock()
SAFE_CHAR_RE = re.compile(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_\- ]")

# ───────────────────────────────────────
# Path helpers
def safe_name(txt: str) -> str:
    return SAFE_CHAR_RE.sub("", txt).strip().replace(" ", "_") or "_unknown"

def team_dir(team: Optional[str]) -> Path:
    return DATA_DIR / (safe_name(team or UNASSIGNED_TEAM_DIR))

def role_dir(team: Optional[str], role: Optional[str]) -> Path:
    return team_dir(team) / (safe_name(role or UNASSIGNED_ROLE_DIR))

def player_card_path(nick: str, team: Optional[str], role: Optional[str]) -> Path:
    return role_dir(team, role) / f"{safe_name(nick)}.txt"

def player_record_path(nick: str, team: Optional[str], role: Optional[str]) -> Path:
    return role_dir(team, role) / "record" / f"{safe_name(nick)}.json"

def ensure_dirs():
    (DATA_DIR / UNASSIGNED_TEAM_DIR / UNASSIGNED_ROLE_DIR).mkdir(parents=True, exist_ok=True)

# ───────────────────────────────────────
# Parsing helpers
def parse_pitch_line(line: str) -> List[Tuple[str, Optional[str]]]:
    """'포심(40) 슬라이더(20) 커터' -> [("포심","40"),("슬라이더","20"),("커터",None)]"""
    items = []
    for raw in re.split(r"[,\s]+", line.strip()):
        if not raw:
            continue
        m = re.match(r"(.+?)\(([^)]+)\)", raw)
        if m:
            items.append((m.group(1).strip(), m.group(2).strip()))
        else:
            items.append((raw.strip(), None))
    return items

def serialize_player(nick: str, arm: str, pitches: List[Tuple[str, Optional[str]]], team: str, role: str) -> str:
    lines = [f"{nick} ({arm})" if arm else nick]
    if pitches:
        lines.append(", ".join([f"{n}({s})" if s else n for n, s in pitches]))
    if team:
        lines.append(f"팀: {team}")
    if role:
        lines.append(f"포지션: {role}")
    return "\n".join(lines).rstrip() + "\n"

def parse_player_file(text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("빈 파일")
    nick, arm = lines[0], ""
    m = re.match(r"(.+?)\(([^)]+)\)", lines[0])
    if m:
        nick, arm = m.group(1).strip(), m.group(2).strip()
    pitches, team, role = [], "", ""
    for l in lines[1:]:
        if l.startswith("팀:"):
            team = l.split(":", 1)[1].strip()
        elif l.startswith("포지션:"):
            role = l.split(":", 1)[1].strip()
        else:
            pitches += parse_pitch_line(l)
    return {"display_name": nick, "arm_angle": arm, "team": team, "role": role, "pitches": pitches}

def write_player(nick: str, arm: str, pitches: List[Tuple[str, Optional[str]]], team: str, role: str, old_path: Optional[Path] = None):
    dest = player_card_path(nick, team, role)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(serialize_player(nick, arm, pitches, team, role), encoding="utf-8")
    (dest.parent / "record").mkdir(parents=True, exist_ok=True)
    if old_path and old_path.resolve() != dest.resolve():
        try:
            old_path.unlink(missing_ok=True)
        except:
            pass

def find_player(nick: str) -> Optional[Path]:
    key = nick.lower() if CASE_INSENSITIVE else nick
    for p in DATA_DIR.rglob("*.txt"):
        try:
            d = parse_player_file(p.read_text(encoding="utf-8"))
            name = d["display_name"].lower() if CASE_INSENSITIVE else d["display_name"]
            if name == key:
                return p
        except:
            continue
    return None

def pitch_str_from_list(pitches: List[Tuple[str, Optional[str]]]) -> str:
    return " ".join([f"{n}({s})" if s else n for n, s in pitches]) if pitches else "-"

# ───────────────────────────────────────
# Freeform import (파일 내부 헤더)
def parse_freeform(text: str) -> List[Tuple[str, str, List[Tuple[str, Optional[str]]], str, str]]:
    blocks = []
    cur, cur_team, cur_role = [], None, None
    players = []
    for ln in text.splitlines():
        if not ln.strip():
            if cur:
                blocks.append(cur)
                cur = []
            continue
        if len(ln.strip().split()) == 1 and "(" not in ln:
            word = ln.strip()
            if word in ["투수", "타자"]:
                cur_role = word
            else:
                cur_team = word
            continue
        cur.append(ln.strip())
    if cur:
        blocks.append(cur)
    for b in blocks:
        first = b[0]
        m = re.match(r"(.+?)\(([^)]+)\)", first)
        if m:
            nick, arm = m.group(1).strip(), m.group(2).strip()
        else:
            nick, arm = first.strip(), ""
        pitches = parse_pitch_line(" ".join(b[1:])) if len(b) > 1 else []
        players.append((nick, arm, pitches, cur_team or UNASSIGNED_TEAM_DIR, cur_role or UNASSIGNED_ROLE_DIR))
    return players

# ───────────────────────────────────────
# Records & Stats
def load_record(nick: str, team: str, role: str) -> Dict[str, Any]:
    rp = player_record_path(nick, team, role)
    if not rp.exists():
        return {"type": "batter" if role == "타자" else "pitcher", "totals": {}, "games": []}
    try:
        return json.loads(rp.read_text(encoding="utf-8"))
    except:
        return {"type": "batter" if role == "타자" else "pitcher", "totals": {}, "games": []}

def save_record(nick: str, team: str, role: str, rec: Dict[str, Any]):
    rp = player_record_path(nick, team, role)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

def calc_batter_stats(t: Dict[str, float]) -> Dict[str, float]:
    AB = t.get("AB", 0.0); H = t.get("H", 0.0)
    _2B = t.get("2B", 0.0); _3B = t.get("3B", 0.0); HR = t.get("HR", 0.0)
    BB = t.get("BB", 0.0); HBP = t.get("HBP", 0.0); SF = t.get("SF", 0.0)
    singles = max(H - _2B - _3B - HR, 0.0)
    TB = singles + 2*_2B + 3*_3B + 4*HR
    AVG = (H/AB) if AB>0 else 0.0
    OBP_den = AB + BB + HBP + SF
    OBP = ((H + BB + HBP) / OBP_den) if OBP_den>0 else 0.0
    SLG = (TB/AB) if AB>0 else 0.0
    OPS = OBP + SLG
    return {"AB":AB,"H":H,"2B":_2B,"3B":_3B,"HR":HR,"BB":BB,"HBP":HBP,"SF":SF,"TB":TB,"AVG":AVG,"OBP":OBP,"SLG":SLG,"OPS":OPS}

def ip_to_outs(ip: float) -> int:
    whole = int(ip); frac = round((ip - whole) + 1e-9, 1)
    if frac not in (0.0, 0.1, 0.2):
        if frac < 0.15: frac = 0.0
        elif frac < 0.25: frac = 0.1
        elif frac < 0.85: frac = 0.1
        else: frac = 0.2
    return whole*3 + (0 if frac==0.0 else (1 if frac==0.1 else 2))

def outs_to_ip(outs: int) -> float:
    whole = outs // 3; rem = outs % 3
    return float(f"{whole}.{rem}")

def calc_pitcher_stats(t: Dict[str, float]) -> Dict[str, float]:
    outs = t.get("IP_outs", 0.0)
    ip_inn = (outs/3.0) if outs else 0.0
    IP = outs_to_ip(int(outs)) if outs else 0.0
    ER = t.get("ER", 0.0); H = t.get("H", 0.0); BB = t.get("BB", 0.0); SO = t.get("SO", 0.0)
    ERA = (ER*9.0/ip_inn) if ip_inn>0 else 0.0
    WHIP = ((BB+H)/ip_inn) if ip_inn>0 else 0.0
    K9 = (SO*9.0/ip_inn) if ip_inn>0 else 0.0
    BB9 = (BB*9.0/ip_inn) if ip_inn>0 else 0.0
    H9 = (H*9.0/ip_inn) if ip_inn>0 else 0.0
    return {"IP":IP,"ER":ER,"H":H,"BB":BB,"SO":SO,"ERA":ERA,"WHIP":WHIP,"K9":K9,"BB9":BB9,"H9":H9}

def add_numeric_totals(tot: Dict[str, float], inc: Dict[str, float]):
    for k, v in inc.items():
        if isinstance(v, (int, float)):
            tot[k] = tot.get(k, 0.0) + float(v)

# ───────────────────────────────────────
# Embed helpers
def make_player_embed(d: Dict[str, Any], title_prefix: str = "") -> discord.Embed:
    title = f"{d['display_name']} 선수 정보" if not title_prefix else f"{title_prefix} {d['display_name']}"
    emb = discord.Embed(title=title, color=discord.Color.blue())
    emb.add_field(name="폼", value=d.get("arm_angle") or "-", inline=True)
    emb.add_field(name="팀", value=d.get("team") or "-", inline=True)
    emb.add_field(name="\u200b", value="\u200b", inline=False)
    pitches_text = pitch_str_from_list(d.get("pitches", [])) or "-"
    emb.add_field(name="구종", value=pitches_text, inline=False)
    role = d.get("role") or "-"
    emb.set_footer(text="⚾ 선수 데이터베이스  •  포지션: " + role)
    return emb

def make_ok_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=msg, color=discord.Color.green())

def make_warn_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=msg, color=discord.Color.orange())

# ───────────────────────────────────────
@bot.event
async def on_ready():
    ensure_dirs()
    print(f"✅ Logged in as {bot.user}")
    print(f"📁 DATA_DIR: {DATA_DIR}")

# 도움말(사용법 + 예시 항상 포함)
@bot.command(name="도움", aliases=["정보도우미", "help"])
async def help_cmd(ctx: commands.Context):
    p = COMMAND_PREFIX
    emb = discord.Embed(
        title="도움말",
        color=discord.Color.blurple(),
        description=(
            f"**조회**\n"
            f"• `{p}정보 닉네임` — 카드 보기\n\n"
            f"**등록/수정(구종 머지 기본)**\n"
            f"• 신규 등록: `{p}추가 닉네임 팔각도 팀=팀명 포지션=투수|타자 | 포심(40) 슬라이더(20)`\n"
            f"• 기존 선수 구종 추가: `{p}추가 닉네임 | 포심(35) 커터(20)`\n"
            f"• 수정(합치기): `{p}수정 닉네임 언더핸드 팀=레이 마린스 포지션=타자 | 포심(20) 체인지업(30)`\n"
            f"• 부분 삭제: `{p}수정 닉네임 구종-=포심 커터`\n"
            f"• 전체 교체: `{p}수정 닉네임 구종전체=포심(40) 슬라이더(20)`\n\n"
            f"**이동/목록/삭제**\n"
            f"• 이적: `{p}팀변경 닉네임 새팀`\n"
            f"• 포지션 변경: `{p}포지션변경 닉네임 새포지션`\n"
            f"• 목록: `{p}목록 팀=팀명 포지션=투수` (검색=`{p}목록 검색=포심`)\n"
            f"• 삭제: `{p}삭제 닉네임`\n\n"
            f"**일괄 가져오기**\n"
            f"• `{p}가져오기파일 팀명` + TXT 첨부 (전원 그 팀으로 저장)\n"
            f"• `{p}가져오기파일` + TXT 첨부 (파일 내 팀/포지션 헤더 사용)\n\n"
            f"**기록(통계)**\n"
            f"• 타자: `{p}기록추가타자 닉네임 타수=3 안타=2 2루타=1 볼넷=1 사구=0 희생플라이=0`\n"
            f"• 투수: `{p}기록추가투수 닉네임 이닝=5.2 자책=2 피안타=4 볼넷=1 사구=0 삼진=6`\n"
            f"• 보기: `{p}기록보기 닉네임`  •  초기화: `{p}기록리셋 닉네임`\n\n"
            f"**백업**\n"
            f"• `{p}백업zip` — 데이터 전체 ZIP"
        )
    )
    await ctx.reply(embed=emb)

# ───────────────────────────────────────
# key=value 추출 (공백/한글/기호 안전) — 모든 키 re.escape 처리
def extract_kv_span(text: str, key: str) -> Optional[str]:
    """
    key=VALUE 형태에서 VALUE를 추출.
    다음 키워드(팀,포지션,팔각도,구종,구종+,구종-,구종전체)나 '|' 또는 문자열 끝 전까지 비탐욕 매칭.
    """
    keys = ["팀", "포지션", "팔각도", "구종", "구종+", "구종-", "구종전체"]
    key_esc = re.escape(key)
    alts = "|".join(re.escape(k) for k in keys)
    pattern = rf"{key_esc}\s*=\s*(.+?)(?=\s(?:{alts})\s*=\s*|\s*\|$|$)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None

# ───────────────────────────────────────
@bot.command(name="정보")
async def info_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p:
        return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(d))

# 추가: (신규 또는 기존 병합 추가)
def parse_add_tail(tail: str) -> Tuple[str, Optional[str], Optional[str], Optional[str], List[Tuple[str, Optional[str]]]]:
    left, right = (tail, "")
    if "|" in tail:
        left, right = tail.split("|", 1)
    left = left.strip()
    pitches = parse_pitch_line(right.strip()) if right else []

    if not left:
        raise ValueError("닉네임이 필요합니다.")
    parts = left.split()
    nick = parts[0]
    rest = left[len(nick):].strip()

    team = extract_kv_span(rest, "팀")
    role = extract_kv_span(rest, "포지션")
    arm  = extract_kv_span(rest, "팔각도")

    free = re.sub(r"(팀\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=)|$)", "", rest)
    free = re.sub(r"(포지션\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=)|$)", "", free)
    free = re.sub(r"(팔각도\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=)|$)", "", free)
    free = free.strip()
    if not arm and free:
        arm = free

    return nick, (arm or None), (team or None), (role or None), pitches

def merge_pitches(existing: List[Tuple[str, Optional[str]]],
                  changes: List[Tuple[str, Optional[str]]]) -> List[Tuple[str, Optional[str]]]:
    idx = {n.lower(): (n, s) for n, s in existing}
    for n, s in changes:
        k = n.lower()
        if k in idx:
            old_n, old_s = idx[k]
            idx[k] = (old_n, s if s is not None else old_s)
        else:
            idx[k] = (n, s)
    return list(idx.values())

def replace_all_pitches(text: str) -> List[Tuple[str, Optional[str]]]:
    items = parse_pitch_line(text)
    seen: Dict[str, Optional[str]] = {}
    for n, s in items:
        seen[n] = s
    return [(n, seen[n]) for n in seen]

@bot.command(name="추가")
async def add_cmd(ctx, *, tail: str):
    try:
        nick, arm, team, role, new_pitches = parse_add_tail(tail)
    except Exception as e:
        return await ctx.reply(embed=make_warn_embed(
            f"형식 오류: {e}\n"
            f"예) `!추가 닉네임 언더핸드 팀=레이 마린스 포지션=투수 | 포심(40) 슬라이더(20)` 또는 `!추가 닉네임 | 포심(35)`"
        ))

    exists = find_player(nick)
    if exists:
        d = parse_player_file(exists.read_text(encoding="utf-8"))
        updated_arm  = arm if arm is not None else d.get("arm_angle", "")
        updated_team = team if team is not None else (d.get("team") or UNASSIGNED_TEAM_DIR)
        updated_role = role if role is not None else (d.get("role") or UNASSIGNED_ROLE_DIR)
        updated_pitches = d.get("pitches", [])
        if new_pitches:
            updated_pitches = merge_pitches(updated_pitches, new_pitches)
        write_player(d["display_name"], updated_arm or "", updated_pitches, updated_team, updated_role, old_path=exists)
        nd = parse_player_file(player_card_path(d["display_name"], updated_team, updated_role).read_text(encoding="utf-8"))
        title = "구종 추가/업데이트:" if new_pitches else "정보 업데이트:"
        return await ctx.reply(embed=make_player_embed(nd, title_prefix=title))

    team = team or UNASSIGNED_TEAM_DIR
    role = role or UNASSIGNED_ROLE_DIR
    write_player(nick, arm or "", new_pitches, team, role)
    d = parse_player_file(player_card_path(nick, team, role).read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(d, title_prefix="등록 완료:"))

# ───────────────────────────────────────
# 수정(합치기/삭제/전체교체 지원)
def remove_pitches(existing: List[Tuple[str, Optional[str]]],
                   names_to_remove: List[str]) -> List[Tuple[str, Optional[str]]]:
    rm = {n.lower() for n in names_to_remove}
    return [(n, s) for n, s in existing if n.lower() not in rm]

@bot.command(name="수정")
async def edit_cmd(ctx, nick: str, *, args: str):
    pth = find_player(nick)
    if not pth:
        return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(pth.read_text(encoding="utf-8"))

    left, pipe_part = (args, "")
    spl = re.split(r"\|\s*", args, maxsplit=1)
    if len(spl) == 2:
        left, pipe_part = spl[0].strip(), spl[1].strip()
    else:
        left = args.strip()

    new_team = extract_kv_span(left, "팀") or d.get("team") or UNASSIGNED_TEAM_DIR
    new_role = extract_kv_span(left, "포지션") or d.get("role") or UNASSIGNED_ROLE_DIR
    new_arm  = extract_kv_span(left, "팔각도")

    # 자유 텍스트 팔각도
    free = re.sub(r"(팀\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=|구종=|구종\+|구종\-|구종전체=)|$)", "", left)
    free = re.sub(r"(포지션\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=|구종=|구종\+|구종\-|구종전체=)|$)", "", free)
    free = re.sub(r"(팔각도\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=|구종=|구종\+|구종\-|구종전체=)|$)", "", free)
    free = free.strip()
    if not new_arm and free:
        new_arm = free

    # 구종 파라미터 (이스케이프 걱정 없이 literal key로 넘김)
    repl_text = extract_kv_span(left, "구종전체")
    add_text  = extract_kv_span(left, "구종+")
    del_text  = extract_kv_span(left, "구종-")
    set_text  = extract_kv_span(left, "구종")

    pitches = d.get("pitches", [])
    if repl_text:
        pitches = replace_all_pitches(repl_text)
    else:
        if pipe_part:
            pitches = merge_pitches(pitches, parse_pitch_line(pipe_part))
        if add_text:
            pitches = merge_pitches(pitches, parse_pitch_line(add_text))
        if set_text:
            pitches = merge_pitches(pitches, replace_all_pitches(set_text))
        if del_text:
            names = [n for n, _ in parse_pitch_line(del_text)]
            pitches = remove_pitches(pitches, names)

    if new_arm is not None:
        d["arm_angle"] = new_arm
    d["pitches"] = pitches

    write_player(d["display_name"], d.get("arm_angle", ""), d.get("pitches", []), new_team, new_role, old_path=pth)
    nd = parse_player_file(player_card_path(d["display_name"], new_team, new_role).read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(nd, title_prefix="수정 완료:"))

# 편의 명령
@bot.command(name="구종추가")
async def add_only_pitches(ctx, nick: str, *, text: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    d["pitches"] = merge_pitches(d.get("pitches", []), parse_pitch_line(text))
    write_player(d["display_name"], d.get("arm_angle",""), d["pitches"], d.get("team",""), d.get("role",""))
    await ctx.reply(embed=make_player_embed(d, title_prefix="구종 추가:"))

@bot.command(name="부분삭제")
async def partial_delete_pitches(ctx, nick: str, *, names: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    name_list = [t for t in re.split(r"[,\s]+", names.strip()) if t]
    d["pitches"] = remove_pitches(d.get("pitches", []), name_list)
    write_player(d["display_name"], d.get("arm_angle",""), d["pitches"], d.get("team",""), d.get("role",""))
    await ctx.reply(embed=make_player_embed(d, title_prefix="구종 삭제:"))

# 이동/삭제/목록/가져오기/백업
@bot.command(name="팀변경")
async def teamchange_cmd(ctx, nick: str, *, newteam: str):
    pth = find_player(nick)
    if not pth:
        return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(pth.read_text(encoding="utf-8"))
    write_player(d["display_name"], d["arm_angle"], d["pitches"], newteam.strip(), d["role"] or UNASSIGNED_ROLE_DIR, old_path=pth)
    nd = parse_player_file(player_card_path(d["display_name"], newteam, d["role"]).read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(nd, title_prefix="팀 변경 완료:"))

@bot.command(name="포지션변경")
async def rolechange_cmd(ctx, nick: str, *, newrole: str):
    pth = find_player(nick)
    if not pth:
        return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(pth.read_text(encoding="utf-8"))
    write_player(d["display_name"], d["arm_angle"], d["pitches"], d["team"] or UNASSIGNED_TEAM_DIR, newrole.strip(), old_path=pth)
    nd = parse_player_file(player_card_path(d["display_name"], d["team"], newrole).read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(nd, title_prefix="포지션 변경 완료:"))

@bot.command(name="삭제")
async def delete_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p:
        return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    p.unlink(missing_ok=True)
    await ctx.reply(embed=make_ok_embed("삭제 완료!"))

@bot.command(name="목록")
async def list_cmd(ctx, *, filters: str = ""):
    team_filter = None; role_filter=None; search=None
    for tok in filters.split():
        if tok.startswith("팀="): team_filter = tok.split("=",1)[1].strip()
        elif tok.startswith("포지션="): role_filter = tok.split("=",1)[1].strip()
        elif tok.startswith("검색="): search = tok.split("=",1)[1].strip().lower()
    items=[]
    for p in DATA_DIR.rglob("*.txt"):
        try:
            d = parse_player_file(p.read_text(encoding="utf-8"))
        except:
            continue
        if team_filter and (d.get("team","") != team_filter): continue
        if role_filter and (d.get("role","") != role_filter): continue
        if search:
            hay = " ".join([d.get("display_name",""), d.get("arm_angle",""), d.get("team",""), d.get("role",""),
                            ",".join([n for n,_ in d.get("pitches",[])])]).lower()
            if search not in hay: continue
        items.append(
            f"• {d['display_name']} — {d.get('arm_angle','-')} / {d.get('team','-')} / "
            + (pitch_str_from_list(d.get('pitches',[])) or "-")
        )
    if not items:
        return await ctx.reply(embed=make_warn_embed("표시할 항목이 없습니다."))
    desc = "\n".join(items[:50])
    if len(items) > 50:
        desc += f"\n… 외 {len(items)-50}명"
    await ctx.reply(embed=discord.Embed(title="선수 목록", description=desc, color=discord.Color.dark_teal()))

@bot.command(name="가져오기파일")
async def import_cmd(ctx, *, team_arg: str = ""):
    if not ctx.message.attachments:
        return await ctx.reply(embed=make_warn_embed("TXT 파일을 첨부해주세요. (예: `!가져오기파일 레이 마린스`)"))
    att = ctx.message.attachments[0]
    txt = (await att.read()).decode("utf-8", errors="ignore")
    parsed = parse_freeform(txt)
    target_team = (team_arg or "").strip()
    if target_team and target_team.startswith("팀="):
        target_team = target_team.split("=", 1)[1].strip()
    count = 0
    for nick, arm, pitches, team_from_file, role in parsed:
        team_to_use = target_team if target_team else team_from_file
        old = find_player(nick)
        write_player(nick, arm, pitches, team_to_use, role, old_path=old)
        count += 1
    await ctx.reply(embed=make_ok_embed(f"가져오기 완료! 총 {count}명 — 팀: {target_team or '파일 내 헤더 사용'}"))

@bot.command(name="백업zip")
async def backup_cmd(ctx):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(DATA_DIR):
            for f in fs:
                p = Path(r) / f
                z.write(p, arcname=p.relative_to(DATA_DIR))
    buf.seek(0)
    await ctx.reply("데이터 백업", file=discord.File(buf, "backup.zip"))

# 기록
def kv_to_dict(args: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for tok in args:
        if "=" not in tok: continue
        k,v = tok.split("=",1)
        k = k.strip()
        try:
            out[k] = float(v.strip())
        except:
            continue
    return out

@bot.command(name="기록추가타자")
async def add_batter_record(ctx, nick: str, *kvs: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    if d.get("role") != "타자":
        return await ctx.reply(embed=make_warn_embed("해당 선수의 포지션이 '타자'가 아닙니다. `!수정 닉네임 포지션=타자`로 변경 후 사용하세요."))
    inc = kv_to_dict(list(kvs))
    mapping = {"타수":"AB","안타":"H","2루타":"2B","3루타":"3B","홈런":"HR","볼넷":"BB","사구":"HBP","희생플라이":"SF","도루":"SB","도루사":"CS"}
    std_inc: Dict[str,float]={}
    for k,v in inc.items(): std_inc[mapping.get(k,k)] = v
    async with DATA_LOCK:
        rec = load_record(d["display_name"], d["team"], d["role"])
        rec["type"] = "batter"
        totals = rec.get("totals", {})
        add_numeric_totals(totals, std_inc)
        rec["totals"] = totals
        rec.setdefault("games", []).append({"ts": ctx.message.created_at.isoformat(), **std_inc})
        rec["stats"] = calc_batter_stats(totals)
        save_record(d["display_name"], d["team"], d["role"], rec)
    await ctx.reply(embed=make_ok_embed("타자 기록이 추가되었습니다. `!기록보기 닉네임`으로 확인하세요."))

@bot.command(name="기록추가투수")
async def add_pitcher_record(ctx, nick: str, *kvs: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    if d.get("role") != "투수":
        return await ctx.reply(embed=make_warn_embed("해당 선수의 포지션이 '투수'가 아닙니다. `!수정 닉네임 포지션=투수`로 변경 후 사용하세요."))
    inc = kv_to_dict(list(kvs))
    mapping = {"이닝":"IP","자책":"ER","피안타":"H","볼넷":"BB","사구":"HBP","삼진":"SO","실점":"R","탈삼진":"SO"}
    std_inc: Dict[str,float]={}
    for k,v in inc.items(): std_inc[mapping.get(k,k)] = v
    outs_add = 0
    if "IP" in std_inc:
        outs_add = ip_to_outs(std_inc["IP"]); std_inc.pop("IP", None)
    async with DATA_LOCK:
        rec = load_record(d["display_name"], d["team"], d["role"])
        rec["type"] = "pitcher"
        totals = rec.get("totals", {})
        totals["IP_outs"] = totals.get("IP_outs", 0.0) + outs_add
        add_numeric_totals(totals, {k:v for k,v in std_inc.items() if k!="IP"})
        rec["totals"] = totals
        rec.setdefault("games", []).append({"ts": ctx.message.created_at.isoformat(), "IP_outs": outs_add, **std_inc})
        rec["stats"] = calc_pitcher_stats(totals)
        save_record(d["display_name"], d["team"], d["role"], rec)
    await ctx.reply(embed=make_ok_embed("투수 기록이 추가되었습니다. `!기록보기 닉네임`으로 확인하세요."))

@bot.command(name="기록보기")
async def show_record(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    rec = load_record(d["display_name"], d["team"], d["role"])
    t = rec.get("totals", {}); s = rec.get("stats", {})
    emb = discord.Embed(title=f"{d['display_name']} 통계", color=discord.Color.dark_gold())
    if rec.get("type") == "batter" or d.get("role") == "타자":
        emb.add_field(name="누적", value=f"AB {int(t.get('AB',0))} / H {int(t.get('H',0))} / 2B {int(t.get('2B',0))} / 3B {int(t.get('3B',0))} / HR {int(t.get('HR',0))}\nBB {int(t.get('BB',0))} / HBP {int(t.get('HBP',0))} / SF {int(t.get('SF',0))}", inline=False)
        emb.add_field(name="지표", value=f"AVG {s.get('AVG',0):.3f} | OBP {s.get('OBP',0):.3f} | SLG {s.get('SLG',0):.3f} | OPS {s.get('OPS',0):.3f}", inline=False)
    else:
        emb.add_field(name="누적", value=f"IP {s.get('IP',0)} / ER {int(t.get('ER',0))} / H {int(t.get('H',0))} / BB {int(t.get('BB',0))} / SO {int(t.get('SO',0))}", inline=False)
        emb.add_field(name="지표", value=f"ERA {s.get('ERA',0):.2f} | WHIP {s.get('WHIP',0):.2f} | K/9 {s.get('K9',0):.2f} | BB/9 {s.get('BB9',0):.2f} | H/9 {s.get('H9',0):.2f}", inline=False)
    emb.set_footer(text=f"팀: {d.get('team') or '-'}  •  포지션: {d.get('role') or '-'}")
    await ctx.reply(embed=emb)

@bot.command(name="기록리셋")
async def reset_record(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=make_warn_embed("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    rec = {"type": "batter" if d.get("role")=="타자" else "pitcher", "totals": {}, "games": [], "stats": {}}
    save_record(d["display_name"], d["team"], d["role"], rec)
    await ctx.reply(embed=make_ok_embed("기록이 초기화되었습니다."))

# ───────────────────────────────────────
if __name__ == "__main__":
    ensure_dirs()
    bot.run(TOKEN)
