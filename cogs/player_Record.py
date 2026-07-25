import os
import io
import json
import re
import asyncio
import traceback
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
        env_creds = os.getenv("GOOGLE_CREDS_JSON") or os.getenv("GOOGLE_CRED_JSON")
        if env_creds:
            creds_dict = json.loads(env_creds)
            return gspread.service_account_from_dict(creds_dict), None
        else:
            if os.path.exists('google_creds.json'):
                return gspread.service_account(filename='google_creds.json'), None
            return None, "환경변수(GOOGLE_CREDS_JSON) 또는 google_creds.json 파일을 찾을 수 없습니다."
    except Exception as e:
        return None, f"구글 인증 JSON 해석 오류: {e}"

def clean_player_name(raw_name: str) -> str:
    """닉네임 뒤의 (에러), (대타), (PH) 등 괄호 부가설명 및 공백 제거"""
    if not raw_name:
        return ""
    cleaned = re.sub(r'\(.*?\)', '', str(raw_name))
    cleaned = re.sub(r'\[.*?\]', '', cleaned)
    return cleaned.strip()

def add_innings(current_inn: float, new_inn: float) -> float:
    c_int = int(current_inn)
    c_frac = int(round((current_inn - c_int) * 10))
    n_int = int(new_inn)
    n_frac = int(round((new_inn - n_int) * 10))
    total_outs = (c_int * 3 + c_frac) + (n_int * 3 + n_frac)
    return (total_outs // 3) + (total_outs % 3) / 10.0

def sync_update_google_sheet(match_type: str, sheet_type: str, records: list, is_pitcher=False):
    client, err_msg = init_gspread()
    if not client:
        return False, [], 0, f"인증 실패: {err_msg}"
        
    try:
        spreadsheet_id = SPREADSHEET_MAPPING.get(match_type)
        if not spreadsheet_id: 
            return False, [], 0, f"[{match_type}] 매핑 ID가 존재하지 않음"
            
        doc = client.open_by_key(spreadsheet_id)
        
        # 탭 이름 유연하게 찾기 ("타자 기록", "타자", "투수 기록", "투수" 등)
        worksheet = None
        worksheets = doc.worksheets()
        target_keyword = "타자" if "타자" in sheet_type else "투수"
        
        for ws in worksheets:
            if target_keyword in ws.title:
                worksheet = ws
                break
                
        if not worksheet:
            return False, [], 0, f"스프레드시트에서 '{target_keyword}' 관련 탭을 찾지 못했습니다."
            
        all_values = worksheet.get_all_values()
        if not all_values: 
            return False, [], 0, f"'{worksheet.title}' 탭이 비어있습니다."
        
        # 첫 번째 행에서 '이름' 또는 '선수명' 또는 A열 찾기
        header = [h.strip().replace(" ", "") for h in all_values[0]]
        name_col_idx = None
        for target in ["이름", "선수명", "선수이름", "닉네임", "NAME", "PLAYER"]:
            if target in header:
                name_col_idx = header.index(target)
                break
                
        # 헤더 이름을 못 찾은 경우 기본값 0열(A열) 지정
        if name_col_idx is None:
            name_col_idx = 0
            
        success_players = []
        skipped_count = 0
        
        for row_data in records:
            player_name = clean_player_name(row_data.get("선수명", ""))
            if not player_name: continue
            
            player_row_idx = None
            for idx, row in enumerate(all_values):
                if idx == 0: continue
                if len(row) > name_col_idx and row[name_col_idx].strip().lower() == player_name.lower():
                    player_row_idx = idx + 1
                    break
                    
            if player_row_idx:
                current_row_values = all_values[player_row_idx - 1]
                for key, val in row_data.items():
                    if key == "선수명": continue
                    mod_key = key.replace(" ", "")
                    if mod_key in header:
                        col_idx = header.index(mod_key)
                        cell_str = str(current_row_values[col_idx]).strip() if col_idx < len(current_row_values) else ""
                        if cell_str.startswith('='): continue
                        try:
                            current_val = float(cell_str) if cell_str else 0.0
                        except ValueError:
                            current_val = 0.0
                        if is_pitcher and key == "이닝":
                            new_val = add_innings(current_val, float(val))
                        else:
                            new_val = current_val + float(val)
                            if new_val.is_integer(): new_val = int(new_val)
                        worksheet.update_cell(player_row_idx, col_idx + 1, new_val)
                success_players.append(player_name)
            else:
                skipped_count += 1
                
        return True, success_players, skipped_count, "성공"
        
    except gspread.exceptions.APIError as api_err:
        return False, [], 0, f"구글 API 권한/응답 에러: {api_err}"
    except Exception as e:
        return False, [], 0, f"시트 업데이트 작업 중 예외 발생: {e}"

class PlayerRecord(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _parse_single_sheet(self, df: pd.DataFrame, sheet_name: str, batting_records: list, pitching_records: list, logs: list):
        current_section = None
        headers = []
        logs.append(f"🔍 [{sheet_name}] 시트 스캔 시작 (총 {len(df)}개 행)")
        
        for idx, row in df.iterrows():
            row_str = [str(val).strip() for val in row.values if pd.notna(val) and str(val).strip() != ""]
            full_line = "".join(row_str).replace(" ", "")
            
            if not full_line:
                continue
                
            if ("타자" in full_line and "기록" in full_line) or "타자기록" in full_line:
                current_section = "batting"
                headers = []
                logs.append(f"  👉 {idx+1}행: [타자 기록] 구간 진입 ({full_line})")
                continue
            elif ("투수" in full_line and "기록" in full_line) or "투수기록" in full_line:
                current_section = "pitching"
                headers = []
                logs.append(f"  👉 {idx+1}행: [투수 기록] 구간 진입 ({full_line})")
                continue
            elif "합계" in full_line or (len(row_str) > 0 and row_str[0] == "합계"):
                current_section = None
                continue
            
            if current_section == "batting" and ("선수명" in row_str or "이름" in row_str) and "타수" in row_str:
                headers = [str(v).strip() for v in row.values]
                logs.append(f"  📋 타자 헤더 감지 완료")
                continue
            elif current_section == "pitching" and ("선수명" in row_str or "이름" in row_str) and "이닝" in row_str:
                headers = [str(v).strip() for v in row.values]
                logs.append(f"  📋 투수 헤더 감지 완료")
                continue
                
            if current_section == "batting" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_name != "" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                
                raw_p_name = row_dict.get("선수명") or row_dict.get("이름")
                p_name = clean_player_name(raw_p_name)
                
                if p_name and p_name != "nan" and not p_name.isdigit() and p_name not in ["선수명", "이름"]:
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
                        logs.append(f"    ⚾ 타자 추출 성공: {p_name} (원문: {raw_p_name})")
                    except Exception as e:
                        logs.append(f"    ❌ 타자 추출 실패 ({p_name}): {e}")
            
            elif current_section == "pitching" and headers:
                row_dict = {}
                for col_idx, col_name in enumerate(headers):
                    if pd.notna(col_name) and col_name != "nan" and col_name != "" and col_idx < len(row):
                        row_dict[col_name] = str(row.iloc[col_idx]).strip()
                        
                raw_p_name = row_dict.get("선수명") or row_dict.get("이름")
                p_name = clean_player_name(raw_p_name)
                
                if p_name and p_name != "nan" and p_name not in ["승", "패", "홀", "세", "선수명", "이름"]:
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
                        logs.append(f"    🥎 투수 추출 성공: {p_name} (원문: {raw_p_name})")
                    except Exception as e:
                        logs.append(f"    ❌ 투수 추출 실패 ({p_name}): {e}")

    async def process_excel_record(self, ctx, match_type: str, attachment: discord.Attachment):
        file_bytes = await attachment.read()
        batting_records = []
        pitching_records = []
        debug_logs = []
        
        try:
            if attachment.filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig', header=None)
                self._parse_single_sheet(df, "CSV_FILE", batting_records, pitching_records, debug_logs)
            else:
                excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
                for sheet in excel_file.sheet_names:
                    df = excel_file.parse(sheet_name=sheet, header=None)
                    self._parse_single_sheet(df, sheet, batting_records, pitching_records, debug_logs)
                    
        except Exception as e:
            await ctx.send(f"❌ 파일 분석 중 에러 발생: `{e}`")
            return

        log_text = "\n".join(debug_logs)
        if len(log_text) > 1500:
            log_text = log_text[:1500] + "\n... (중략) ..."
        
        await ctx.send(f"🛠️ **[실시간 엔진 추적 리포트]**\n```text\n{log_text}\n```")

        if not batting_records and not pitching_records:
            await ctx.send("❌ 데이터 파싱 실패: 선수 기록을 읽지 못했습니다.")
            return

        loop = asyncio.get_running_loop()
        await ctx.send("📊 데이터를 정제하여 구글 스프레드시트에 누적 연산을 시작합니다...")
        
        bat_ok, bat_ok_players, bat_skip_count, bat_err = await loop.run_in_executor(
            None, sync_update_google_sheet, match_type, "타자 기록", batting_records, False
        )
        pit_ok, pit_ok_players, pit_skip_count, pit_err = await loop.run_in_executor(
            None, sync_update_google_sheet, match_type, "투수 기록", pitching_records, True
        )

        embed = discord.Embed(
            title=f"📊 [{match_type}] 경기 기록 합산 결과",
            color=discord.Color.green() if (bat_ok or pit_ok) else discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        
        if batting_records:
            b_summary = "✅ 반영: " + ", ".join([f"`{p}`" for p in list(set(bat_ok_players))]) if bat_ok_players else "⚠️ 매칭된 인원 없음"
            if bat_skip_count > 0: b_summary += f" (제외 {bat_skip_count}명)"
            embed.add_field(name="⚾ 타자 결과", value=b_summary, inline=False)
            
        if pitching_records:
            p_summary = "✅ 반영: " + ", ".join([f"`{p}`" for p in list(set(pit_ok_players))]) if pit_ok_players else "⚠️ 매칭된 인원 없음"
            if pit_skip_count > 0: p_summary += f" (제외 {pit_skip_count}명)"
            embed.add_field(name="🥎 투수 결과", value=p_summary, inline=False)

        if bat_ok or pit_ok:
            status = "✅ 시트 반영 성공"
        else:
            status = f"❌ 타자 연동 오류: `{bat_err}`\n❌ 투수 연동 오류: `{pit_err}`"
            
        embed.add_field(name="구글 API 상태", value=status, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="기록엑셀")
    async def record_excel_cmd(self, ctx, match_type: str = None):
        if not match_type or match_type not in ["연습경기", "리그경기"]:
            await ctx.send("❌ 사용법: `!기록엑셀 연습경기` 또는 `!기록엑셀 리그경기` (파일 첨부 필수)")
            return
        if not ctx.message.attachments:
            await ctx.send("❌ 엑셀 파일을 함께 첨부해 주세요.")
            return
        await self.process_excel_record(ctx, match_type, ctx.message.attachments[0])

async def setup(bot):
    await bot.add_cog(PlayerRecord(bot))
