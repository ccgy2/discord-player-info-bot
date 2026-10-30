"""Streaming scoreboard parser: same record fields as PlayerRecord, no SDK/AI imports."""
from __future__ import annotations
import math
import re

# Match the empty-value conventions used by the previous pandas reader.
_NA = {"", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan",
       "1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"}


def clean_player_name(raw_name):
    if not raw_name:
        return ""
    return re.sub(r'\[.*?\]', '', re.sub(r'\(.*?\)', '', str(raw_name))).strip()


def safe_float(value):
    try:
        if value is None or value == "":
            return 0.0
        text = str(value).strip()
        if text.startswith('='):
            return 0.0
        number = float(text.replace(',', ''))
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def cell_text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    # Do not strip before NA recognition: pandas also leaves ' NA ' as text.
    text = str(value)
    return "" if text in _NA else text.strip()


def parse_rows(rows, sheet_name, batting, pitching, logs):
    if sheet_name in ("설명서", "펑션"):
        return
    logs.append(f"🔍 [{sheet_name}] 스트리밍 스캔 시작")
    current_section, headers = None, []
    count = 0
    for count, raw in enumerate(rows, 1):
        values = [cell_text(value) for value in raw]
        row_str = [value for value in values if value]
        full_line = ''.join(row_str).replace(' ', '')
        if not full_line:
            continue
        if '타자' in full_line and '기록' in full_line:
            current_section, headers = 'batting', []
            logs.append(f"  👉 {count}행: [타자 기록] 구간 감지")
            continue
        if '투수' in full_line and '기록' in full_line:
            current_section, headers = 'pitching', []
            logs.append(f"  👉 {count}행: [투수 기록] 구간 감지")
            continue
        if '합계' in full_line:
            current_section = None
            continue
        name_header = '선수명' in row_str or '이름' in row_str
        if name_header and ((current_section == 'batting' and '타수' in row_str)
                            or (current_section == 'pitching' and '이닝' in row_str)):
            headers = values
            logs.append('  📋 기록 헤더 감지 완료')
            continue
        if not current_section or not headers:
            continue
        record = {name: value for name, value in zip(headers, values) if name and name != 'nan'}
        player = clean_player_name(record.get('선수명') or record.get('이름'))
        if not player or player == 'nan':
            continue
        if current_section == 'batting':
            if player.isdigit() or player in ('선수명', '이름'):
                continue
            batting.append({'선수명': player, **{key: safe_float(record.get(key))
                           for key in ('타수', '안타', '타점', '득점', '도루')}})
            logs.append(f'    ⚾ 타자 추출 성공: {player}')
        elif player not in ('승', '패', '홀', '세', '선수명', '이름'):
            pitching.append({'선수명': player, **{key: safe_float(record.get(key))
                            for key in ('이닝', '타자', '피안타', '피홈런', '삼진', '실점', '자책점')}})
            logs.append(f'    🥎 투수 추출 성공: {player}')
    logs.append(f'✅ [{sheet_name}] {count}개 행 스캔 완료')
