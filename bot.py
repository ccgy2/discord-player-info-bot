import os, io, re, json, zipfile, asyncio, shutil
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

import discord
from discord.ext import commands
from dotenv import load_dotenv

# ─────────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
COMMAND_PREFIX = (os.getenv("COMMAND_PREFIX", "!") or "!").strip()
CASE_INSENSITIVE = os.getenv("CASE_INSENSITIVE", "true").lower() == "true"
UNASSIGNED_TEAM_DIR = os.getenv("UNASSIGNED_TEAM_DIR", "_unassigned").strip() or "_unassigned"
UNASSIGNED_ROLE_DIR = os.getenv("UNASSIGNED_ROLE_DIR", "_unassigned_role").strip() or "_unassigned_role"

FA_TEAM = "FA"
WAIVERS_TEAM = "웨이버"

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN이 .env에 필요합니다.")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

SAFE_CHAR_RE = re.compile(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_\- ]")
DATA_LOCK = asyncio.Lock()

# ─────────────────────────────────────────
# 허용 목록(팔각도/구종) — 파일에 지속 저장
CONFIG_DIR = DATA_DIR / "config"
ALLOWED_PATH = CONFIG_DIR / "allowed.json"

DEFAULT_ALLOWED = {
    "arms": [
        "오버핸드", "쓰리쿼터", "로우쓰리쿼터", "하이쓰리쿼터", "사이드암", "언더핸드"
    ],
    "pitches": [
        "포심","투심","싱커","커터","슬라이더","자이로 슬라이더","스위퍼","슬러터","슬러브",
        "커브","너클 커브","이퓨스","너클","체인지업","서클 체인지업","벌칸 체인지업","킥 체인지업",
        "스플리터","포크","팜볼","스크류볼"
    ]
}

