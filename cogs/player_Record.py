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

def init_gspread():
    """구글 서비스 계정 인증 객체 빌더 함수"""
    try:
        env_creds = os.getenv("GOOGLE_CREDS_JSON")
        if env_creds:
            creds_dict = json.loads(env_creds)
            return gspread.service_account_from_dict(creds_dict)
        else:
            if os.path.exists('google_creds.json'):
                return gspread.service_account(filename='google_creds.json')
    except Exception as e:
        print("⚠️ 구글 스프레드시트 클라이언트 초기화 에러:", e)
    return None

# 야구 이닝(소수점 .1, .2) 합산 계산용 헬퍼 함수
def add_innings(current_inn: float, new_inn: float) -> float:
    c_int = int(current_inn)
    c_frac = int(round((current_inn - c_int) * 10))
    n_int = int(new_inn)
    n_frac = int(round((new_inn - n_int) * 10))
    total_outs = (c_int * 3 + c_frac) + (n_int * 3 + n_frac)
    return (total_outs // 3) + (total_outs % 3) / 10.0

# 구글 스프레드시트 '이름' 컬럼 기준으로 누적 데이터 업데이트
def sync_update_google_sheet(match_type: str, sheet_name: str, records: list, is_pitcher=False):
    client = init_gspread()
    if not client:
        print("❌ [구글 인증 실패] 환경변수나 json 키를 확인해 주세요.")
        return False, [], 0
        
    try:
        spreadsheet_id = SPREADSHEET_MAPPING.get(match_type)
        if not spreadsheet_id:
            return False, [], 0
            
        doc = client.open_by_key(spreadsheet_id)
        try:
            worksheet = doc.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"⚠️ '{sheet_name}' 탭을 찾지 못했습니다.")
            return False, [], 0
            
        all_values = worksheet.get_all_values()
        if not all_values:
            return False, [], 0
            
        # 스프레드시트의 헤더 가져오기 (공백 제거)
        header = [h.strip().replace(" ", "") for h in all_values[0]]
        
        # 구글 시트의 기준 열 index 찾기 ('이름', '선수명', '선수이름')
        name_col_idx = None
        for target in ["이름", "선수명", "선수이름"]:
            if target in header:
                name_col_idx = header.index(target)
                break
                
        if name_col_idx is None:
            print(f"❌ '{sheet_name}' 탭에서 '이름' 컬럼 헤더를 찾을 수 없습니다.")
            return False, [], 0
        
        success_players = []
        skipped_count = 0
        
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
            
            # 명단에 존재할 때만 합산 진행
            if player_row_idx:
                current_row_values = all_values[player_row_idx - 1]
                for key, val in row_data.items():
                    if key == "선수명": continue
                    
                    mod_key = key.replace(" ", "")
                    if mod_key in header:
                        col_idx = header.index(mod_key)
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
                success_players.append(player_name)
            else:
                skipped_count += 1
                
        return True, success_players, skipped_count
    except Exception as e:
        print(f"❌ 구글 업데이트 내부 에러: {e}")
        return False, [], 0


