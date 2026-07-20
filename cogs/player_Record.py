import os
import io
import json
import asyncio
from datetime import datetime, timezone
import pandas as pd
import gspread
import discord
from discord.ext import commands

# ---------- 스프레드시트 링크 및 ID 매핑 ----------
SPREADSHEET_MAPPING = {
    "연습경기": "181T8HXTv5G0WemE8Zspyzk2Ye8dvU78rIK_3wWOt2oQ",
    "리그경기": "1bgYyE2BwiRL9k9TUJavbi1S-iCs7N3zW3rtPL5ygk6o"
}

# 구글 스프레드시트 클라이언트 전역 변수
gc = None

def init_gspread():
    global gc
    if gc is not None:
        return gc
    try:
        env_creds = os.getenv("GOOGLE_CREDS_JSON")
        if env_creds:
            creds_dict = json.loads(env_creds)
            gc = gspread.service_account_from_dict(creds_dict)
        else:
            gc = gspread.service_account(filename='google_creds.json')
    except Exception as e:
        print("⚠️ 구글 스프레드시트 연동 실패:", e)
        gc = None
    return gc

# 야구 이닝 누적 계산용 헬퍼 함수
def add_innings(current_inn: float, new_inn: float) -> float:
    c_int = int(current_inn)
    c_frac = int(round((current_inn - c_int) * 10))
    n_int = int(new_inn)
    n_frac = int(round((new_inn - n_int) * 10))
    total_outs = (c_int * 3 + c_frac) + (n_int * 3 + n_frac)
    return (total_outs // 3) + (total_outs % 3) / 10.0

# [🔥 보완] 스프레드시트에 '존재하는 선수'만 누적 기록 업데이트 처리
def sync_update_google_sheet(match_type: str, sheet_name: str, records: list, is_pitcher=False):
    client = init_gspread()
    if not client:
        return False
        
    try:
        spreadsheet_id = SPREADSHEET_MAPPING.get(match_type)
        if not spreadsheet_id:
            return False
            
        doc = client.open_by_key(spreadsheet_id)
        try:
            worksheet = doc.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️ '{sheet_name}' 탭을 찾지 못했습니다.")
            return False
            
        all_values = worksheet.get_all_values()
        if not all_values:
            return False
            
        header = [h.strip() for h in all_values[0]]
        if "선수명" not in header:
            return False
        name_col_idx = header.index("선수명")
        
        for row_data in records:
            player_name = row_data.get("선수명", "").strip()
            if not player_name:
                continue
                
            player_row_idx = None
            for idx, row in enumerate(all_values):
                if idx == 0: continue
                if len(row) > name_col_idx and row[name_col_idx].strip() == player_name:
                    player_row_idx = idx + 1
                    break
            
            # 💡 핵심 수정: 스프레드시트에 선수명이 존재하는 경우에만 누적을 진행합니다.
            if player_row_idx:
                current_row_values = all_values[player_row_idx - 1]
                for key, val in row_data.items():
                    if key == "선수명": continue
                    if key in header:
                        col_idx = header.index(key)
                        cell_str = str(current_row_values[col_idx]).strip() if col_idx < len(current_row_values) else ""
                        if cell_str.startswith('='):
                            continue
                            
                        try:
                            current_val = float(cell_str) if cell_str else 0.0
                        except ValueError:
                            current_val = 0.0
                        
                        if is_pitcher and key == "이닝":
                            new_val = add_innings(current_val, float(val))
                        else:
                            new_val = current_val + float(val)
                            if new_val.is_integer():
                                new_val = int(new_val)
                                
                        worksheet.update_cell(player_row_idx, col_idx + 1, new_val)
            else:
                # 명단에 없는 신규 선수일 경우, 추가하지 않고 로그를 남기고 무시합니다.
                print(f"ℹ️ [{sheet_name}] 스프레드시트 명단에 없는 선수 제외됨: {player_name}")
                
        return True
    except Exception as e:
        print(f"❌ 구글 업데이트 내부 에러: {e}")
        return False


class PlayerRecord(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = getattr(bot, "db", None)

    # 엑셀 시트(DataFrame) 하나를 분석하는 내부 헬퍼 함수
    def _parse_single_sheet(self, df: pd.DataFrame, batting_records: list, pitching_records: list):
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
                    if pd.notna(col_name) and col_name != "nan" and col_name != "" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                
                p_name = row_dict.get("선수명")
                if p_name and p_name != "nan" and p_name != "" and not p_name.isdigit() and "선수명" not in p_name:
                    try:
                        def safe_int(v):
                            if not v or v == "nan": return 0
                            return int(float(v))
                        
                        batting_records.append({
                            "선수명": p_name,
                            "타수": safe_int(row_dict.get("타수")),
                            "안타": safe_int(row_dict.get("안타")),
                            "타점": safe_int(row_dict.get("타점")),
                            "득점": safe_int(row_dict.get("득점")),
                            "도루": safe_int(row_dict.get("도루"))
                        })
                    except:
                        pass
                        
            elif current_section == "pitching" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_name != "" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                        
                p_name = row_dict.get("선수명")
                if p_name and p_name != "nan" and p_name != "" and p_name not in ["승", "패", "홀", "세", "선수명"]:
                    try:
                        def safe_int(v):
                            if not v or v == "nan": return 0
                            return int(float(v))
                            
                        inn_val = row_dict.get("이닝", "0")
                        inn_val = float(inn_val) if inn_val and inn_val != "nan" else 0.0
                        
                        pitching_records.append({
                            "선수명": p_name,
                            "이닝": inn_val,
                            "타자": safe_int(row_dict.get("타자")),
                            "피안타": safe_int(row_dict.get("피안타")),
                            "피홈런": safe_int(row_dict.get("피홈런")),
                            "삼진": safe_int(row_dict.get("삼진")),
                            "실점": safe_int(row_dict.get("실점")),
                            "자책점": safe_int(row_dict.get("자책점"))
                        })
                    except:
                        pass

    async def process_excel_record(self, ctx, match_type: str, attachment: discord.Attachment):
        file_bytes = await attachment.read()
        
        batting_records = []
        pitching_records = []
        
        try:
            # 💡 핵심 수정: 파일이 엑셀(.xlsx)인 경우 모든 시트(홈 기록지, 원정 기록지 등)를 다 긁어옵니다.
            if attachment.filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig', header=None)
                self._parse_single_sheet(df, batting_records, pitching_records)
            else:
                excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
                # 파일 내 존재하는 시트 이름들 파악
                sheet_names = excel_file.sheet_names
                
                # '홈 기록지'와 '원정 기록지'가 존재하면 각각 파싱 진행
                target_sheets = [s for s in sheet_names if "홈 기록지" in s or "원정 기록지" in s or "어웨이" in s]
                
                # 만약 지정 탭이 없으면 전체 시트 파싱
                if not target_sheets:
                    target_sheets = sheet_names
                    
                for sheet in target_sheets:
                    df = excel_file.parse(sheet_name=sheet, header=None)
                    self._parse_single_sheet(df, batting_records, pitching_records)
                    
        except Exception as e:
            await ctx.send(f"❌ 파일을 파싱하는 중 오류가 발생했습니다: `{e}`")
            return

        if not batting_records and not pitching_records:
            await ctx.send("❌ 엑셀 구조 분석 실패: 홈/원정 기록지에서 유효한 타자/투수 기록 라인을 찾지 못했습니다.")
            return

        # 구글 API 동기 작업을 비동기 스레드 풀에서 격리 구동
        loop = asyncio.get_running_loop()
        await ctx.send("📊 [홈 & 원정 통합] 데이터를 수집했습니다. 구글 스프레드시트 명단과 대조하여 누적 기록을 매칭 중입니다...")
        
        gs_bat_success = await loop.run_in_executor(
            None, sync_update_google_sheet, match_type, "타자 기록", batting_records, False
        )
        gs_pit_success = await loop.run_in_executor(
            None, sync_update_google_sheet, match_type, "투수 기록", pitching_records, True
        )

        # 디스코드 결과 임베드 출력
        embed = discord.Embed(
            title=f"📊 [{match_type}] 홈/원정 경기 기록 자동 등록 완료",
            description=f"업로드된 기록지를 가공하여 구글 스프레드시트에 이미 등록되어 있는 선수만 선별해 합산 누적했습니다.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        
        if batting_records:
            b_summary = ""
            for b in batting_records[:10]:
                b_summary += f"**{b['선수명']}**: {b['타수']}타수 {b['안타']}안타\n"
            if len(batting_records) > 10:
                b_summary += f"*외 {len(batting_records)-10}명*"
            embed.add_field(name=f"⚾ 수집된 타자 데이터 ({len(batting_records)}건)", value=b_summary, inline=False)
            
        if pitching_records:
            p_summary = ""
            for p in pitching_records[:10]:
                p_summary += f"**{p['선수명']}**: {p['이닝']}이닝 삼진 {p['삼진']}\n"
            if len(pitching_records) > 10:
                p_summary += f"*외 {len(pitching_records)-10}명*"
            embed.add_field(name=f"🥎 수집된 투수 데이터 ({len(pitching_records)}건)", value=p_summary, inline=False)

        status_text = "✅ 명단 대조 완료 및 스프레드시트 반영 성공" if (gs_bat_success or gs_pit_success) else "⚠️ 구글 연동 실패 (권한 또는 시트명 확인)"
        embed.add_field(name="구글 스프레드시트 연동 상태", value=status_text, inline=False)
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
            
        await self.process_excel_record(ctx, match_type, attachment)

async def setup(bot):
    await bot.add_cog(PlayerRecord(bot))
