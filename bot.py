# bot.py
"""
Discord + Firebase (Firestore) Baseball Player Manager Bot
- Python 3.8+
- discord.py 명령 기반 봇
- Firestore: players, teams, records(collection per player doc)
- 한국어 명령어: !정보, !정보상세, !등록, !추가, !수정, !닉변, !삭제, !구종삭제, 팀명령, 기록명령 등
- 이번 버전: "닉네임 (폼) [팀] 구종(능력치) ..." 형식 파싱 지원
"""

import os
import json
import asyncio
import re
from datetime import datetime
from typing import List, Optional

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

# ---------- 유틸리티 ----------
def now_iso():
    return datetime.utcnow().isoformat() + "Z"

def normalize_nick(nick: str) -> str:
    return nick.strip().lower()

def short_time(ts_iso: str) -> str:
    try:
        return ts_iso.replace("T", " ").split(".")[0].replace("Z", "")
    except Exception:
        return ts_iso

async def ensure_db_or_warn(ctx):
    if db is None:
        await ctx.send("❌ 데이터베이스가 초기화되어 있지 않습니다. 관리자에게 문의하세요.")
        return False
    return True

# ---------- 기본 헬프 (한글) ----------
async def send_help_text(ctx):
    BOT = BOT_PREFIX
    cmds = f"""
**사용 가능한 명령어 (요약)**

**조회**
`{BOT}정보 닉네임` - 기본 정보 출력  
`{BOT}정보상세 닉네임` - 구종 / 폼 / 팀 / 포지션 등 상세

**등록/추가/대량등록**
`{BOT}등록` - 여러 줄 텍스트로 등록 (두 포맷 지원)
  1) 기존 파이프 형식: `nick|이름|팀|포지션|구종1,구종2|폼`
  2) 새 포맷(이미지 예시): `닉네임 (폼) [팀] 구종(능력치) 구종(능력치) ...`
     예: `ccpy (언더핸드) [레이 마린스] 포심(20) 체인지업(20) 포크(30) 너클볼(30) 너클커브(40)`

`{BOT}추가 nick|이름|팀|포지션|구종1,구종2|폼` - 한 명 추가 (파이프 형식)
`{BOT}추가포맷` - (옵션) 필요 시 별도 포맷 명령 확장 가능

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

# 한국어 별칭
@bot.command(name="도움")
async def help_kor(ctx):
    await send_help_text(ctx)

@bot.command(name="도움말")
async def help_kor2(ctx):
    await send_help_text(ctx)

# ---------- 선수 관리 헬퍼 ----------
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
    msg = (
        f"**{d.get('nickname','-')}** — 기본 정보\n"
        f"이름: {d.get('name','-')}\n"
        f"팀: {d.get('team','-')}\n"
        f"포지션: {d.get('position','-')}\n"
        f"등록일: {short_time(d.get('created_at','-'))}\n"
    )
    await ctx.send(msg)

@bot.command(name="정보상세")
async def info_detail_cmd(ctx, nick: str):
    if not await ensure_db_or_warn(ctx): return
    doc = player_doc_ref(nick).get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수가 존재하지 않습니다.")
        return
    d = doc.to_dict()
    pitch_types = ", ".join([f"{p}" for p in d.get("pitch_types", [])]) if d.get("pitch_types") else "-"
    form = d.get("form","-")
    extra = d.get("extra","-")
    msg = (
        f"**{d.get('nickname','-')}** — 상세 정보\n"
        f"이름: {d.get('name','-')}\n"
        f"팀: {d.get('team','-')}\n"
        f"포지션: {d.get('position','-')}\n"
        f"구종: {pitch_types}\n"
        f"폼: {form}\n"
        f"추가정보: {extra}\n"
        f"등록: {short_time(d.get('created_at','-'))}  수정: {short_time(d.get('updated_at','-'))}\n"
    )
    await ctx.send(msg)

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
        # 팀 로스터 업데이트
        if team:
            t_ref = team_doc_ref(team)
            t_ref.set({"name": team, "created_at": now_iso()}, merge=True)
            t_ref.update({"roster": firestore.ArrayUnion([normalize_nick(nick)])})
        await ctx.send(f"✅ 선수 `{nick}` 추가됨.")
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
    added = 0
    errors = []

    # regex to parse "닉네임 (폼) [팀] 구종(숫자) 구종(숫자) , 구종(숫자)"
    line_pattern = re.compile(
        r'^\s*(?P<nick>[^\(\[\s][^\(\[\]]*?)\s*(?:\((?P<form>[^\)]*?)\))?\s*(?:\[(?P<team>[^\]]*?)\])?\s*(?P<pitches>.*)$'
    )
    pitch_pattern = re.compile(r'([^\s,()]+)\s*\(\s*(\d+)\s*\)')  # 구종(숫자)

    for i, line in enumerate(lines, start=1):
        try:
            # if line contains '|' treat as pipe format
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

                # parse pitch types like "포심(20) 체인지업(20), 포크(30)"
                pitch_types = []
                for pm in pitch_pattern.finditer(pitch_text):
                    pname = pm.group(1).strip()
                    pval = pm.group(2).strip()
                    # store as "구종(숫자)" string or as dict? keep string for display
                    pitch_types.append(f"{pname}({pval})")

                # if no explicit name field available, set name = nick
                name = nick

                # position unknown here; set placeholder
                position = "N/A"

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
            added += 1
        except Exception as e:
            errors.append(f"라인 {i}: {e}")

    res = f"✅ 등록 완료: {added}명 추가되었습니다."
    if errors:
        res += f"\n⚠️ 일부 오류:\n" + "\n".join(errors[:20])
    await ctx.send(res)

# ---------- 수정 ----------
@bot.command(name="수정")
async def edit_cmd(ctx, nick: str, field: str, *, value: str):
    """
    예: !수정 yian position P
    허용 필드: name, team, position, form, extra.<key>, pitch_types (콤마로 덮어쓰기)
    """
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 가 존재하지 않습니다.")
        return
    updates = {}
    if field.startswith("extra."):
        # nested extra field: extra.key
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

# ---------- 닉변 (문서 ID 바꾸기) ----------
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
        # 팀 로스터에 반영
        team = data.get("team")
        if team:
            t_ref = team_doc_ref(team)
            t_ref.update({
                "roster": firestore.ArrayRemove([normalize_nick(oldnick)])
            })
            t_ref.update({
                "roster": firestore.ArrayUnion([normalize_nick(newnick)])
            })
        # records doc rename (copy)
        rec_old = records_doc_ref(oldnick)
        rec_old_doc = rec_old.get()
        if rec_old_doc.exists:
            rec_new = records_doc_ref(newnick)
            rec_new.set(rec_old_doc.to_dict())
            rec_old.delete()
        await ctx.send(f"✅ `{oldnick}` → `{newnick}` 으로 변경되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 닉네임 변경 실패: {e}")

# ---------- 삭제 ----------
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
        # 팀 로스터에서 제거
        if team:
            t_ref = team_doc_ref(team)
            t_ref.update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
        # records 삭제
        records_doc_ref(nick).delete()
        await ctx.send(f"🗑️ `{nick}` 삭제되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 삭제 실패: {e}")

# ---------- 구종삭제 ----------
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
        # pitch may be stored with value like "포심(20)" so remove if equality or startswith
        newlist = [p for p in current if not (p == pitch or p.startswith(pitch+"("))]
        if len(newlist) == len(current):
            await ctx.send(f"⚠️ `{nick}` 에 `{pitch}` 구종이 없습니다.")
            return
        ref.update({"pitch_types": newlist, "updated_at": now_iso()})
        await ctx.send(f"✅ `{nick}` 의 `{pitch}` 구종이 삭제되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

# ---------- 팀 명령 ----------
@bot.command(name="팀")
async def team_cmd(ctx, *, teamname: str):
    if not await ensure_db_or_warn(ctx): return
    t_ref = team_doc_ref(teamname)
    t_doc = t_ref.get()
    if not t_doc.exists:
        # 생성
        t_ref.set({"name": teamname, "created_at": now_iso(), "roster": []})
        await ctx.send(f"✅ 팀 `{teamname}` 이(가) 생성되었습니다.")
        return
    t = t_doc.to_dict()
    roster = t.get("roster", [])
    if roster:
        # fetch first 50 names for display
        lines = []
        for nick in roster[:50]:
            lines.append(nick)
        await ctx.send(f"**{teamname}** — 로스터 ({len(roster)}):\n" + ", ".join(lines))
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
            # chunk message if too long
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
        # update player
        p_ref.update({"team": newteam, "updated_at": now_iso()})
        # remove from old team roster
        if oldteam:
            team_doc_ref(oldteam).update({"roster": firestore.ArrayRemove([normalize_nick(nick)])})
        # add to new team
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
    # set status
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
        # roster updates
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

@bot.command(name="팀이름변경")
async def rename_team_cmd(ctx, old: str, new: str):
    if not await ensure_db_or_warn(ctx): return
    old_ref = team_doc_ref(old)
    old_doc = old_ref.get()
    if not old_doc.exists:
        await ctx.send("해당 팀이 존재하지 않습니다.")
        return
    try:
        # create new team doc with same roster
        data = old_doc.to_dict()
        roster = data.get("roster", [])
        new_ref = team_doc_ref(new)
        new_ref.set({"name": new, "created_at": now_iso(), "roster": roster})
        # update each player team field
        for nick in roster:
            player_doc_ref(nick).update({"team": new, "updated_at": now_iso()})
        old_ref.delete()
        await ctx.send(f"✅ 팀 이름 `{old}` -> `{new}` 로 변경되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

@bot.command(name="팀삭제")
async def delete_team_cmd(ctx, teamname: str):
    if not await ensure_db_or_warn(ctx): return
    t_ref = team_doc_ref(teamname)
    t_doc = t_ref.get()
    if not t_doc.exists:
        await ctx.send("해당 팀이 존재하지 않습니다.")
        return
    try:
        roster = t_doc.to_dict().get("roster", [])
        for nick in roster:
            player_doc_ref(nick).update({"team": "Free", "updated_at": now_iso()})
        t_ref.delete()
        await ctx.send(f"✅ 팀 `{teamname}` 이(가) 삭제되었고 로스터 선수들은 'Free' 처리되었습니다.")
    except Exception as e:
        await ctx.send(f"❌ 실패: {e}")

# ---------- 파일 가져오기 (첨부로 CSV/TXT) ----------
@bot.command(name="가져오기파일")
async def import_file_cmd(ctx):
    """
    사용법:
    - 파일(첨부)을 메시지와 함께 올리고 `!가져오기파일` 명령어를 실행하세요.
    - 파일 포맷: 각 줄이 `nick|이름|팀|포지션|구종,구종|폼` 형식 OR 새 라인 포맷 허용
    """
    if not await ensure_db_or_warn(ctx): return
    if not ctx.message.attachments:
        await ctx.send("❌ 첨부 파일을 포함하여 명령을 호출하세요. (CSV 또는 TXT)")
        return
    att = ctx.message.attachments[0]
    try:
        data = await att.read()
        text = data.decode("utf-8").strip()
        await bulk_register_cmd.callback(ctx, bulk_text=text)
    except Exception as e:
        await ctx.send(f"❌ 파일 처리 실패: {e}")

# ---------- 기록: 타자 추가 ----------
@bot.command(name="기록추가타자")
async def add_batting_cmd(ctx, nick: str, date: str, PA: int, AB: int, R: int, H: int, RBI: int, HR: int, SB: int):
    """
    예: !기록추가타자 nick 2025-11-16 4 3 1 2 1 0
    """
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    if not ref.get().exists:
        await ctx.send("해당 선수 없음")
        return
    entry = {
        "date": date,
        "PA": int(PA),
        "AB": int(AB),
        "R": int(R),
        "H": int(H),
        "RBI": int(RBI),
        "HR": int(HR),
        "SB": int(SB),
        "added_at": now_iso()
    }
    try:
        rec_ref = records_doc_ref(nick)
        rec_ref.set({}, merge=True)  # ensure doc exists
        rec_ref.update({"batting": firestore.ArrayUnion([entry])})
        await ctx.send(f"✅ `{nick}` 에 타자 기록 추가됨: {date}")
    except Exception as e:
        await ctx.send(f"❌ 기록 추가 실패: {e}")

# ---------- 기록: 투수 추가 ----------
@bot.command(name="기록추가투수")
async def add_pitching_cmd(ctx, nick: str, date: str, IP: float, H: int, R: int, ER: int, BB: int, SO: int):
    """
    예: !기록추가투수 nick 2025-11-16 5.2 6 3 3 2 7
    IP 표기: 소수점으로 이닝 표기(예: 5.2는 5와 2/3)
    """
    if not await ensure_db_or_warn(ctx): return
    ref = player_doc_ref(nick)
    if not ref.get().exists:
        await ctx.send("해당 선수 없음")
        return
    entry = {
        "date": date,
        "IP": float(IP),
        "H": int(H),
        "R": int(R),
        "ER": int(ER),
        "BB": int(BB),
        "SO": int(SO),
        "added_at": now_iso()
    }
    try:
        rec_ref = records_doc_ref(nick)
        rec_ref.set({}, merge=True)
        rec_ref.update({"pitching": firestore.ArrayUnion([entry])})
        await ctx.send(f"✅ `{nick}` 에 투수 기록 추가됨: {date}")
    except Exception as e:
        await ctx.send(f"❌ 기록 추가 실패: {e}")

# ---------- 기록 보기 ----------
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
        # ERA 계산: (ER * 9) / IP
        era = (total_ER * 9 / total_IP) if total_IP>0 else 0
        lines.append(f"투수 기록 {len(pitching)}경기 — IP:{total_IP} ER:{total_ER} ERA:{era:.2f}")
    else:
        lines.append("투수 기록: 없음")
    # send in chunks if necessary
    msg = "\n".join(lines)
    await ctx.send(msg)

# ---------- 기록 리셋 ----------
@bot.command(name="기록리셋")
async def reset_records_cmd(ctx, nick: str, typ: str):
    """
    typ: batting | pitching | all
    """
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
    # 기본적 친절한 메시지
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("인자가 부족합니다. `!도움` 로 사용법을 확인하세요.")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("알 수 없는 명령어입니다. `!도움` 를 확인하세요.")
    else:
        # fallback: 내부 오류 로그
        await ctx.send(f"명령 실행 중 오류가 발생했습니다: `{error}`")
        print("Unhandled command error:", error)

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
