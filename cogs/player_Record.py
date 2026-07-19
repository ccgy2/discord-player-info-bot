import pandas as pd
import io
import gspread
import discord
from discord.ext import commands
from datetime import datetime, timezone

# ---------- 구글 스프레드시트 연동 설정 ----------
try:
    # google_creds.json 파일이 봇 폴더 내에 있어야 합니다.
    gc = gspread.service_account(filename='google_creds.json')
except Exception as e:
    print("⚠️ 구글 스프레드시트 연동 실패 (google_creds.json 확인 필요):", e)
    gc = None

# 스프레드시트 ID 매핑
SPREADSHEET_MAPPING = {
    "연습경기": "181T8HXTv5G0WemE8Zspyzk2Ye8dvU78rIK_3wWOt2oQ",
    "리그경기": "1bgYyE2BwiRL9k9TUJavbi1S-iCs7N3zW3rtPL5ygk6o"
}

# 구글 스프레드시트에 누적 기록을 업데이트하는 헬퍼 함수
def update_google_sheet(match_type: str, sheet_name: str, records: list, is_pitcher=False):
    """
    records: [{'선수명': 'name', '타수': 4, '안타': 3}, ...] 형태의 리스트
    is_pitcher: 투수 기록 여부 (시트 분리 혹은 컬럼 기준 판별용)
    """
    if not gc:
        print("❌ 구글 서비스 계정이 설정되지 않아 스프레드시트 반영을 건너넙니다.")
        return False
        
    try:
        spreadsheet_id = SPREADSHEET_MAPPING.get(match_type)
        if not spreadsheet_id:
            return False
            
        doc = gc.open_by_key(spreadsheet_id)
        
        # 일반적으로 '타자기록', '투수기록' 또는 시트명에 따라 선택 (기본적으로 첫번째 시트 혹은 지정된 워크시트 사용)
        # 여기서는 편의상 투수/타자에 맞게 worksheet를 찾거나 첫 시트를 기준으로 설정합니다.
        try:
            worksheet = doc.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # 지정된 이름의 시트가 없으면 첫 번째 시트 사용
            worksheet = doc.get_worksheet(0)
            
        # 모든 데이터 가져오기
        all_values = worksheet.get_all_values()
        if not all_values:
            return False
            
        header = all_values[0]
        
        # 선수명 열 위치 찾기
        if "선수명" not in header:
            return False
        name_col_idx = header.index("선수명")
        
        # 업데이트할 열들의 인덱스 매핑 (타수, 안타, 홈런, 이닝, 피안타 등)
        for row_data in records:
            player_name = row_data.get("선수명")
            if not player_name:
                continue
                
            # 기존 시트에서 선수 이름 찾기
            player_row_idx = None
            for idx, row in enumerate(all_values):
                if idx == 0: continue
                if len(row) > name_col_idx and row[name_col_idx].strip() == player_name.strip():
                    player_row_idx = idx + 1 # 1-based index for gspread
                    break
            
            if player_row_idx:
                # 이미 선수가 존재하는 경우: 기존 데이터에 더하기(추가)
                current_row_values = all_values[player_row_idx - 1]
                for key, val in row_data.items():
                    if key == "선수명": continue
                    if key in header:
                        col_idx = header.index(key)
                        # 기존 값 가져와서 숫자로 변환 시도
                        try:
                            current_val = float(current_row_values[col_idx]) if current_row_values[col_idx] else 0
                        except ValueError:
                            current_val = 0
                        
                        # 새로운 누적 값 계산
                        new_val = current_val + float(val)
                        # 소수점 처리 (이닝 수 같은 경우 .1, .2 처리나 소수점 정리를 위해 수식 또는 포맷 결정)
                        if new_val.is_integer():
                            new_val = int(new_val)
                            
                        worksheet.update_cell(player_row_idx, col_idx + 1, new_val)
            else:
                # 선수가 없는 경우: 신규 행 추가
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


