import firebase_admin
cred_json = os.getenv("FIREBASE_KEY")
cred = credentials.Certificate(json.loads(cred_json))
firebase_admin.initialize_app(cred)
db = firestore.client()
print("✅ Firestore 연결 성공")
from firebase_admin import credentials, firestore
import os, io, re, json, zipfile, asyncio, shutil, time
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

import discord
from discord.ext import commands
from dotenv import load_dotenv

# ─────────────────────────────────────────
# Firestore 저장/불러오기 함수
def save_player_to_firestore(nick, arm, pitches, team, role):
    try:
        doc_ref = db.collection("players").document(nick)
        data = {
            "display_name": nick,
            "arm_angle": arm,
            "team": team,
            "role": role,
            "pitches": [{"name": n, "value": s} for n, s in pitches],
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(data)
        print(f"✅ Firestore 저장 완료: {nick}")
    except Exception as e:
        print(f"❌ Firestore 저장 실패 ({nick}):", e)

def load_player_from_firestore(nick):
    try:
        doc = db.collection("players").document(nick).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        print(f"⚠️ Firestore 불러오기 실패 ({nick}):", e)
        return None

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
    except:
        data = DEFAULT_ALLOWED.copy()
    # 기본값 보강(중복 제거 + 정렬)
    arms = list(dict.fromkeys((data.get("arms") or []) + DEFAULT_ALLOWED["arms"]))
    pitches = list(dict.fromkeys((data.get("pitches") or []) + DEFAULT_ALLOWED["pitches"]))
    data = {"arms": arms, "pitches": pitches}
    ALLOWED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

def save_allowed(data: Dict[str, List[str]]):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ALLOWED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

ALLOWED = load_allowed()
def allowed_arm_set(): return set(ALLOWED.get("arms", []))
def allowed_pitch_set(): return set(ALLOWED.get("pitches", []))

# ─────────────────────────────────────────
# 경로 & 파일 유틸
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
    if not value: return None
    v = value.strip()
    return v if v in allowed_arm_set() else None

def filter_allowed_pitches(items: List[Tuple[str, Optional[str]]]) -> List[Tuple[str, Optional[str]]]:
    allowed = allowed_pitch_set()
    return [(n, s) for n, s in items if n in allowed]

def parse_pitch_line(line: str) -> List[Tuple[str, Optional[str]]]:
    items: List[Tuple[str, Optional[str]]] = []
    for raw in re.split(r"[,\s]+", (line or "").strip()):
        if not raw: 
            continue
        if raw in allowed_arm_set():  # 팔각도가 구종 파트에 섞여 들어오면 무시
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

def write_player(nick: str, arm: str, pitches: List[Tuple[str, Optional[str]]], team: str, role: str, old_path: Optional[Path] = None) -> Path:
    dest = player_card_path(nick, team, role)
    dest.parent.mkdir(parents=True, exist_ok=True)
    arm = arm if arm in allowed_arm_set() else ""
    pitches = filter_allowed_pitches(pitches)
    content = serialize_player(nick, arm, pitches, team, role)
    dest.write_text(content, encoding="utf-8")
    (dest.parent / "record").mkdir(parents=True, exist_ok=True)
    if old_path and old_path.resolve() != dest.resolve():
        try:
            old_path.unlink(missing_ok=True)
        except:
            pass
    print(f"[WRITE] {dest}  ({len(content)} bytes)")
    return dest

# ─────────────────────────────────────────
# 탐색 로직(보강)
def find_player(nick: str) -> Optional[Path]:
    """
    1) 파일 내용 파싱 후 display_name 비교
    2) 파일명 직접 비교(safe_name(nick).txt)로도 보조 탐색
    """
    key_disp = nick.lower() if CASE_INSENSITIVE else nick
    target_filename = f"{safe_name(nick)}.txt"

    # 2) 파일명 매치 우선(대규모 데이터일 때 빠름)
    for p in DATA_DIR.rglob(target_filename):
        return p

    # 1) 내용 파싱 매치
    for p in DATA_DIR.rglob("*.txt"):
        try:
            d = parse_player_file(p.read_text(encoding="utf-8"))
            name = d["display_name"].lower() if CASE_INSENSITIVE else d["display_name"]
            if name == key_disp:
                return p
        except:
            continue
    return None

def pitch_str_from_list(pitches: List[Tuple[str, Optional[str]]]) -> str:
    return " ".join([f"{n}({s})" if s else n for n, s in pitches]) if pitches else "-"

# ─────────────────────────────────────────
# Embeds
def make_player_embed(d: Dict[str, Any], title_prefix: str = "", footer_note: str = "", file_path: Optional[Path] = None) -> discord.Embed:
    title = f"{d['display_name']} 선수 정보" if not title_prefix else f"{title_prefix} {d['display_name']}"
    arm = d.get("arm_angle") or "-"
    pitches_text = pitch_str_from_list(d.get("pitches", [])) or "-"
    desc = f"폼: {arm}\n구종: {pitches_text}"
    emb = discord.Embed(title=title, description=desc, color=discord.Color.dark_teal())
    foot = "📚 선수 데이터베이스"
    if footer_note:
        foot += f" • {footer_note}"
    if file_path:
        foot += f" • 저장: {file_path.relative_to(DATA_DIR)}"
    emb.set_footer(text=foot)
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
# 봇 라이프사이클
@bot.event
async def on_ready():
    ensure_dirs()
    _ = load_allowed()
    print(f"✅ Logged in as {bot.user}")
    print(f"   DATA_DIR = {DATA_DIR}")
    print(f"   Allowed arms = {len(allowed_arm_set())}, pitches = {len(allowed_pitch_set())}")

# ─────────────────────────────────────────
# 도움말
@bot.command(name="도움", aliases=["help", "정보도우미"])
async def help_cmd(ctx: commands.Context):
    p = COMMAND_PREFIX
    e = discord.Embed(
        title="📌 마린스 봇 명령어 안내",
        description="봇에서 사용할 수 있는 명령어 목록과 사용 예시입니다.",
        color=discord.Color.brand_red()
    )
    e.add_field(
        name="등록(여러명) / 빠른 추가",
        value=(
            f"`{p}등록`\n```text\n{p}등록\n닉A (오버핸드)\n포심(40) 슬라이더(20)\n\n닉B (사이드암)\n커터(40)\n```\n"
            f"`{p}추가 닉 포심(40) 커터(20)`"
        ),
        inline=False
    )
    e.add_field(
        name="수정(머지), 부분삭제/전체교체",
        value=(
            f"`{p}수정 닉 언더핸드 포지션=투수 | 체인지업(30)`\n"
            f"`{p}수정 닉 구종-=포심 커터`\n"
            f"`{p}수정 닉 구종전체=포심(60) 싱커(40)`"
        ),
        inline=False
    )
    e.add_field(
        name="허용 목록",
        value=(
            f"팔각도: {', '.join(sorted(allowed_arm_set()))}\n"
            f"구종(일부): {', '.join(sorted(list(allowed_pitch_set()))[:10])} …\n"
            f"`{p}팔각도추가 하이언더핸드` • `{p}구종추가 슈퍼체인지업`"
        ),
        inline=False
    )
    e.add_field(
        name="조회/팀/목록",
        value=f"`{p}정보 닉` • `{p}정보상세 닉` • `{p}팀 팀명` • `{p}목록`",
        inline=False
    )
    e.add_field(
        name="이적/트레이드/팀관리",
        value=(f"`{p}이적 닉 새팀` • `{p}트레이드 닉1,닉2 닉3/닉4` • `{p}팀이름변경 A B` • `{p}팀삭제 팀명`"),
        inline=False
    )
    e.add_field(
        name="디버그",
        value=(f"`{p}저장경로` • `{p}스캔` • `{p}파일목록` • `{p}리로드허용`"),
        inline=False
    )
    await ctx.reply(embed=e)

# ─────────────────────────────────────────
# 디버그/점검 명령
@bot.command(name="저장경로")
async def cmd_where(ctx):
    await ctx.reply(embed=ok(f"DATA_DIR: `{DATA_DIR}`\n파일 수(TXT): {len(list(DATA_DIR.rglob('*.txt')))}\n허용목록: `{ALLOWED_PATH.relative_to(DATA_DIR)}`"))

@bot.command(name="스캔")
async def cmd_scan(ctx):
    names = []
    for p in DATA_DIR.rglob("*.txt"):
        try:
            d = parse_player_file(p.read_text(encoding="utf-8"))
            names.append(d.get("display_name","?"))
        except:
            pass
    if not names:
        return await ctx.reply(embed=warn("스캔 결과: 선수 카드가 없습니다."))
    chunk = ", ".join(sorted(names))[:1900]
    await ctx.reply(embed=ok(f"스캔된 선수: {chunk}"))

@bot.command(name="파일목록")
async def cmd_files(ctx):
    files = [str(p.relative_to(DATA_DIR)) for p in DATA_DIR.rglob("*.txt")]
    if not files:
        return await ctx.reply(embed=warn("TXT 파일이 없습니다."))
    text = "\n".join(files)
    while text:
        part = text[:1900]
        cut = part.rfind("\n")
        if cut != -1 and cut > 1000:
            part, text = part[:cut], text[cut+1:]
        else:
            text = text[1900:]
        await ctx.reply(f"```text\n{part}\n```")

@bot.command(name="리로드허용")
async def cmd_reload_allowed(ctx):
    global ALLOWED
    ALLOWED = load_allowed()
    await ctx.reply(embed=ok("허용 목록을 리로드했습니다."))

# ─────────────────────────────────────────
# 조회
@bot.command(name="정보")
async def info_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요. `!스캔`으로 저장된 닉을 확인하세요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    await ctx.reply(embed=make_player_embed(d))

@bot.command(name="정보상세")
async def info_detail_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    await ctx.reply(embed=make_detail_embed(d))

# ─────────────────────────────────────────
# 허용 목록 추가
@bot.command(name="팔각도추가")
async def add_arm_allowed(ctx, *, arms: str):
    cands = [t for t in re.split(r"[-,\s]+", arms.strip()) if t]
    if not cands:
        return await ctx.reply(embed=warn("예) `!팔각도추가 하이쓰리쿼터`"))
    data = load_allowed()
    cur = set(data["arms"]); added=[]
    for a in cands:
        if a not in cur:
            cur.add(a); added.append(a)
    data["arms"] = sorted(cur)
    save_allowed(data); ALLOWED.update(data)
    await ctx.reply(embed=ok(f"팔각도 추가: {', '.join(added) if added else '없음'}"))

@bot.command(name="구종추가")
async def add_pitch_allowed(ctx, *, pitches: str):
    cands = [t for t in re.split(r"[-,\s]+", pitches.strip()) if t]
    if not cands:
        return await ctx.reply(embed=warn("예) `!구종추가 슈퍼체인지업`"))
    data = load_allowed()
    cur = set(data["pitches"]); added=[]
    for a in cands:
        if a not in cur:
            cur.add(a); added.append(a)
    data["pitches"] = sorted(cur)
    save_allowed(data); ALLOWED.update(data)
    await ctx.reply(embed=ok(f"구종 추가: {', '.join(added) if added else '없음'}"))

# ─────────────────────────────────────────
# 등록/추가/수정 (새 형식: 닉 (팔각도) [팀] + 구종)
PLAYER_BLOCK_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*\[([^\]]+)\]$", re.MULTILINE)