class PlayerRecord(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = getattr(bot, "db", None)

    def _parse_single_sheet(self, df: pd.DataFrame, batting_records: list, pitching_records: list):
        current_section = None
        headers = []
        
        for idx, row in df.iterrows():
            row_str = [str(val).strip() for val in row.values if pd.notna(val) and str(val).strip() != ""]
            full_line = "".join(row_str)
            
            if not full_line:
                continue
                
            # 위치 무관 섹션 판별
            if "타자기록" in full_line.replace(" ", ""):
                current_section = "batting"
                headers = []
                continue
            elif "투수기록" in full_line.replace(" ", ""):
                current_section = "pitching"
                headers = []
                continue
            elif "합계" in full_line or row_str[0] == "합계":
                current_section = None
                continue
            
            # 헤더 라인 검출
            if current_section == "batting" and ("선수명" in row_str or "이름" in row_str) and "타수" in row_str:
                headers = [str(v).strip() for v in row.values]
                continue
            elif current_section == "pitching" and ("선수명" in row_str or "이름" in row_str) and "이닝" in row_str:
                headers = [str(v).strip() for v in row.values]
                continue
                
            # 타자 데이터 파싱
            if current_section == "batting" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_name != "" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                
                p_name = row_dict.get("선수명") or row_dict.get("이름")
                if p_name and p_name != "nan" and p_name != "" and not p_name.isdigit() and p_name not in ["선수명", "이름"]:
                    try:
                        def safe_int(v):
                            if not v or v == "nan" or v == "": return 0
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
            
            # 투수 데이터 파싱
            elif current_section == "pitching" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_name != "" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                        
                p_name = row_dict.get("선수명") or row_dict.get("이름")
                if p_name and p_name != "nan" and p_name != "" and p_name not in ["승", "패", "홀", "세", "선수명", "이름"]:
                    try:
                        def safe_int(v):
                            if not v or v == "nan" or v == "": return 0
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
            if attachment.filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig', header=None)
                self._parse_single_sheet(df, batting_records, pitching_records)
            else:
                excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
                sheet_names = excel_file.sheet_names
                
                # 💡 [🔥 핵심 수정] 시트 이름 조건 필터를 완전히 제거했습니다!
                # 파일 내부에 있는 모든 시트(홈, 원정, Sheet1 등)를 무조건 전부 긁어옵니다.
                for sheet in sheet_names:
                    df = excel_file.parse(sheet_name=sheet, header=None)
                    self._parse_single_sheet(df, batting_records, pitching_records)
                    
        except Exception as e:
            await ctx.send(f"❌ 파일을 읽고 분석하는 중 에러가 발생했습니다: `{e}`")
            return

        if not batting_records and not pitching_records:
            await ctx.send("❌ 엑셀 매칭 오류: 데이터 파싱 단계에서 선수 기록 단락을 수집하지 못했습니다.")
            return

        loop = asyncio.get_running_loop()
        await ctx.send("📊 데이터를 분석 완료했습니다. 구글 명단과 일치하는 선수 정보만 선별해 누적 연산을 수행합니다...")
        
        bat_ok, bat_ok_players, bat_skip_count = await loop.run_in_executor(
            None, sync_update_google_sheet, match_type, "타자 기록", batting_records, False
        )
        
        pit_ok, pit_ok_players, pit_skip_count = await loop.run_in_executor(
            None, sync_update_google_sheet, match_type, "투수 기록", pitching_records, True
        )

        embed = discord.Embed(
            title=f"📊 [{match_type}] 홈 & 원정 경기 기록 통합 반영",
            description=f"구글 스프레드시트 명단에 명시된 팀원만 필터링하여 시즌 데이터에 합산 처리했습니다.",
            color=discord.Color.green() if (bat_ok or pit_ok) else discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        if batting_records:
            if bat_ok_players:
                # 중복 이름을 제거하여 깔끔하게 세팅
                unique_bat_players = list(set(bat_ok_players))
                b_summary = "✅ **반영된 선수**: " + ", ".join([f"`{p}`" for p in unique_bat_players])
            else:
                b_summary = "⚠️ *스프레드시트에 일치하는 타자 없음*"
                
            if bat_skip_count > 0:
                b_summary += f"\n❌ **제외된 인원**: 외 `{bat_skip_count}명` (시트에 이름이 존재하지 않음)"
            embed.add_field(name="⚾ 타자 누적 반영 결과", value=b_summary, inline=False)
            
        if pitching_records:
            if pit_ok_players:
                unique_pit_players = list(set(pit_ok_players))
                p_summary = "✅ **반영된 선수**: " + ", ".join([f"`{p}`" for p in unique_pit_players])
            else:
                p_summary = "⚠️ *스프레드시트에 일치하는 투수 없음*"
                
            if pit_skip_count > 0:
                p_summary += f"\n❌ **제외된 인원**: 외 `{pit_skip_count}명` (시트에 이름이 존재하지 않음)"
            embed.add_field(name="🥎 투수 누적 반영 결과", value=p_summary, inline=False)

        status_text = "✅ 명단 동기화 및 스프레드시트 연동 성공" if (bat_ok or pit_ok) else "❌ 구글 연동 실패 (Railway 환경변수 GOOGLE_CREDS_JSON 확인 요망)"
        embed.add_field(name="구글 API 통신 상태", value=status_text, inline=False)
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
    