# ---------- 디스코드 명령어 및 파싱 처리 ----------

async def process_excel_record(ctx, match_type: str, attachment: discord.Attachment):
    # 파일 다운로드 및 Pandas로 읽기
    file_bytes = await attachment.read()
    
    # 엑셀 파일 형식을 DataFrame으로 변환 (.xlsx 또는 .csv 지원)
    try:
        if attachment.filename.endswith('.csv'):
            # DBO 기록지 인코딩에 맞게 설정 (cp949 또는 utf-8-sig)
            df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig')
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), header=None)
    except Exception as e:
        await ctx.send(f"❌ 파일을 읽는 중 오류가 발생했습니다: `{e}`")
        return

    # 엑셀 파일 내에서 홈/원정 데이터 및 타자/투수 데이터 구역 파싱 기법
    # 기록지 양식을 보면 '구단 타자 기록', '구단 투수 기록'이라는 텍스트가 포함된 행을 기점으로 데이터가 나뉩니다.
    
    batting_records = []
    pitching_records = []
    
    current_section = None
    headers = []
    
    for idx, row in df.iterrows():
        # 행 전체를 문자열 리스트로 변환
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
            # 합계 행이 나오면 해당 섹션 종류 혹은 데이터 파싱 종료/공백 전환
            current_section = None
            continue
            
        # 컬럼 헤더 잡기
        if current_section == "batting" and "선수명" in row_str and "타수" in row_str:
            headers = [str(v).strip() for v in row.values]
            continue
        elif current_section == "pitching" and "선수명" in row_str and "이닝" in row_str:
            headers = [str(v).strip() for v in row.values]
            continue
            
        # 데이터 추출
        if current_section == "batting" and headers:
            # 현재 행을 사전형태로 매핑
            row_dict = {}
            for col_idx, col_name in enumerate(headers):
                if pd.notna(col_name) and col_name != "nan" and col_idx < len(row):
                    row_dict[col_name] = str(row.iloc[col_idx]).strip()
            
            p_name = row_dict.get("선수명")
            # 선수명이 유효하고 비어있지 않으며 숫자가 아닌 경우 기록 추가
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
                    pitching_records.append({
                        "선수명": p_name,
                        "이닝": float(row_dict.get("이닝", 0)),
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
        await ctx.send("❌ 엑셀 양식에서 유효한 타자 또는 투수 기록을 찾지 못했습니다. 양식을 확인해주세요.")
        return

    # 1. 디스코드 내 Firestore DB 데이터 백업 보관 (기존 봇 설계 구조 연동)
    # 기존 records 컬렉션 등에 누적 보관하는 로직 추가 가능
    for b in batting_records:
        ref = db.collection("records").document(b["선수명"])
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict()
            # 기존 기록에 합산
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

    # 2. 구글 스프레드시트 실시간 데이터 누적 업데이트 호출
    # 시트 내부의 서브 워크시트 이름을 '타자기록', '투수기록'으로 가정하여 업데이트 진행
    gs_bat_success = update_google_sheet(match_type, "타자기록", batting_records, is_pitcher=False)
    gs_pit_success = update_google_sheet(match_type, "투수기록", pitching_records, is_pitcher=True)

    # 3. 결과를 이쁘게 디스코드 임베드로 출력하기
    embed = discord.Embed(
        title=f"📊 [{match_type}] 경기 기록 자동 등록 완료",
        description=f"업로드된 엑셀 파일을 분석하여 구글 스프레드시트 및 DB에 합산 반영했습니다.",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    
    # 타자 등록 요약 정보 가독성 있게 편집
    if batting_records:
        b_summary = ""
        for b in batting_records[:10]: # 너무 길어지면 임베드가 잘리므로 최대 10명 표시
            b_summary += f"**{b['선수명']}**: {b['타수']}타수 {b['안타']}안타 ({b['타점']}타점 {b['득점']}득점)\n"
        if len(batting_records) > 10:
            b_summary += f"*외 {len(batting_records)-10}명의 타자 기록이 추가됨*"
        embed.add_field(name=f"⚾ 타자 합산 기록 ({len(batting_records)}명)", value=b_summary, inline=False)
        
    # 투수 등록 요약 정보 가독성 있게 편집
    if pitching_records:
        p_summary = ""
        for p in pitching_records[:10]:
            p_summary += f"**{p['선수명']}**: {p['이닝']}이닝 피안타 {p['피안타']} 삼진 {p['삼진']} (자책 {p['자책점']})\n"
        if len(pitching_records) > 10:
            p_summary += f"*외 {len(pitching_records)-10}명의 투수 기록이 추가됨*"
        embed.add_field(name=f"🥎 투수 합산 기록 ({len(pitching_records)}명)", value=p_summary, inline=False)

    status_text = "✅ 성공" if (gs_bat_success or gs_pit_success) else "⚠️ 실패 (gspread 설정 확인)"
    embed.add_field(name="구글 스프레드시트 반영 상태", value=status_text, inline=False)
    embed.set_footer(text=f"요청자: {ctx.author.display_name}")
    
    await ctx.send(embed=embed)


# ---------- 디스코드 커맨드 정의 ----------

@bot.command(name="기록엑셀")
async def record_excel_cmd(ctx, match_type: str = None):
    """
    사용법: !기록엑셀 연습경기 <엑셀파일 첨부>
    사용법: !기록엑셀 리그경기 <엑셀파일 첨부>
    """
    if not match_type or match_type not in ["연습경기", "리그경기"]:
        await ctx.send("❌ 올바른 경기 유형을 입력해주세요. 사용법: `!기록엑셀 연습경기` 또는 `!기록엑셀 리그경기` (파일 첨부 필수)")
        return
        
    if not ctx.message.attachments:
        await ctx.send("❌ 기록지 엑셀 파일(.xlsx 또는 .csv)을 함께 첨부하여 명령어를 입력해주세요.")
        return
        
    attachment = ctx.message.attachments[0]
    if not (attachment.filename.endswith('.xlsx') or attachment.filename.endswith('.xls') or attachment.filename.endswith('.csv')):
        await ctx.send("❌ 지원하지 않는 파일 형식입니다. 엑셀(.xlsx) 또는 CSV 파일만 업로드 가능합니다.")
        return
        
    await ctx.send(f"🔄 `{attachment.filename}` 파일을 분석하고 구글 스프레드시트에 누적하는 중입니다...")
    await process_excel_record(ctx, match_type, attachment)


@bot.command(name="기록확인")
async def view_record_cmd(ctx, nick: str):
    """
    사용법: !기록확인 닉네임
    임베드 형태로 합산된 누적 기록을 조회합니다.
    """
    ref = db.collection("records").document(nick)
    doc = ref.get()
    if not doc.exists:
        await ctx.send(f"❌ `{nick}` 선수의 등록된 누적 기록이 없습니다.")
        return
        
    data = doc.to_dict()
    
    # 타율 계산 예시
    ab = data.get("batting_ab", 0)
    h = data.get("batting_h", 0)
    avg = h / ab if ab > 0 else 0.0
    
    embed = discord.Embed(
        title=f"📋 {nick} 선수의 누적 시즌 기록",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    
    # 마인크래프트 스킨 이미지 연동 (기존 봇의 함수 재활용)
    try:
        from bot import safe_avatar_urls
        avatar_url, _ = safe_avatar_urls(nick)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
    except:
        pass
        
    batting_info = f"**타율**: `{avg:.3f}`\n**타수**: {ab}타수\n**안타**: {h}안타\n**타점**: {data.get('batting_rbi', 0)}타점"
    embed.add_field(name="⚾ 타격 부문", value=batting_info, inline=True)
    
    embed.set_footer(text=f"최종 갱신일: {data.get('updated_at', '-')[:10]}")
    await ctx.send(embed=embed)