def parse_formatted_player_block(text: str):
    """닉네임 (팔각도) [팀이름] + 구종 줄 구조"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    header, pitch_line = lines[0], lines[1]
    m = PLAYER_BLOCK_RE.match(header)
    if not m:
        return None
    nick, arm, team = m.groups()
    arm = arm.strip()
    if arm not in allowed_arm_set():
        return None
    team = team.strip()
    pitches = parse_pitch_line(pitch_line)
    return {"nick": nick, "arm": arm, "team": team, "pitches": pitches}

@bot.command(name="등록")
async def register_players(ctx):
    content = ctx.message.content.strip()
    if "\n" not in content:
        return await ctx.reply(embed=warn("형식 오류입니다.\n```\n!등록\n닉 (오버핸드) [팀이름]\n포심(20) 커터(30)\n```"))
    blocks = re.split(r"\n\s*\n", content.split("\n", 1)[1].strip())
    success = 0
    for block in blocks:
        data = parse_formatted_player_block(block)
        if not data:
            continue
        try:
            write_player(data["nick"], data["arm"], data["pitches"], data["team"], "_unassigned_role")
            success += 1
        except Exception as e:
            print("등록 오류:", e)
    if success:
        await ctx.reply(embed=ok(f"✅ {success}명의 선수 정보를 등록 완료!"))
    else:
        await ctx.reply(embed=warn("❌ 저장 중 오류가 발생했습니다."))

@bot.command(name="추가")
async def add_player(ctx):
    content = ctx.message.content.strip()
    if "\n" not in content:
        return await ctx.reply(embed=warn("형식 오류입니다.\n```\n!추가 닉 (팔각도) [팀이름]\n포심(20) 커터(30)\n```"))
    data = parse_formatted_player_block(content.split("\n", 1)[1].strip())
    if not data:
        return await ctx.reply(embed=warn("❌ 형식을 확인해주세요."))
    try:
        write_player(data["nick"], data["arm"], data["pitches"], data["team"], "_unassigned_role")
        await ctx.reply(embed=ok("➕ 1명의 선수 정보를 추가 완료!"))
    except Exception:
        await ctx.reply(embed=warn("❌ 저장 중 오류가 발생했습니다."))

@bot.command(name="수정")
async def edit_player(ctx):
    content = ctx.message.content.strip()
    if "\n" not in content:
        return await ctx.reply(embed=warn("형식 오류입니다.\n```\n!수정 닉 (팔각도) [팀이름]\n포심(20) 커터(30)\n```"))
    data = parse_formatted_player_block(content.split("\n", 1)[1].strip())
    if not data:
        return await ctx.reply(embed=warn("❌ 수정할 선수 정보를 찾지 못했습니다. 형식을 확인해주세요."))
    old = find_player(data["nick"])
    if not old:
        return await ctx.reply(embed=warn("❌ 수정할 선수 정보를 찾지 못했습니다. 형식을 확인해주세요."))
    try:
        write_player(data["nick"], data["arm"], data["pitches"], data["team"], "_unassigned_role", old_path=old)
        await ctx.reply(embed=ok("✏️ 1명의 선수 정보를 수정 완료!"))
    except Exception as e:
        print("수정 오류:", e)
        await ctx.reply(embed=warn("❌ 저장 중 오류가 발생했습니다."))


# ─────────────────────────────────────────
# 구종 삭제/닉변/삭제
@bot.command(name="구종삭제")
async def cmd_delete_pitch(ctx, nick: str, *, names: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    to_remove = [t for t in re.split(r"[,\s]+", names.strip()) if t]
    if not to_remove:
        return await ctx.reply(embed=warn("삭제할 구종 이름을 적어주세요. 예) `포심 커터`"))
    d["pitches"] = remove_pitches(d.get("pitches", []), to_remove)
    path = write_player(d["display_name"], d.get("arm_angle",""), d["pitches"], d.get("team","") or UNASSIGNED_TEAM_DIR, d.get("role","") or UNASSIGNED_ROLE_DIR)
    await ctx.reply(embed=make_player_embed(d, title_prefix="구종 삭제:", file_path=path))

@bot.command(name="닉변")
async def rename_player(ctx, old_name: str, new_name: str):
    p = find_player(old_name)
    if not p: return await ctx.reply(embed=warn("해당 선수를 찾지 못했어요."))
    d = parse_player_file(p.read_text(encoding="utf-8"))
    d["display_name"] = new_name
    path = write_player(new_name, d.get("arm_angle",""), d.get("pitches",[]), d.get("team",""), d.get("role",""), old_path=p)
    await ctx.reply(embed=make_player_embed(d, title_prefix="닉네임 변경 완료:", file_path=path))

@bot.command(name="삭제")
async def delete_cmd(ctx, *, nick: str):
    p = find_player(nick)
    if not p: return await ctx.reply(embed=warn("선수를 찾지 못했어요."))
    p.unlink(missing_ok=True)
    await ctx.reply(embed=ok("삭제 완료!"))

# ─────────────────────────────────────────
# 팀 이동/관리
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

    # 🔹 새 형식으로 블록 분리
    blocks = re.split(r"\n\s*\n", txt.strip())
    success = 0
    for block in blocks:
        data = parse_formatted_player_block(block)
        if not data:
            continue
        try:
            write_player(data["nick"], data["arm"], data["pitches"], data["team"], "_unassigned_role")
            success += 1
        except Exception as e:
            print("가져오기 오류:", e)
    await ctx.reply(embed=ok(f"📥 가져오기 완료: {success}명 저장"))

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






