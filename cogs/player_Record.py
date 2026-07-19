import os
import io
import math
import json  # ◀ [필수] 이 녀석이 꼭 들어가야 json.loads가 작동합니다!
from datetime import datetime, timezone
import pandas as pd
import gspread
import discord
from discord.ext import commands

# ---------- 구글 스프레드시트 연동 설정 ----------
try:
    # 1. 먼저 Railway 환경 변수(Variables)에 등록된 키가 있는지 확인합니다.
    env_creds = os.getenv("GOOGLE_CREDS_JSON")
    
    if env_creds:
        # 환경 변수가 있으면 텍스트를 JSON 구조로 변환하여 바로 인증합니다.
        creds_dict = json.loads(env_creds)
        gc = gspread.service_account_from_dict(creds_dict)
    else:
        # 2. 환경 변수가 없으면(로컬 내 컴퓨터 테스트 등) 기존처럼 파일에서 읽어옵니다.
        gc = gspread.service_account(filename='google_creds.json')
except Exception as e:
    print("⚠️ 구글 스프레드시트 연동 실패 (환경변수 또는 google_creds.json 확인 필요):", e)
    gc = None

# 야구 이닝(소수점 .1, .2) 합산 계산용 헬퍼 함수 (예: 1.2 + 0.2 = 2.1)
def add_innings(current_inn: float, new_inn: float) -> float:
    c_int = int(current_inn)
    c_frac = int(round((current_inn - c_int) * 10))
    
    n_int = int(new_inn)
    n_frac = int(round((new_inn - n_int) * 10))
    
    total_outs = (c_int * 3 + c_frac) + (n_int * 3 + n_frac)
    return (total_outs // 3) + (total_outs % 3) / 10.0


def update_google_sheet(match_type: str, sheet_name: str, records: list, is_pitcher=False):
    if not gc:
        print("❌ 구글 서비스 계정이 설정되지 않아 스프레드시트 반영을 건너넙니다.")
        return False
        
    try:
        spreadsheet_id = SPREADSHEET_MAPPING.get(match_type)
        if not spreadsheet_id:
            return False
            
        doc = gc.open_by_key(spreadsheet_id)
        
        try:
            worksheet = doc.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = doc.get_worksheet(0)
            
        all_values = worksheet.get_all_values()
        if not all_values:
            return False
            
        header = all_values[0]
        if "선수명" not in header:
            return False
        name_col_idx = header.index("선수명")
        
        for row_data in records:
            player_name = row_data.get("선수명")
            if not player_name:
                continue
                
            player_row_idx = None
            for idx, row in enumerate(all_values):
                if idx == 0: continue
                if len(row) > name_col_idx and row[name_col_idx].strip() == player_name.strip():
                    player_row_idx = idx + 1
                    break
            
            if player_row_idx:
                current_row_values = all_values[player_row_idx - 1]
                for key, val in row_data.items():
                    if key == "선수명": continue
                    if key in header:
                        col_idx = header.index(key)
                        
                        # 스프레드시트 내 수식(=)이 있거나 빈 값일 때 예외 처리
                        cell_str = str(current_row_values[col_idx]).strip() if col_idx < len(current_row_values) else ""
                        if cell_str.startswith('='):
                            continue
                            
                        try:
                            current_val = float(cell_str) if cell_str else 0.0
                        except ValueError:
                            current_val = 0.0
                        
                        # 투수 이닝 누적 계산 처리
                        if is_pitcher and key == "이닝":
                            new_val = add_innings(current_val, float(val))
                        else:
                            new_val = current_val + float(val)
                            if new_val.is_integer():
                                new_val = int(new_val)
                                
                        worksheet.update_cell(player_row_idx, col_idx + 1, new_val)
            else:
                new_row = [""] * len(header)
                new_row[name_col_idx] = player_name
                for key, val in row_data.items():
                    if key in header:
                        new_row[header.index(key)] = val
                worksheet.append_row(new_row)
                
        return True
    except Exception as e:
        print(f"❌ 구글 스프레드시트 업데이트 오류: {e}")
        return False


# ---------- Cogs 클래스 구성 ----------

class PlayerRecord(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 메인 bot.py에서 연동해 둔 firestore db 객체를 안전하게 끌어와서 사용합니다.
        self.db = getattr(bot, "db", None)

    async def process_excel_record(self, ctx, match_type: str, attachment: discord.Attachment):
        file_bytes = await attachment.read()
        
        try:
            if attachment.filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig', header=None)
            else:
                df = pd.read_excel(io.BytesIO(file_bytes), header=None)
        except Exception as e:
            await ctx.send(f"❌ 파일을 읽는 중 오류가 발생했습니다: `{e}`")
            return

        batting_records = []
        pitching_records = []
        current_section = None
        headers = []
        
        for idx, row in df.iterrows():
            row_str = [str(val).strip() for val in row.values if pd.notna(val)]
            full_line = " ".join(row_str)
            
            if "타자 기록" in full_line:
                current_section = "batting"
                headers = []
                continue
            elif "투수 기록" in full_line:
                current_section = "pitching"
                headers = []
                continue
            elif "합계" in full_line or (len(row_str) > 0 and row_str[0] == "합계"):
                current_section = None
                continue
                
            if current_section == "batting" and "선수명" in row_str and "타수" in row_str:
                headers = [str(v).strip() for v in row.values]
                continue
            elif current_section == "pitching" and "선수명" in row_str and "이닝" in row_str:
                headers = [str(v).strip() for v in row.values]
                continue
                
            if current_section == "batting" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                
                p_name = row_dict.get("선수명")
                if p_name and p_name != "nan" and p_name != "" and not p_name.isdigit():
                    try:
                        batting_records.append({
                            "선수명": p_name,
                            "타수": int(float(row_dict.get("타수", 0))),
                            "안타": int(float(row_dict.get("안타", 0))),
                            "타점": int(float(row_dict.get("타점", 0))),
                            "득점": int(float(row_dict.get("득점", 0))),
                            "도루": int(float(row_dict.get("도루", 0)))
                        })
                    except:
                        pass
                        
            elif current_section == "pitching" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                        
                p_name = row_dict.get("선수명")
                if p_name and p_name != "nan" and p_name != "" and p_name not in ["승", "패", "홀", "세"]:
                    try:
                        # 공백 데이터 필터링 후 0으로 보완
                        inn_val = row_dict.get("이닝", "0")
                        inn_val = float(inn_val) if inn_val and inn_val != "nan" else 0.0
                        
                        pitching_records.append({
                            "선수명": p_name,
                            "이닝": inn_val,
                            "타자": int(float(row_dict.get("타자", 0))),
                            "피안타": int(float(row_dict.get("피안타", 0))),
                            "피홈런": int(float(row_dict.get("피홈런", 0))),
                            "삼진": int(float(row_dict.get("삼진", 0))),
                            "실점": int(float(row_dict.get("실점", 0))),
                            "자책점": int(float(row_dict.get("자책점", 0)))
                        })
                    except:
                        pass

        if not batting_records and not pitching_records:
            await ctx.send("❌ 엑셀 양식에서 유효한 타자/투수 기록을 찾지 못했습니다.")
            return

        # 1. Firestore DB 백업 연동 (메인 봇의 db 객체가 활성화 상태일 때 진행)
        if self.db:
            for b in batting_records:
                ref = self.db.collection("records").document(b["선수명"])
                doc = ref.get()
                if doc.exists:
                    data = doc.to_dict()
                    ref.update({
                        "batting_ab": data.get("batting_ab", 0) + b["타수"],
                        "batting_h": data.get("batting_h", 0) + b["안타"],
                        "batting_rbi": data.get("batting_rbi", 0) + b["타점"],
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })
                else:
                    ref.set({
                        "nickname": b["선수명"],
                        "batting_ab": b["타수"],
                        "batting_h": b["안타"],
                        "batting_rbi": b["타점"],
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })

        # 2. 구글 스프레드시트 누적 업데이트
        gs_bat_success = update_google_sheet(match_type, "타자기록", batting_records, is_pitcher=False)
        gs_pit_success = update_google_sheet(match_type, "투수기록", pitching_records, is_pitcher=True)

        # 3. 디스코드 결과 임베드 출력
        embed = discord.Embed(
            title=f"📊 [{match_type}] 경기 기록 자동 등록 완료",
            description=f"업로드된 기록지를 가공하여 구글 스프레드시트 및 DB에 누적 합산했습니다.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        
        if batting_records:
            b_summary = ""
            for b in batting_records[:10]:
                b_summary += f"**{b['선수명']}**: {b['타수']}타수 {b['안타']}안타 ({b['타점']}타점 {b['득점']}득점)\n"
            if len(batting_records) > 10:
                b_summary += f"*외 {len(batting_records)-10}명의 타자*"
            embed.add_field(name=f"⚾ 타자 합산 기록 ({len(batting_records)}명)", value=b_summary, inline=False)
            
        if pitching_records:
            p_summary = ""
            for p in pitching_records[:10]:
                p_summary += f"**{p['선수명']}**: {p['이닝']}이닝 피안타 {p['피안타']} 삼진 {p['삼진']} (자책 {p['자책점']})\n"
            if len(pitching_records) > 10:
                p_summary += f"*외 {len(pitching_records)-10}명의 투수*"
            embed.add_field(name=f"🥎 투수 합산 기록 ({len(pitching_records)}명)", value=p_summary, inline=False)

        status_text = "✅ 성공적으로 반영됨" if (gs_bat_success or gs_pit_success) else "⚠️ 구글 연동 오류 (권한 설정 확인)"
        embed.add_field(name="구글 스프레드시트 상태", value=status_text, inline=False)
        embed.set_footer(text=f"요청자: {ctx.author.display_name}")
        
        await ctx.send(embed=embed)

    @commands.command(name="기록엑셀")
    async def record_excel_cmd(self, ctx, match_type: str = None):
        if not match_type or match_type not in ["연습경기", "리그경기"]:
            await ctx.send("❌ 올바른 경기 유형을 입력해주세요.\n사용법: `!기록엑셀 연습경기` 또는 `!기록엑셀 리그경기` (파일 첨부 필수)")
            return
            
        if not ctx.message.attachments:
            await ctx.send("❌ 처리할 기록지 파일(.xlsx / .csv)을 첨부 파일로 함께 동봉하여 입력해 주세요.")
            return
            
        attachment = ctx.message.attachments[0]
        if not (attachment.filename.endswith('.xlsx') or attachment.filename.endswith('.xls') or attachment.filename.endswith('.csv')):
            await ctx.send("❌ 지원하지 않는 파일 형식입니다. 엑셀 파일 또는 .csv 형식만 가능합니다.")
            return
            
        await ctx.send(f"🔄 `{attachment.filename}` 파일을 스캔하여 구글 및 데이터베이스 누적 통합을 시작합니다...")
        await self.process_excel_record(ctx, match_type, attachment)

    @commands.command(name="기록확인")
    async def view_record_cmd(self, ctx, nick: str):
        if not self.db:
            await ctx.send("❌ 시스템 에러: 데이터베이스가 연결되어 있지 않습니다.")
            return
            
        ref = self.db.collection("records").document(nick)
        doc = ref.get()
        if not doc.exists:
            await ctx.send(f"❌ DB에 `{nick}` 선수의 저장된 시즌 통합 누적 기록이 존재하지 않습니다.")
            return
            
        data = doc.to_dict()
        ab = data.get("batting_ab", 0)
        h = data.get("batting_h", 0)
        avg = h / ab if ab > 0 else 0.0
        
        embed = discord.Embed(
            title=f"📋 {nick} 선수의 시즌 통합 누적 기록",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        
        # 메인 bot.py 모듈에 존재하는 아바타 주소 변환 함수 재사용 시도
        try:
            from bot import safe_avatar_urls
            avatar_url, _ = safe_avatar_urls(nick)
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
        except:
            pass
            
        batting_info = f"**타율**: `{avg:.3f}`\n**타수**: {ab}타수\n**안타**: {h}안타\n**타점**: {data.get('batting_rbi', 0)}타점"
        embed.add_field(name="⚾ 시즌 누적 타격 스탯", value=batting_info, inline=True)
        embed.set_footer(text=f"마지막 동기화: {data.get('updated_at', '-')[:10]}")
        
        await ctx.send(embed=embed)

# 봇이 Cogs 파일을 자동으로 로드할 때 호출하는 필수 함수
async def setup(bot):
    await bot.add_cog(PlayerRecord(bot))