def load_allowed() -> Dict[str, List[str]]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not ALLOWED_PATH.exists():
        ALLOWED_PATH.write_text(json.dumps(DEFAULT_ALLOWED, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_ALLOWED.copy()
    try:
        data = json.loads(ALLOWED_PATH.read_text(encoding="utf-8"))
        # 기본값 보정
        arms = list(dict.fromkeys((data.get("arms") or []) + DEFAULT_ALLOWED["arms"]))
        pitches = list(dict.fromkeys((data.get("pitches") or []) + DEFAULT_ALLOWED["pitches"]))
        data = {"arms": arms, "pitches": pitches}
        ALLOWED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    except:
        ALLOWED_PATH.write_text(json.dumps(DEFAULT_ALLOWED, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_ALLOWED.copy()

def save_allowed(allowed: Dict[str, List[str]]):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ALLOWED_PATH.write_text(json.dumps(allowed, ensure_ascii=False, indent=2), encoding="utf-8")

ALLOWED = load_allowed()

def allowed_arm_set(): return set(ALLOWED.get("arms", []))
def allowed_pitch_set(): return set(ALLOWED.get("pitches", []))

# ─────────────────────────────────────────
# 경로 유틸
def safe_name(txt: str) -> str:
    return SAFE_CHAR_RE.sub("", txt).strip().replace(" ", "_") or "_unknown"

def team_dir(team: Optional[str]) -> Path:
    return DATA_DIR / safe_name(team or UNASSIGNED_TEAM_DIR)

def role_dir(team: Optional[str], role: Optional[str]) -> Path:
    return team_dir(team) / safe_name(role or UNASSIGNED_ROLE_DIR)

def player_card_path(nick: str, team: Optional[str], role: Optional[str]) -> Path:
    return role_dir(team, role) / f"{safe_name(nick)}.txt"

def player_record_path(nick: str, team: Optional[str], role: Optional[str]) -> Path:
    return role_dir(team, role) / "record" / f"{safe_name(nick)}.json"

def ensure_dirs():
    (DATA_DIR / UNASSIGNED_TEAM_DIR / UNASSIGNED_ROLE_DIR).mkdir(parents=True, exist_ok=True)
    (DATA_DIR / FA_TEAM / UNASSIGNED_ROLE_DIR).mkdir(parents=True, exist_ok=True)
    (DATA_DIR / WAIVERS_TEAM / UNASSIGNED_ROLE_DIR).mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────
# 파싱 & 직렬화
def normalize_arm(value: Optional[str]) -> Optional[str]:
    """허용된 팔각도만 반환, 그 외는 None."""
    if not value: return None
    v = value.strip()
    return v if v in allowed_arm_set() else None

def filter_allowed_pitches(items: List[Tuple[str, Optional[str]]]) -> List[Tuple[str, Optional[str]]]:
    """허용된 구종만 남김(이름 정확 일치)."""
    allowed = allowed_pitch_set()
    out: List[Tuple[str, Optional[str]]] = []
    for n, s in items:
        if n in allowed:
            out.append((n, s))
    return out

def parse_pitch_line(line: str) -> List[Tuple[str, Optional[str]]]:
    """
    '포심(40) 슬라이더(30) 커터' -> [("포심","40"),("슬라이더","30"),("커터",None)]
    팔각도 단어가 섞여오면 무시. 허용되지 않은 구종은 필터링.
    """
    items: List[Tuple[str, Optional[str]]] = []
    for raw in re.split(r"[,\s]+", (line or "").strip()):
        if not raw: 
            continue
        if raw in allowed_arm_set():  # 팔각도가 구종 파트에 섞여 들어온 경우 무시
            continue
        m = re.match(r"(.+?)\(([^)]+)\)", raw)
        if m:
            items.append((m.group(1).strip(), m.group(2).strip()))
        else:
            items.append((raw.strip(), None))
    return filter_allowed_pitches(items)

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
        nick, arm = m.group(1).strip(), normalize_arm(m.group(2).strip()) or ""
    pitches: List[Tuple[str, Optional[str]]] = []
    team, role = "", ""
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
    # 저장 전에도 필터 한 번 더
    arm = arm if arm in allowed_arm_set() else ""
    pitches = filter_allowed_pitches(pitches)
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

# ─────────────────────────────────────────
# Embeds
def make_player_embed(d: Dict[str, Any], title_prefix: str = "", footer_note: str = "") -> discord.Embed:
    title = f"{d['display_name']} 선수 정보" if not title_prefix else f"{title_prefix} {d['display_name']}"
    arm = d.get("arm_angle") or "-"
    pitches_text = pitch_str_from_list(d.get("pitches", [])) or "-"
    desc = f"폼: {arm}\n구종: {pitches_text}"
    emb = discord.Embed(title=title, description=desc, color=discord.Color.dark_teal())
    emb.set_footer(text=("📚 선수 데이터베이스" + (f" • {footer_note}" if footer_note else "")))
    return emb

def make_detail_embed(d: Dict[str, Any]) -> discord.Embed:
    arm = d.get("arm_angle") or "-"
    team = d.get("team") or "-"
    role = d.get("role") or "-"
    pitches_text = pitch_str_from_list(d.get("pitches", [])) or "-"
    desc = f"폼: {arm}\n팀: {team}\n포지션: {role}\n구종: {pitches_text}"
    emb = discord.Embed(title=f"{d['display_name']} 상세 정보", description=desc, color=discord.Color.blurple())
    emb.set_footer(text="📚 선수 데이터베이스")
    return emb

def ok(msg: str): return discord.Embed(description=msg, color=discord.Color.green())
def warn(msg: str): return discord.Embed(description=msg, color=discord.Color.orange())

# ─────────────────────────────────────────
# 유틸/머지
def extract_kv_span(text: str, key: str) -> Optional[str]:
    keys = ["팀", "포지션", "팔각도", "구종", "구종+", "구종-", "구종전체"]
    key_esc = re.escape(key)
    alts = "|".join(re.escape(k) for k in keys)
    pattern = rf"{key_esc}\s*=\s*(.+?)(?=\s(?:{alts})\s*=\s*|\s*\|$|$)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None

def merge_pitches(existing: List[Tuple[str, Optional[str]]], changes: List[Tuple[str, Optional[str]]]) -> List[Tuple[str, Optional[str]]]:
    # 허용 필터
    existing = filter_allowed_pitches(existing)
    changes = filter_allowed_pitches(changes)
    result: Dict[str, Optional[str]] = {n.lower(): s for n, s in existing}
    name_map: Dict[str, str] = {n.lower(): n for n, _ in existing}
    for n, s in changes:
        key = n.lower()
        result[key] = s if s is not None else result.get(key)
        name_map.setdefault(key, n)
    return [(name_map[k], v) for k, v in result.items()]

def remove_pitches(existing: List[Tuple[str, Optional[str]]], names_to_remove: List[str]) -> List[Tuple[str, Optional[str]]]:
    rm = {n.lower() for n in names_to_remove}
    return [(n, s) for n, s in existing if n.lower() not in rm]

def replace_all_pitches(text: str) -> List[Tuple[str, Optional[str]]]:
    items = parse_pitch_line(text)
    seen: Dict[str, Optional[str]] = {}
    order: List[str] = []
    for n, s in items:
        k = n.lower()
        if k not in seen:
            order.append(k)
        seen[k] = s
    # 입력 순서 보존
    return [(n, seen[n.lower()]) for n in [next(orig for orig in [n for n,_ in items] if orig.lower()==k) for k in order]]

# ─────────────────────────────────────────
# Bot lifecycle
@bot.event
async def on_ready():
    ensure_dirs()
    # 허용 파일 보장
    _ = load_allowed()
    print(f"✅ Logged in as {bot.user}  •  DATA_DIR={DATA_DIR}")

# ─────────────────────────────────────────
# 도움말
@bot.command(name="도움", aliases=["help", "정보도우미"])
async def help_cmd(ctx: commands.Context):
    p = COMMAND_PREFIX
    e = discord.Embed(
        title="📌 선수 정보 봇 명령어 안내",
        description="봇에서 사용할 수 있는 명령어 목록과 사용 예시입니다.",
        color=discord.Color.brand_red()
    )
    e.add_field(
        name="등록/추가/수정",
        value=(
            f"`{p}등록` (여러명):\n```text\n{p}등록\n닉A (오버핸드)\n포심(40) 슬라이더(20)\n\n닉B (사이드암)\n커터(40)\n```\n"
            f"`{p}추가 닉 포심(40) 커터(20)` — 빠른 구종 추가\n"
            f"`{p}수정 닉 언더핸드 포지션=투수 | 체인지업(30)` — 머지\n"
            f"`{p}수정 닉 구종-=포심 커터` — 부분삭제 / `{p}수정 닉 구종전체=포심(60)` — 전체교체\n"
            f"※ 팔각도 허용: {', '.join(sorted(allowed_arm_set()))}\n"
            f"※ 구종 허용: {', '.join(sorted(list(allowed_pitch_set()))[:8])} …"
        ),
        inline=False
    )
    e.add_field(
        name="허용 목록 확장",
        value=f"`{p}팔각도추가 언더핸드-쓰리쿼터` (여러 개는 하이픈으로 연결) / `{p}구종추가 포심2`",
        inline=False
    )
    e.add_field(
        name="조회/목록",
        value=(
            f"`{p}정보 닉` / `{p}정보상세 닉`\n"
            f"`{p}목록` — 팀별 묶음 출력\n"
            f"`{p}팀 팀명` — 특정 팀만"
        ),
        inline=False
    )
    e.add_field(
        name="팀 이동/관리",
        value=(
            f"`{p}이적 닉 새팀` • `{p}트레이드 닉1,닉2 닉3/닉4`\n"
            f"`{p}팀이름변경 기존팀 새팀` • `{p}팀삭제 팀명`"
        ),
        inline=False
    )
    e.add_field(
        name="무소속 처리",
        value=f"`{p}방출 닉` • `{p}fa 닉` • `{p}웨이버 닉`",
        inline=False
    )
    e.add_field(
        name="가져오기/백업/기록",
        value=(
            f"`{p}가져오기파일 팀명` + TXT 첨부\n"
            f"`{p}백업zip`\n"
            f"`{p}기록추가타자/기록추가투수/기록보기/기록리셋`"
        ),
        inline=False
    )
    await ctx.reply(embed=e)

# ─────────────────────────────────────────
# 조회
@bot.command(name="정보")
async def info_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(d))

@bot.command(name="정보상세")
async def info_detail_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    await ctx.reply(embed=make_detail_embed(d))

# ─────────────────────────────────────────
# 허용 목록 추가 명령
@bot.command(name="팔각도추가")
async def add_arm_allowed(ctx, *, arms: str):
    # 여러 개를 하이픈(-) 또는 공백/콤마로 구분 입력 지원
    candidates = [t for t in re.split(r"[-,\s]+", arms.strip()) if t]
    if not candidates:
        return await ctx.reply(embed=warn("추가할 팔각도를 입력하세요. 예) `!팔각도추가 하이쓰리쿼터`"))
    added = []
    data = load_allowed()
    cur = set(data["arms"])
    for a in candidates:
        if a not in cur:
            cur.add(a); added.append(a)
    data["arms"] = sorted(cur)
    save_allowed(data)
    ALLOWED.update(data)
    if added:
        await ctx.reply(embed=ok(f"팔각도 허용 추가: {', '.join(added)}"))
    else:
        await ctx.reply(embed=warn("새로 추가된 항목이 없습니다."))

@bot.command(name="구종추가")
async def add_pitch_allowed(ctx, *, pitches: str):
    candidates = [t for t in re.split(r"[-,\s]+", pitches.strip()) if t]
    if not candidates:
        return await ctx.reply(embed=warn("추가할 구종을 입력하세요. 예) `!구종추가 슈퍼 체인지업`"))
    added = []
    data = load_allowed()
    cur = set(data["pitches"])
    for n in candidates:
        if n not in cur:
            cur.add(n); added.append(n)
    data["pitches"] = sorted(cur)
    save_allowed(data)
    ALLOWED.update(data)
    if added:
        await ctx.reply(embed=ok(f"구종 허용 추가: {', '.join(added)}"))
    else:
        await ctx.reply(embed=warn("새로 추가된 항목이 없습니다."))

# ─────────────────────────────────────────
# 등록/추가/수정/삭제
@bot.command(name="추가")
async def add_cmd(ctx, *, text: str):
    """!추가 닉 포심(40) 커터(20)  ← 빠른 추가. 팔각도 입력은 무시. 허용 외 구종은 자동 무시."""
    toks = text.split()
    if not toks: return await ctx.reply(embed=warn("형식: `!추가 닉네임 포심(40)`"))
    nick = toks[0]
    if len(toks) < 2: return await ctx.reply(embed=warn("추가할 구종을 입력하세요."))
    pitches = parse_pitch_line(" ".join(toks[1:]))
    p = find_player(nick)
    if p:
        d = parse_player_file(p.read_text(encoding="utf-8"))
        merged = merge_pitches(d.get("pitches", []), pitches)
        write_player(d["display_name"], d.get("arm_angle",""), merged, d.get("team","") or UNASSIGNED_TEAM_DIR, d.get("role","") or UNASSIGNED_ROLE_DIR, old_path=p)
        nd = parse_player_file(player_card_path(d["display_name"], d.get("team","") or UNASSIGNED_TEAM_DIR, d.get("role","") or UNASSIGNED_ROLE_DIR).read_text(encoding="utf-8"))
        return await ctx.reply(embed=make_player_embed(nd, title_prefix="구종 추가:"))
    write_player(nick, "", pitches, UNASSIGNED_TEAM_DIR, UNASSIGNED_ROLE_DIR)
    d = parse_player_file(player_card_path(nick, UNASSIGNED_TEAM_DIR, UNASSIGNED_ROLE_DIR).read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(d, title_prefix="등록 완료:"))

def parse_freeform_players(text: str) -> List[Tuple[str, str, List[Tuple[str, Optional[str]]]]]:
    blocks = re.split(r"\n\s*\n", text.strip())
    out: List[Tuple[str, str, List[Tuple[str, Optional[str]]]]] = []
    for b in blocks:
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if not lines: continue
        first = lines[0]
        m = re.match(r"(.+?)\(([^)]+)\)", first)
        if m:
            nick, arm_raw = m.group(1).strip(), m.group(2).strip()
            arm = normalize_arm(arm_raw) or ""  # 허용되지 않으면 빈 값
        else:
            nick, arm = first.strip(), ""
        pitches = parse_pitch_line(" ".join(lines[1:])) if len(lines) > 1 else []
        out.append((nick, arm, pitches))
    return out

@bot.command(name="등록")
async def register_multi(ctx):
    content = ctx.message.content
    if "\n" not in content:
        return await ctx.reply(embed=warn("`!등록` 다음 줄부터 선수 블록을 적어주세요."))
    text = content.split("\n", 1)[1]
    players = parse_freeform_players(text)
    if not players: return await ctx.reply(embed=warn("파싱할 선수가 없습니다. 예시: `!도움`"))
    count = 0
    for nick, arm, pitches in players:
        old = find_player(nick)
        if old:
            d = parse_player_file(old.read_text(encoding="utf-8"))
            merged = merge_pitches(d.get("pitches", []), pitches)
            new_arm = normalize_arm(arm) or d.get("arm_angle","")
            write_player(d["display_name"], new_arm, merged, d.get("team","") or UNASSIGNED_TEAM_DIR, d.get("role","") or UNASSIGNED_ROLE_DIR, old_path=old)
        else:
            write_player(nick, normalize_arm(arm) or "", pitches, UNASSIGNED_TEAM_DIR, UNASSIGNED_ROLE_DIR)
        count += 1
    await ctx.reply(embed=ok(f"✅ {count}명의 선수 정보를 등록 완료!"))

@bot.command(name="수정")
async def edit_cmd(ctx, nick: str, *, args: str):
    """
    폼/팀/포지션/구종 수정.
    - 팔각도는 허용값만 적용, 그 외 텍스트는 무시.
    - | 오른쪽, 구종+, 구종-, 구종, 구종전체 지원.
    """
    pth = find_player(nick)
    if not pth: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(pth.read_text(encoding="utf-8"))

    left, pipe_part = (args, "")
    spl = re.split(r"\|\s*", args, maxsplit=1)
    if len(spl) == 2: left, pipe_part = spl[0].strip(), spl[1].strip()
    else: left = args.strip()

    new_team = extract_kv_span(left, "팀") or d.get("team") or UNASSIGNED_TEAM_DIR
    new_role = extract_kv_span(left, "포지션") or d.get("role") or UNASSIGNED_ROLE_DIR

    arm_kv  = extract_kv_span(left, "팔각도")
    # 자유 텍스트에서 남은 토큰 중 허용 팔각도만 인정
    free = re.sub(r"(팀\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=|구종=|구종\+|구종\-|구종전체=)|$)", "", left)
    free = re.sub(r"(포지션\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=|구종=|구종\+|구종\-|구종전체=)|$)", "", free)
    free = re.sub(r"(팔각도\s*=\s*.+?)(?=\s(?:팀=|포지션=|팔각도=|구종=|구종\+|구종\-|구종전체=)|$)", "", free)
    free = free.strip()
    cand_arm = arm_kv or (free if free in allowed_arm_set() else None)
    valid_arm = normalize_arm(cand_arm)

    repl_text = extract_kv_span(left, "구종전체")
    add_text  = extract_kv_span(left, "구종+")
    del_text  = extract_kv_span(left, "구종-")
    set_text  = extract_kv_span(left, "구종")

    pitches = d.get("pitches", [])
    if repl_text:
        pitches = replace_all_pitches(repl_text)
    else:
        if pipe_part: pitches = merge_pitches(pitches, parse_pitch_line(pipe_part))
        if add_text:  pitches = merge_pitches(pitches, parse_pitch_line(add_text))
        if set_text:  pitches = merge_pitches(pitches, replace_all_pitches(set_text))
        if del_text:
            names = [n for n, _ in parse_pitch_line(del_text)]
            pitches = remove_pitches(pitches, names)

    if valid_arm is not None:
        d["arm_angle"] = valid_arm

    d["pitches"] = pitches
    write_player(d["display_name"], d.get("arm_angle",""), d.get("pitches",[]), new_team, new_role, old_path=pth)

    nd = parse_player_file(player_card_path(d["display_name"], new_team, new_role).read_text(encoding="utf-8"))
    note = ""
    if cand_arm and valid_arm is None:
        note = "팔각도 값이 허용 목록이 아니라서 변경하지 않았습니다."
    # 구종이 전부 필터되어 사라진 경우 안내
    if (not nd.get("pitches")) and (repl_text or pipe_part or add_text or set_text):
        note = (note + " " if note else "") + "허용되지 않은 구종은 자동으로 제외되었습니다."
    await ctx.reply(embed=make_player_embed(nd, title_prefix="수정 완료:", footer_note=note))

@bot.command(name="구종삭제")
async def cmd_delete_pitch(ctx, nick: str, *, names: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    to_remove = [t for t in re.split(r"[,\s]+", names.strip()) if t]
    if not to_remove:
        return await ctx.reply(embed=warn("삭제할 구종 이름을 적어주세요. 예) `포심 커터`"))
    d["pitches"] = remove_pitches(d.get("pitches", []), to_remove)
    write_player(d["display_name"], d.get("arm_angle",""), d["pitches"], d.get("team","") or UNASSIGNED_TEAM_DIR, d.get("role","") or UNASSIGNED_ROLE_DIR)
    await ctx.reply(embed=make_player_embed(d, title_prefix="구종 삭제:"))

@bot.command(name="닉변")
async def rename_player(ctx, old_name: str, new_name: str):
    p = find_player(old_name)
    if not p: return await ctx.reply(embed=warn("해당 선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    d["display_name"] = new_name
    write_player(new_name, d.get("arm_angle",""), d.get("pitches",[]), d.get("team",""), d.get("role",""), old_path=p)
    await ctx.reply(embed=make_player_embed(d, title_prefix="닉네임 변경 완료:"))

@bot.command(name="삭제")
async def delete_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    p.unlink(missing_ok=True)
    await ctx.reply(embed=ok("삭제 완료!"))

# ─────────────────────────────────────────
# 팀 이동/관리 (이적/트레이드/팀명변경/삭제/무소속 처리 등)
async def change_team_of(nick: str, new_team: str) -> bool:
    p = find_player(nick)
    if not p: return False
    d = parse_player_file(p.read_text(encoding="utf-8"))
    write_player(d["display_name"], d.get("arm_angle",""), d.get("pitches",[]), new_team, d.get("role","") or UNASSIGNED_ROLE_DIR, old_path=p)
    return True

@bot.command(name="이적")
async def transfer_cmd(ctx, nick: str, *, new_team: str):
    ok1 = await change_team_of(nick, new_team.strip())
    if not ok1: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    await ctx.reply(embed=ok(f"🔁 {nick} → `{new_team.strip()}` 이적 완료!"))

@bot.command(name="방출")
async def release_cmd(ctx, *, nick: str):
    if not await change_team_of(nick, UNASSIGNED_TEAM_DIR):
        return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    await ctx.reply(embed=ok(f"🆓 {nick} 방출: 무소속({_unassigned:=UNASSIGNED_TEAM_DIR}) 처리 완료!"))

@bot.command(name="fa")
async def fa_cmd(ctx, *, nick: str):
    if not await change_team_of(nick, FA_TEAM):
        return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    await ctx.reply(embed=ok(f"📝 {nick} → FA"))

@bot.command(name="웨이버")
async def waivers_cmd(ctx, *, nick: str):
    if not await change_team_of(nick, WAIVERS_TEAM):
        return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    await ctx.reply(embed=ok(f"📝 {nick} → 웨이버"))

@bot.command(name="트레이드")
async def trade_cmd(ctx, *, body: str):
    parts = re.split(r"\s+", body.strip(), maxsplit=1)
    if len(parts) < 2: return await ctx.reply(embed=warn("형식: `!트레이드 닉1,닉2 닉3/닉4`"))
    left_names = [t for t in re.split(r"[,/]+", parts[0]) if t.strip()]
    right_names = [t for t in re.split(r"[,/]+", parts[1]) if t.strip()]
    if not left_names or not right_names:
        return await ctx.reply(embed=warn("좌/우 그룹에 닉네임을 입력하세요."))

    pL = find_player(left_names[0]); pR = find_player(right_names[0])
    if not pL or not pR: return await ctx.reply(embed=warn("대표 닉네임을 찾지 못했어요."))
    dL = parse_player_file(pL.read_text(encoding="utf-8"))
    dR = parse_player_file(pR.read_text(encoding="utf-8"))
    teamA, teamB = dL.get("team") or UNASSIGNED_TEAM_DIR, dR.get("team") or UNASSIGNED_TEAM_DIR

    moved_ok, not_found = [], []
    for n in left_names:
        if await change_team_of(n.strip(), teamB): moved_ok.append(f"{n}→{teamB}")
        else: not_found.append(n)
    for n in right_names:
        if await change_team_of(n.strip(), teamA): moved_ok.append(f"{n}→{teamA}")
        else: not_found.append(n)

    desc = "🔁 트레이드 완료!\n" + ("\n".join(f"• {x}" for x in moved_ok) if moved_ok else "이동 없음")
    if not_found: desc += f"\n\n⚠️ 미발견: {', '.join(not_found)}"
    await ctx.reply(embed=ok(desc))

@bot.command(name="팀이름변경")
async def rename_team_cmd(ctx, old_team: str, *, new_team: str):
    old_dir = team_dir(old_team)
    if not old_dir.exists():
        return await ctx.reply(embed=warn("해당 팀 폴더를 찾지 못했어요."))
    count = 0
    for p in old_dir.rglob("*.txt"):
        d = parse_player_file(p.read_text(encoding="utf-8"))
        write_player(d["display_name"], d.get("arm_angle",""), d.get("pitches",[]), new_team.strip(), d.get("role","") or UNASSIGNED_ROLE_DIR, old_path=p)
        count += 1
    try:
        shutil.rmtree(old_dir, ignore_errors=True)
    except:
        pass
    await ctx.reply(embed=ok(f"🏷️ 팀명 변경: `{old_team}` → `{new_team.strip()}` (선수 {count}명 갱신)"))

@bot.command(name="팀삭제")
async def delete_team_cmd(ctx, *, team_name: str):
    tdir = team_dir(team_name)
    if not tdir.exists():
        return await ctx.reply(embed=warn("해당 팀 폴더를 찾지 못했어요."))
    count = 0
    for p in tdir.rglob("*.txt"):
        d = parse_player_file(p.read_text(encoding="utf-8"))
        write_player(d["display_name"], d.get("arm_angle",""), d.get("pitches",[]), UNASSIGNED_TEAM_DIR, d.get("role","") or UNASSIGNED_ROLE_DIR, old_path=p)
        count += 1
    try:
        shutil.rmtree(tdir, ignore_errors=True)
    except:
        pass
    await ctx.reply(embed=ok(f"🗑️ 팀 `{team_name}` 삭제 — 선수 {count}명 무소속 처리"))

# ─────────────────────────────────────────
# 목록 / 팀 / 가져오기 / 백업
@bot.command(name="목록")
async def list_cmd(ctx, *, filters: str = ""):
    if not filters.strip():
        teams: Dict[str, List[str]] = {}
        for p in DATA_DIR.rglob("*.txt"):
            try:
                d = parse_player_file(p.read_text(encoding="utf-8"))
            except:
                continue
            team = d.get("team") or UNASSIGNED_TEAM_DIR
            head = f"{d['display_name']} ({d.get('arm_angle')})" if d.get("arm_angle") else d["display_name"]
            pitches = pitch_str_from_list(d.get("pitches", []))
            teams.setdefault(team, []).append(f"{head} — {pitches}")
        if not teams:
            return await ctx.reply(embed=warn("등록된 선수가 없습니다."))
        for tname in sorted(teams.keys()):
            body = "\n".join(teams[tname])
            chunks = []
            text = body
            while len(text) > 1900:
                cut = text.rfind("\n", 0, 1900)
                if cut == -1: cut = 1900
                chunks.append(text[:cut]); text = text[cut:].lstrip()
            chunks.append(text)
            for i, ch in enumerate(chunks, 1):
                header = f"팀: {tname} (p{i}/{len(chunks)})" if len(chunks) > 1 else f"팀: {tname}"
                await ctx.reply(f"**{header}**\n```text\n{ch}\n```")
        return

    # 필터 방식
    team_filter = None; role_filter = None; search = None
    for tok in filters.split():
        if tok.startswith("팀="): team_filter = tok.split("=",1)[1].strip()
        elif tok.startswith("포지션="): role_filter = tok.split("=",1)[1].strip()
        elif tok.startswith("검색="): search = tok.split("=",1)[1].strip().lower()

    items = []
    for p in DATA_DIR.rglob("*.txt"):
        try:
            d = parse_player_file(p.read_text(encoding="utf-8"))
        except:
            continue
        if team_filter and (d.get("team","") != team_filter): continue
        if role_filter and (d.get("role","") != role_filter): continue
        if search:
            hay = " ".join([
                d.get("display_name",""), d.get("arm_angle",""),
                d.get("team",""), d.get("role",""),
                ",".join([n for n,_ in d.get("pitches",[])])
            ]).lower()
            if search not in hay: continue
        items.append(
            f"• {d['display_name']} — {d.get('arm_angle','-')} / {d.get('team','-')} / "
            + (pitch_str_from_list(d.get('pitches',[])) or "-")
        )
    if not items:
        return await ctx.reply(embed=warn("표시할 항목이 없습니다."))
    desc = "\n".join(items[:50])
    if len(items) > 50: desc += f"\n… 외 {len(items)-50}명"
    await ctx.reply(embed=discord.Embed(title="선수 목록", description=desc, color=discord.Color.dark_teal()))

@bot.command(name="팀")
async def team_cmd(ctx, *, team_name: str):
    out_sections: List[str] = []
    for p in DATA_DIR.rglob("*.txt"):
        try:
            d = parse_player_file(p.read_text(encoding="utf-8"))
        except:
            continue
        if (d.get("team") or "") != team_name:
            continue
        head = f"{d['display_name']} ({d.get('arm_angle')})" if d.get("arm_angle") else d["display_name"]
        pitches = pitch_str_from_list(d.get("pitches", []))
        out_sections.append(f"{head}\n{pitches}\n")
    if not out_sections:
        return await ctx.reply(embed=warn(f"팀 `{team_name}` 의 선수를 찾지 못했어요."))
    text = "\n".join(out_sections).rstrip()
    chunks = []
    while len(text) > 1900:
        cut = text.rfind("\n\n", 0, 1900)
        if cut == -1: cut = 1900
        chunks.append(text[:cut]); text = text[cut:].lstrip()
    chunks.append(text)
    for i, ch in enumerate(chunks, 1):
        header = f"팀: {team_name} (페이지 {i}/{len(chunks)})" if len(chunks) > 1 else f"팀: {team_name}"
        await ctx.reply(f"**{header}**\n```text\n{ch}\n```")

@bot.command(name="가져오기파일")
async def import_cmd(ctx, *, team_arg: str = ""):
    if not ctx.message.attachments:
        return await ctx.reply(embed=warn("TXT 파일을 첨부해주세요. (예: `!가져오기파일 레이`)"))
    att = ctx.message.attachments[0]
    txt = (await att.read()).decode("utf-8", errors="ignore")
    players = parse_freeform_players(txt)
    target_team = (team_arg or "").strip()
    if target_team and target_team.startswith("팀="):
        target_team = target_team.split("=", 1)[1].strip()
    count = 0
    for nick, arm, pitches in players:
        old = find_player(nick)
        team_to_use = target_team if target_team else UNASSIGNED_TEAM_DIR
        if old:
            d = parse_player_file(old.read_text(encoding="utf-8"))
            merged = merge_pitches(d.get("pitches", []), pitches)
            new_arm = normalize_arm(arm) or d.get("arm_angle","")
            write_player(d["display_name"], new_arm, merged, team_to_use or d.get("team",""), d.get("role","") or UNASSIGNED_ROLE_DIR, old_path=old)
        else:
            write_player(nick, normalize_arm(arm) or "", pitches, team_to_use, UNASSIGNED_ROLE_DIR)
        count += 1
    await ctx.reply(embed=ok(f"가져오기 완료! 총 {count}명 — 팀: {target_team or '미지정(파일 헤더 없음)'}"))

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

# ─────────────────────────────────────────
# 기록 (타자/투수)
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

def calc_batter_stats(t: Dict[str, float]) -> Dict[str, float]:
    AB=t.get("AB",0.0); H=t.get("H",0.0); _2B=t.get("2B",0.0); _3B=t.get("3B",0.0); HR=t.get("HR",0.0)
    BB=t.get("BB",0.0); HBP=t.get("HBP",0.0); SF=t.get("SF",0.0)
    singles=max(H-_2B-_3B-HR,0.0); TB=singles+2*_2B+3*_3B+4*HR
    AVG=(H/AB) if AB>0 else 0.0; OBP_den=AB+BB+HBP+SF
    OBP=((H+BB+HBP)/OBP_den) if OBP_den>0 else 0.0; SLG=(TB/AB) if AB>0 else 0.0
    OPS=OBP+SLG
    return {"AB":AB,"H":H,"2B":_2B,"3B":_3B,"HR":HR,"BB":BB,"HBP":HBP,"SF":SF,"TB":TB,"AVG":AVG,"OBP":OBP,"SLG":SLG,"OPS":OPS}

def calc_pitcher_stats(t: Dict[str, float]) -> Dict[str, float]:
    outs=t.get("IP_outs",0.0); ip_inn=(outs/3.0) if outs else 0.0
    IP=outs_to_ip(int(outs)) if outs else 0.0; ER=t.get("ER",0.0); H=t.get("H",0.0); BB=t.get("BB",0.0); SO=t.get("SO",0.0)
    ERA=(ER*9.0/ip_inn) if ip_inn>0 else 0.0; WHIP=((BB+H)/ip_inn) if ip_inn>0 else 0.0
    K9=(SO*9.0/ip_inn) if ip_inn>0 else 0.0; BB9=(BB*9.0/ip_inn) if ip_inn>0 else 0.0; H9=(H*9.0/ip_inn) if ip_inn>0 else 0.0
    return {"IP":IP,"ER":ER,"H":H,"BB":BB,"SO":SO,"ERA":ERA,"WHIP":WHIP,"K9":K9,"BB9":BB9,"H9":H9}

def load_record(nick: str, team: str, role: str) -> Dict[str, Any]:
    rp = player_record_path(nick, team, role)
    if not rp.exists(): return {"type":"batter" if role=="타자" else "pitcher","totals":{},"games":[],"stats":{}}
    try: return json.loads(rp.read_text(encoding="utf-8"))
    except: return {"type":"batter" if role=="타자" else "pitcher","totals":{},"games":[],"stats":{}}

def save_record(nick: str, team: str, role: str, rec: Dict[str, Any]):
    rp = player_record_path(nick, team, role)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

@bot.command(name="기록추가타자")
async def add_batter_record(ctx, nick: str, *kvs: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    if d.get("role") != "타자": return await ctx.reply(embed=warn("포지션이 '타자'가 아닙니다. `!수정 닉 포지션=타자` 후 사용하세요."))
    inc = {k:float(v) for k,v in (t.split("=",1) for t in kvs if "=" in t)}
    mapping={"타수":"AB","안타":"H","2루타":"2B","3루타":"3B","홈런":"HR","볼넷":"BB","사구":"HBP","희생플라이":"SF"}
    std={mapping.get(k,k):v for k,v in inc.items()}
    async with DATA_LOCK:
        rec = load_record(d["display_name"], d["team"], d["role"])
        rec["type"]="batter"; tot=rec.get("totals",{})
        for k,v in std.items(): tot[k]=tot.get(k,0.0)+v
        rec["totals"]=tot; rec["stats"]=calc_batter_stats(tot)
        save_record(d["display_name"], d["team"], d["role"], rec)
    await ctx.reply(embed=ok("타자 기록이 추가되었습니다. `!기록보기 닉`으로 확인하세요."))

def ip_to_outs_wrapper(s: str) -> int:
    try: return ip_to_outs(float(s))
    except: return 0

@bot.command(name="기록추가투수")
async def add_pitcher_record(ctx, nick: str, *kvs: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    if d.get("role") != "투수": return await ctx.reply(embed=warn("포지션이 '투수'가 아닙니다. `!수정 닉 포지션=투수` 후 사용하세요."))
    inc = {k:v for k,v in (t.split("=",1) for t in kvs if "=" in t)}
    mapping={"이닝":"IP","자책":"ER","피안타":"H","볼넷":"BB","사구":"HBP","삼진":"SO","실점":"R","탈삼진":"SO"}
    std={mapping.get(k,k):v for k,v in inc.items()}
    outs_add = ip_to_outs_wrapper(std["IP"]) if "IP" in std else 0
    if "IP" in std: std.pop("IP")
    async with DATA_LOCK:
        rec = load_record(d["display_name"], d["team"], d["role"])
        rec["type"]="pitcher"; tot=rec.get("totals",{})
        tot["IP_outs"]=tot.get("IP_outs",0.0)+outs_add
        for k,v in std.items():
            try: fv=float(v)
            except: continue
            tot[k]=tot.get(k,0.0)+fv
        rec["totals"]=tot; rec["stats"]=calc_pitcher_stats(tot)
        save_record(d["display_name"], d["team"], d["role"], rec)
    await ctx.reply(embed=ok("투수 기록이 추가되었습니다. `!기록보기 닉`으로 확인하세요."))

@bot.command(name="기록보기")
async def show_record(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    rec = load_record(d["display_name"], d["team"], d["role"])
    t=rec.get("totals",{}); s=rec.get("stats",{})
    emb = discord.Embed(title=f"{d['display_name']} 통계", color=discord.Color.dark_gold())
    if rec.get("type")=="batter" or d.get("role")=="타자":
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
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    rec={"type":"batter" if d.get("role")=="타자" else "pitcher","totals":{},"games":[],"stats":{}}
    save_record(d["display_name"], d["team"], d["role"], rec)
    await ctx.reply(embed=ok("기록이 초기화되었습니다."))

# ─────────────────────────────────────────
if __name__ == "__main__":
    ensure_dirs()
    bot.run(TOKEN)
