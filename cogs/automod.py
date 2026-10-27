"""한국어 Discord 자동관리 — 단일 Cog 배포판 2.1.0.

배치: 기존 프로젝트의 cogs/automod.py 하나만 추가합니다.
필수: Python 3.11+, discord.py 2.x, Firebase Admin, EasyOCR ko/en CPU.
설치된 Python 라이브러리·OCR 모델·Firebase 환경변수는 별도로 필요합니다.
이 파일은 bot.py/토큰/다른 Cog를 수정하거나 보조 Python/JSON 파일을 풀지 않습니다.

명령 (프로젝트 루트에서):
    python cogs/automod.py --check
    python cogs/automod.py --install-ocr-models
    python cogs/automod.py --check-ocr-models

Firebase: FIREBASE_PROJECT_ID, ADC 또는 GOOGLE_APPLICATION_CREDENTIALS.
선택: FIREBASE_STORAGE_BUCKET, AUTOMOD_OCR_MODEL_DIR.
기본 사전은 이 파일에 내장하며 서버별 추가·제외 설정은 Firestore에 저장합니다.
설치 후 /자동관리 로그생성 또는 /자동관리 설정 명령으로 운영을 시작합니다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

__version__ = "2.1.0-singlefile"
_SINGLE_FILE = Path(__file__).resolve()
ROOT = _SINGLE_FILE.parent.parent


def _model_directory() -> Path:
    configured = os.environ.get("AUTOMOD_OCR_MODEL_DIR", "").strip()
    directory = Path(configured).expanduser() if configured else ROOT / "data" / "ocr_models"
    if not directory.is_absolute():
        directory = ROOT / directory
    return directory.resolve()


MODELS_PATH = _model_directory()
# None means the immutable dictionary embedded below. An explicit path can be
# injected for developer tests; no data/automod_rules.json is needed to run.
RULES_PATH: Path | None = None


# ===========================================================================
# 내장 기본 사전
# ===========================================================================

_BUILTIN_RULES_JSON = r'''
{
  "version": 1,
  "description": "Local conservative seed dictionary. No statistical certainty; review labels never count as warnings. Bounded gap <=2; exact explicit variants only.",
  "zero_width_characters": [
    "U+200B",
    "U+200C",
    "U+200D",
    "U+2060",
    "U+FEFF"
  ],
  "allow_terms": [
    "시발점",
    "시바견",
    "한남동"
  ],
  "rules": [
    {
      "id": "ko.profanity.sibal",
      "expression": "씨발",
      "variants": [
        "시발",
        "씨팔",
        "씹할",
        "씨이발",
        "씨이이발",
        "ssibal"
      ],
      "category": "profanity",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테",
        "점"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.profanity.jot",
      "expression": "좆",
      "variants": [
        "좆같다",
        "좆같아",
        "좆같네",
        "좆같은"
      ],
      "category": "profanity",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.profanity.jonna",
      "expression": "존나",
      "variants": [],
      "category": "profanity",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "review_only",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.profanity.jiral",
      "expression": "지랄",
      "variants": [
        "지랄하네",
        "지랄한다",
        "지랄하지마"
      ],
      "category": "profanity",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.profanity.gaesaekki",
      "expression": "개새끼",
      "variants": [],
      "category": "profanity",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.profanity.gaesori",
      "expression": "개소리",
      "variants": [
        "개소리하네",
        "개소리하지마"
      ],
      "category": "profanity",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.profanity.michin",
      "expression": "미친놈",
      "variants": [
        "미친년"
      ],
      "category": "profanity",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.shorthand",
      "expression": "ㅅㅂ",
      "variants": [
        "ㅆㅂ",
        "ㅂㅅ",
        "ㅄ",
        "ㅈㄹ",
        "ㅈㄴ",
        "ㄲㅈ",
        "ㄷㅊ"
      ],
      "category": "shorthand",
      "severity": "medium",
      "match_mode": "token",
      "context_required": true,
      "auto_policy": "review_only",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.insult.byeongsin",
      "expression": "병신",
      "variants": [
        "븅신"
      ],
      "category": "insult",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.insult.deungsin",
      "expression": "등신",
      "variants": [],
      "category": "insult",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.insult.mock",
      "expression": "멍청이",
      "variants": [
        "찐따",
        "찌질이"
      ],
      "category": "insult",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.insult.dismiss",
      "expression": "꺼져",
      "variants": [
        "닥쳐"
      ],
      "category": "insult",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.family",
      "expression": "느금마",
      "variants": [
        "느그애미",
        "니애미",
        "니애비",
        "느개비"
      ],
      "category": "family",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": false,
      "auto_policy": "intrinsic",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.sexual.demand",
      "expression": "알몸사진보내",
      "variants": [
        "벗은사진보내",
        "야한사진보내",
        "몸사진보내"
      ],
      "category": "sexual",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.sexual.insult",
      "expression": "창녀같은년",
      "variants": [
        "걸레같은년"
      ],
      "category": "sexual",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.sexual.blackmail",
      "expression": "알몸사진퍼뜨린다",
      "variants": [
        "나체사진유포한다",
        "성폭행하겠다",
        "강간하겠다"
      ],
      "category": "sexual",
      "severity": "critical",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.disability",
      "expression": "애자",
      "variants": [
        "정공"
      ],
      "category": "disability",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.gender",
      "expression": "한남",
      "variants": [
        "한녀"
      ],
      "category": "gender",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": true,
      "enabled": true
    },
    {
      "id": "ko.origin.slur",
      "expression": "짱깨",
      "variants": [
        "쪽바리",
        "틀딱",
        "급식충"
      ],
      "category": "origin",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.origin.homonym",
      "expression": "홍어",
      "variants": [],
      "category": "origin",
      "severity": "high",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": true,
      "enabled": true
    },
    {
      "id": "ko.mockery",
      "expression": "잼민이",
      "variants": [
        "꼰대"
      ],
      "category": "mockery",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "review_only",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.mockery.homonym",
      "expression": "걸레",
      "variants": [],
      "category": "mockery",
      "severity": "medium",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "review_only",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": true,
      "enabled": true
    },
    {
      "id": "ko.threat.die",
      "expression": "죽어",
      "variants": [
        "죽어라",
        "뒤져",
        "뒤져라",
        "자살해",
        "자살해라",
        "죽어버려"
      ],
      "category": "threat",
      "severity": "critical",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "ko.threat.harm",
      "expression": "죽여버린다",
      "variants": [
        "죽이겠다",
        "죽여버리겠다",
        "죽여버릴거야",
        "찾아가서죽인다"
      ],
      "category": "threat",
      "severity": "critical",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [
        "아",
        "야",
        "이야",
        "이다",
        "이네",
        "이냐",
        "인가",
        "같다",
        "같아",
        "같네",
        "들",
        "들이",
        "새끼",
        "새끼야",
        "짓",
        "짓을",
        "처럼",
        "년",
        "이라고",
        "이라는",
        "이라며",
        "이래",
        "라고",
        "라는",
        "라며",
        "냐",
        "네",
        "야말로",
        "임",
        "입니다",
        "이에요",
        "을",
        "를",
        "은",
        "는",
        "이",
        "가",
        "도",
        "의",
        "한테"
      ],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "en.profanity.fuck",
      "expression": "fuck",
      "variants": [
        "fucking"
      ],
      "category": "english",
      "severity": "medium",
      "match_mode": "token",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "en.profanity.shit",
      "expression": "shit",
      "variants": [],
      "category": "english",
      "severity": "medium",
      "match_mode": "token",
      "context_required": true,
      "auto_policy": "review_only",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "en.insult",
      "expression": "bitch",
      "variants": [
        "asshole",
        "motherfucker"
      ],
      "category": "english",
      "severity": "high",
      "match_mode": "token",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "en.stfu",
      "expression": "stfu",
      "variants": [],
      "category": "english",
      "severity": "medium",
      "match_mode": "token",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "en.kys",
      "expression": "kys",
      "variants": [],
      "category": "threat",
      "severity": "critical",
      "match_mode": "token",
      "context_required": true,
      "auto_policy": "review_only",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    },
    {
      "id": "en.threat",
      "expression": "kill yourself",
      "variants": [],
      "category": "threat",
      "severity": "critical",
      "match_mode": "bounded",
      "context_required": true,
      "auto_policy": "targeted",
      "suffixes": [],
      "requires_hostility": false,
      "enabled": true
    }
  ]
}
'''

def _rules_data(path: str | Path | None = None) -> dict:
    return json.loads(_BUILTIN_RULES_JSON if path is None else Path(path).read_text(encoding='utf-8'))



# ===========================================================================
# engine
# ===========================================================================

"""Conservative, offline moderation rules, independent of Discord and SQLite.

Rule schema: id/expression/variants/category/severity/match_mode,
context_required/auto_policy/suffixes/requires_hostility/enabled.  Only escaped
literal patterns are accepted; users cannot install arbitrary regexes.

`bounded` permits at most two listed separators *between registered letters*.
Hangul is decomposed into all consonants AND vowels for equivalent spelling;
it is never converted to initials. Every atom retains its original offset.
Confidence labels are policy evidence levels, not statistical probabilities.
"""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

CATEGORIES = {
    "profanity": "일반 욕설·비속어", "shorthand": "초성·축약",
    "insult": "직접 모욕", "family": "패드립", "sexual": "성희롱·성적 모욕",
    "disability": "장애·질병 비하", "gender": "성별·성적 지향 비하",
    "origin": "출신·국적·지역·연령 비하", "mockery": "문맥 의존 조롱",
    "threat": "위협·괴롭힘", "english": "영어 욕설·축약",
}
ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff")
SEPARATOR_PATTERN = r"[ \t._@*~!^+|\-·,:/\\0-9]{0,2}"
MAX_TEXT = 20000
MAX_RULES = 500
MAX_ALLOW_TERMS = 500
_LEADING = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_TRAILING = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
_ATOMS = {
    **{chr(0x1100 + i): c for i, c in enumerate(_LEADING)},
    **{chr(0x1161 + i): c for i, c in enumerate(_VOWELS)},
    **{chr(0x11A8 + i): c for i, c in enumerate(_TRAILING)},
}
_WORD = re.compile(r"[\wㄱ-ㅎㅏ-ㅣ가-힣]", re.UNICODE)
_TARGET = re.compile(
    r"(?<![가-힣a-z0-9_])(?:너(?:는|가|를|한테|에게|희|네)?|네가|니가|니|"
    r"당신(?:은|이|을|들)?|넌|니들|너희들|쟤(?:는|가)?|걔(?:는|가)?|"
    r"저\s*사람(?:은|이)?|그\s*사람(?:은|이)?|you(?:'re|r)?|u)(?![가-힣a-z0-9_])"
    r"|<@!?\d{1,22}>", re.IGNORECASE,
)
_HOSTILE = re.compile(
    r"(?:꺼져|닥쳐|죽어|죽이|죽여|병신|븅신|개새끼|미친놈|미친년|혐오|"
    r"역겹|더럽|쓰레기|한심|같은\s*새끼|새끼(?:야|들|가)?|주제에|"
    r"stfu|kys|fuck\s+you|shut\s+up)", re.IGNORECASE,
)
_EDUCATION = re.compile(
    r"(?:욕설|비속어|금칙어|혐오\s*표현|모욕\s*표현|단어|표현|용어).{0,25}"
    r"(?:뜻|의미|예시|예를|설명|사용하지|쓰지\s*마|금지|교육|해당|인가요|인가요)"
    r"|(?:뜻|의미|예시|설명|교육).{0,25}(?:욕설|비속어|혐오\s*표현)"
    r"|(?:라는|이라는)\s*(?:말|표현|단어).{0,20}(?:뜻|의미|금지|쓰지|사용하지)",
)
_REPORT_GOAL = re.compile(r"신고|도와|도움|괴롭힘|괴롭혀|상담|피해|증거|인용|기록")
_NEGATION = re.compile(
    r"아니(?:야|다|에요|라|라고|고)|아닙|아닌|하지\s*(?:마|말)|하면\s*안|"
    r"쓰지\s*(?:마|말)|사용하지|말하지\s*(?:마|말)|부르지\s*(?:마|말)|"
    r"부르면\s*안|불러서는\s*안|하지\s*않|금지",
)
_ATTRIBUTION = re.compile(
    r"(?:누가|상대(?:방)?(?:가|는)|그\s*사람(?:이|은)|[가-힣]{1,12}님이|"
    r"저에게|나에게|제게|저한테|나한테).{0,100}"
    r"(?:라고|이라고|라는|했|말했|들었|보냈|받았)"
    r"|(?:라고|이라고|라는)\s*(?:말을?\s*)?"
    r"(?:들었|들어|했어|해서|했다|했대|말했|말한|말하는|보냈|보낸|받았|하더)",
)
_GAME = re.compile(
    r"몬스터|좀비|보스|npc|게임\s*(?:캐릭터|공략|에서|속|내|중|하다|하던|할\s*때)|"
    r"캐릭터|적\s*팀|킬\s*각", re.IGNORECASE,
)
_TECHNICAL = re.compile(r"절연|송전|전기\s*부품|부품|전압|변압기|제조\s*공정|공업|사업부")
_SENTENCE_BREAK = re.compile(r"[.!?;\n。！？]")


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    category: str
    start: int
    end: int
    matched: str
    severity: str
    confidence: str
    reason: str
    recommendation: str
    source: str = "text"
    attachment_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Normalized:
    text: str
    positions: tuple[int, ...]


def _normalize(text: str) -> _Normalized:
    """NFKC + casefold + full Hangul decomposition, preserving source indices."""
    out: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char in ZERO_WIDTH:
            continue
        for atom in unicodedata.normalize("NFD", unicodedata.normalize("NFKC", char).casefold()):
            if atom in ZERO_WIDTH:
                continue
            out.append(_ATOMS.get(atom, atom))
            positions.append(index)
    return _Normalized("".join(out), tuple(positions))


def _context_normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", "".join(c for c in text if c not in ZERO_WIDTH)).casefold()


def _word_char(char: str) -> bool:
    return bool(char and _WORD.fullmatch(char))


def _original_span(norm: _Normalized, start: int, end: int) -> tuple[int, int] | None:
    if start == end:
        return None
    # Never match only a consonant inside a normal syllable, e.g. ㅄ inside 값.
    if start and norm.positions[start - 1] == norm.positions[start]:
        return None
    if end < len(norm.text) and norm.positions[end - 1] == norm.positions[end]:
        return None
    return norm.positions[start], norm.positions[end - 1] + 1


def _compile(expression: str, mode: str) -> re.Pattern:
    normalized = _normalize(expression).text
    if mode == "bounded":
        # Optional spaces apply only inside this explicitly registered expression.
        normalized = normalized.replace(" ", "").replace("\t", "")
        if not normalized or not all(("a" <= c <= "z") or ("ㄱ" <= c <= "ㅣ") for c in normalized):
            raise ValueError("제한변형은 한글·자모·영문과 공백만 등록할 수 있습니다.")
        pattern = SEPARATOR_PATTERN.join(re.escape(c) for c in normalized)
    else:
        pattern = re.escape(normalized)
    if not pattern:
        raise ValueError("빈 규칙은 등록할 수 없습니다.")
    return re.compile(pattern)


def validate_custom_rule(expression: str, category: str, match_mode: str) -> dict:
    """Validate a literal custom rule; assigning an ID is the caller's job.

    review_only is intentional. A separately authorized admin choice may change
    auto_policy to targeted. Neither this function nor Engine accepts regexes.
    """
    if not isinstance(expression, str):
        raise ValueError("표현은 문자열이어야 합니다.")
    expression = expression.strip()
    if not 2 <= len(expression) <= 64:
        raise ValueError("사용자 규칙은 2~64자로 입력하세요.")
    if any(unicodedata.category(c).startswith("C") and c not in ZERO_WIDTH for c in expression):
        raise ValueError("제어 문자·줄바꿈은 등록할 수 없습니다.")
    if category not in CATEGORIES:
        raise ValueError("지원하지 않는 분류입니다.")
    if match_mode not in {"token", "bounded", "literal"}:
        raise ValueError("일치방식은 token, bounded, literal 중 하나입니다.")
    if len(_normalize(expression).text.replace(" ", "")) < 2:
        raise ValueError("정규화 후 두 글자 이상의 표현이 필요합니다.")
    _compile(expression, match_mode)
    return {
        "id": "", "expression": expression, "variants": [], "category": category,
        "severity": "medium", "match_mode": match_mode, "context_required": True,
        "auto_policy": "review_only", "suffixes": [], "requires_hostility": False,
        "enabled": True,
    }


def validate_allow_term(expression: str) -> str:
    """Validate before saving an administrator's literal, span-scoped exception."""
    if not isinstance(expression, str):
        raise ValueError("허용어는 문자열이어야 합니다.")
    expression = expression.strip()
    if not 1 <= len(expression) <= 128:
        raise ValueError("허용어는 1~128자로 입력하세요.")
    if any(unicodedata.category(c).startswith("C") and c not in ZERO_WIDTH for c in expression):
        raise ValueError("허용어에 제어 문자·줄바꿈을 넣을 수 없습니다.")
    _compile(expression, "literal")
    return expression


def _validate_rule(rule: Mapping) -> dict:
    rule = dict(rule)
    if not isinstance(rule.get("id"), str) or not rule["id"] or len(rule["id"]) > 100:
        raise ValueError("규칙 ID가 잘못되었습니다.")
    if rule.get("category") not in CATEGORIES:
        raise ValueError(f"분류가 잘못되었습니다: {rule['id']}")
    if rule.get("severity") not in {"low", "medium", "high", "critical"}:
        raise ValueError(f"심각도가 잘못되었습니다: {rule['id']}")
    if rule.get("match_mode") not in {"token", "bounded", "literal"}:
        raise ValueError(f"일치방식이 잘못되었습니다: {rule['id']}")
    if rule.get("auto_policy") not in {"targeted", "review_only", "intrinsic"}:
        raise ValueError(f"판단 정책이 잘못되었습니다: {rule['id']}")
    if not isinstance(rule.get("context_required"), bool):
        raise ValueError("context_required는 bool이어야 합니다.")
    variants = rule.get("variants", [])
    suffixes = rule.get("suffixes", [])
    if not isinstance(variants, list) or len(variants) > 40 or not isinstance(suffixes, list) or len(suffixes) > 64:
        raise ValueError("변형·접미사 개수가 너무 많거나 형식이 잘못되었습니다.")
    for item in [rule.get("expression"), *variants, *suffixes]:
        if not isinstance(item, str) or len(item) > 64:
            raise ValueError("표현·변형·접미사는 64자 이하 문자열이어야 합니다.")
        if any(unicodedata.category(c).startswith("C") and c not in ZERO_WIDTH for c in item):
            raise ValueError("규칙에 제어 문자를 넣을 수 없습니다.")
    if not rule["expression"]:
        raise ValueError("기본 표현이 비어 있습니다.")
    return rule


def _local_clause(text: str, start: int, end: int) -> tuple[int, int]:
    left = max(0, start - 120)
    right = min(len(text), end + 120)
    for match in _SENTENCE_BREAK.finditer(text, left, start):
        left = match.end()
    match = _SENTENCE_BREAK.search(text, end, right)
    if match:
        right = match.start()
    return left, right


def _quoted_ranges(text: str) -> list[tuple[int, int]]:
    """Bounded linear scans; a quote is evidence only with additional context."""
    ranges: list[tuple[int, int]] = []
    # Triple backticks and single backticks are considered content, not an exemption.
    for opener, closer in [("```", "```"), ("`", "`"), ('"', '"'), ("'", "'"),
                           ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』")]:
        offset = 0
        while offset < len(text):
            opening = text.find(opener, offset)
            if opening < 0:
                break
            closing = text.find(closer, opening + len(opener))
            if closing < 0:
                break
            ranges.append((opening, closing + len(closer)))
            offset = closing + len(closer)
    return ranges


class Engine:
    def __init__(self, rules: Iterable[Mapping], allow_terms: Iterable[str] = ()):
        rules = list(rules)
        if len(rules) > MAX_RULES:
            raise ValueError(f"규칙은 기본·사용자 합계 {MAX_RULES}개까지 지원합니다.")
        validated = [_validate_rule(r) for r in rules if r.get("enabled", True)]
        if len({r["id"] for r in validated}) != len(validated):
            raise ValueError("규칙 ID가 중복되었습니다.")
        self.rules = tuple(validated)
        self.allow_terms = tuple(dict.fromkeys(allow_terms))
        if len(self.allow_terms) > MAX_ALLOW_TERMS:
            raise ValueError("허용어가 너무 많습니다.")
        self._patterns: list[tuple[dict, re.Pattern]] = []
        for rule in self.rules:
            for expression in dict.fromkeys([rule["expression"], *rule.get("variants", [])]):
                self._patterns.append((rule, _compile(expression, rule["match_mode"])))
        self._allow_patterns = []
        for term in self.allow_terms:
            validate_allow_term(term)
            self._allow_patterns.append(_compile(term, "literal"))

    @classmethod
    def from_file(cls, path: str | Path | None = None, custom_rules: Iterable[Mapping] = (),
                  disabled_ids: Iterable[str] = (), allow_terms: Iterable[str] = ()) -> "Engine":
        data = _rules_data(path)
        if data.get("version") != 1 or not isinstance(data.get("rules"), list):
            raise ValueError("지원하지 않는 기본 사전 형식입니다.")
        disabled = set(disabled_ids)
        custom_rules = list(custom_rules)
        base_ids = {r.get("id") for r in data["rules"]}
        if any(r.get("id") in base_ids for r in custom_rules):
            raise ValueError("사용자 규칙은 기본 규칙 ID를 덮어쓸 수 없습니다.")
        rules = [r for r in [*data["rules"], *custom_rules] if r.get("id") not in disabled]
        return cls(rules, [*data.get("allow_terms", []), *allow_terms])

    @staticmethod
    def _boundary_ok(text: str, start: int, end: int, rule: Mapping) -> bool:
        if rule["match_mode"] == "literal":
            return True
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if _word_char(before):
            return False
        if not _word_char(after):
            return True
        if rule["match_mode"] == "token":
            return False
        for suffix in rule.get("suffixes", []):
            if suffix and text.startswith(suffix, end):
                suffix_end = end + len(suffix)
                if suffix_end == len(text) or not _word_char(text[suffix_end]):
                    return True
        return False

    def scan(self, text: str, category_modes: Mapping[str, str] | None = None) -> list[Finding]:
        if not isinstance(text, str):
            raise TypeError("검사할 본문은 문자열이어야 합니다.")
        if len(text) > MAX_TEXT:
            raise ValueError(f"검사 본문은 {MAX_TEXT}자 이하여야 합니다.")
        if not text:
            return []
        category_modes = category_modes or {}
        if any(value not in {"enforce", "review", "off"} for value in category_modes.values()):
            raise ValueError("분류 설정은 enforce, review, off 중 하나여야 합니다.")
        normalized = _normalize(text)
        allowed: list[tuple[int, int]] = []
        for pattern in self._allow_patterns:
            for match in pattern.finditer(normalized.text):
                span = _original_span(normalized, *match.span())
                if span:
                    allowed.append(span)
        quoted = _quoted_ranges(text)
        findings: list[Finding] = []
        seen: set[tuple[str, int, int]] = set()
        for rule, pattern in self._patterns:
            mode = category_modes.get(rule["category"], "enforce")
            if mode == "off":
                continue
            for match in pattern.finditer(normalized.text):
                span = _original_span(normalized, *match.span())
                if span is None:
                    continue
                start, end = span
                key = (rule["id"], start, end)
                if key in seen or not self._boundary_ok(text, start, end, rule):
                    continue
                if any(a <= start and end <= b for a, b in allowed):
                    continue
                seen.add(key)
                left, right = _local_clause(text, start, end)
                clause = _context_normalize(text[left:right])
                target = bool(_TARGET.search(clause))
                hostile = bool(_HOSTILE.search(clause))
                if rule["category"] in {"gender", "origin", "disability"}:
                    group_tail = _context_normalize(text[end:min(right, end + 40)])
                    # A named group plus a hostile generalization is direct targeting.
                    target = target or bool(re.match(
                        r"(?:들(?:은|이|는)?|은|는|이|가)?\s*(?:다|모두|전부)\s*"
                        r"(?:죽어|꺼져|쓰레기|역겹|더럽|한심)", group_tail,
                    ))
                # Homonyms such as 홍어 and 걸레 need both targeting and a separate
                # hostile predicate; ordinary food/cleaning sentences are not logged.
                if rule.get("requires_hostility") and not (target and hostile):
                    continue
                # Calendar term is exempt only where a calendar explanation exists.
                if rule["id"] == "ko.insult.byeongsin" and text.startswith("년", end):
                    if re.search(r"2016\s*년|丙申|육십갑자|간지|병신년의\s*뜻", clause):
                        continue
                containing_quote = next(((a, b) for a, b in quoted if a <= start and end <= b), None)
                education = bool(_EDUCATION.search(clause))
                attribution = bool(_ATTRIBUTION.search(clause))
                report = bool(_REPORT_GOAL.search(clause) and attribution)
                # Reported speech includes a concrete reporting verb, not merely
                # quotation marks or the word "신고합니다".
                after_match = _context_normalize(text[end:min(right, end + 50)])
                report = report or bool(attribution and re.match(
                    r"(?:[\"'’”`]*)(?:이?라고|라는)\s*(?:말을?\s*)?"
                    r"(?:들었|들어|했어|했다|했대|해서|말했|말한|말하는|보냈|보낸|받았|하더)",
                    after_match,
                ))
                if containing_quote:
                    qa, qb = containing_quote
                    # Scope attribution to this quote's sentence, so an earlier
                    # report cannot excuse a separate later quoted attack.
                    quote_left, quote_right = _local_clause(text, qa, qb)
                    surrounding = _context_normalize(text[quote_left:quote_right])
                    report = report or bool(_ATTRIBUTION.search(surrounding))
                    education = education or bool(_EDUCATION.search(surrounding))
                confidence = "review"
                reasons = ["등록된 표현과 제한된 변형이 일치"]
                negation = bool(_NEGATION.search(_context_normalize(text[end:min(right, end + 45)])))
                if education or report or negation:
                    reasons.append("인용·신고·설명 문맥의 추가 근거가 있어 자동 제재 보류")
                elif rule["category"] == "threat" and _GAME.search(clause):
                    reasons.append("게임·캐릭터 문맥 가능성으로 관리자 검토 필요")
                elif rule["category"] == "disability" and not hostile and _TECHNICAL.search(clause):
                    reasons.append("기술 용어·부품의 동음이의어 문맥 가능성으로 관리자 검토 필요")
                elif rule["auto_policy"] == "intrinsic":
                    confidence = "clear"
                    reasons.append("표현 자체에 직접적인 모욕 대상이 포함됨")
                elif rule["auto_policy"] == "targeted" and target:
                    confidence = "clear"
                    reasons.append("같은 문장 구간에서 명시적인 공격 대상 확인")
                else:
                    reasons.append("감탄·축약·동음이의어 또는 공격 대상의 근거가 부족함")
                if mode == "review":
                    confidence = "review"
                    reasons.append("관리자가 이 분류를 검토 전용으로 설정")
                findings.append(Finding(
                    rule_id=rule["id"], category=rule["category"], start=start, end=end,
                    matched=text[start:end], severity=rule["severity"], confidence=confidence,
                    reason="; ".join(reasons),
                    recommendation="enforce" if confidence == "clear" else "review",
                ))
        findings.sort(key=lambda f: (f.start, f.end, f.rule_id))
        return findings



# ===========================================================================
# ocr
# ===========================================================================

"""Bounded, offline OCR subprocess manager; no Discord API or image downloads.

One persistent child loads EasyOCR once. Decode/inference never run in the bot
event loop. A timed-out child is killed and reaped, rather than abandoned in a
thread. Inputs/outputs stay in memory; only the explicit installer fetches models.
"""

import asyncio
import base64
from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 8192
MAX_OCR_LINES = 200
MAX_OCR_CHARACTERS = 8000
STRONG_OCR_SCORE = 0.85  # Model score threshold, NOT a calibrated probability.
_PROJECT_ROOT = ROOT


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    confidence: float
    bbox: tuple[tuple[float, float], ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OCRResult:
    status: str
    lines: list[OCRLine] = field(default_factory=list)
    detail: str = ""
    width: int = 0
    height: int = 0
    sha256: str = ""

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class _Job:
    data: bytes
    filename: str
    mime: str | None
    digest: str
    queued_at: float
    future: asyncio.Future


class OCRPool:
    """Single worker; at most ``max_queue`` waiting images plus one active image.

    ``await start()`` returns readiness. ``ready`` is bool; ``health`` is a
    metadata-only dict. ``submit`` always returns an OCRResult unless its caller
    is cancelled. Call ``await close()`` on Cog unload. Closed pools cannot reopen.
    """

    def __init__(self, model_dir: str | Path, max_queue: int = 8,
                 timeout_seconds: float = 30):
        if not 1 <= max_queue <= 32 or not 0.1 <= timeout_seconds <= 120:
            raise ValueError("OCR queue must be 1–32; timeout must be 0.1–120 seconds")
        self.model_dir = Path(model_dir).expanduser().resolve()
        self.timeout_seconds = float(timeout_seconds)
        self.startup_timeout_seconds = 120.0
        self.restart_cooldown_seconds = 60.0
        self.restart_window_seconds = 300.0
        self.max_starts_per_window = 3
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=max_queue)
        self._proc: asyncio.subprocess.Process | None = None
        self._runner: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = None
        self._process_lock = asyncio.Lock()
        self._starts: deque[float] = deque()
        self._cooldown_until = 0.0
        self._closed = False
        self._ready = False
        self._state = "not_started"
        self._detail = "OCR 모델 초기화 전"

    @property
    def ready(self) -> bool:
        return bool(self._ready and self._proc and self._proc.returncode is None and not self._closed)

    @property
    def readiness(self) -> str:
        return self._state

    @property
    def last_error(self) -> str:
        return "" if self.ready else self._detail

    @property
    def health(self) -> dict[str, Any]:
        return {
            "ready": self.ready, "state": self._state, "detail": self._detail,
            "queue_size": self._queue.qsize(), "queue_limit": self._queue.maxsize,
            "worker_pid": self._proc.pid if self._proc and self._proc.returncode is None else None,
            "cooldown_seconds": max(0, math.ceil(self._cooldown_until - time.monotonic())),
        }

    async def start(self) -> bool:
        if self._closed:
            return False
        if self._startup_task is None or self._startup_task.done():
            self._startup_task = asyncio.create_task(self._start_locked(), name="automod-ocr-start")
        try:
            ready = await asyncio.shield(self._startup_task)
        except asyncio.CancelledError:
            if self._closed:
                return False
            raise
        if not self._closed and (self._runner is None or self._runner.done()):
            self._runner = asyncio.create_task(self._run(), name="automod-ocr-queue")
        return ready

    async def _start_locked(self) -> bool:
        async with self._process_lock:
            return await self._ensure_worker()

    async def _ensure_worker(self) -> bool:
        if self._closed:
            return False
        if self.ready:
            return True
        now = time.monotonic()
        if now < self._cooldown_until:
            return False
        while self._starts and self._starts[0] <= now - self.restart_window_seconds:
            self._starts.popleft()
        if len(self._starts) >= self.max_starts_per_window:
            self._cooldown_until = self._starts[0] + self.restart_window_seconds
            self._state = "cooldown"
            self._detail = "OCR 재시작 횟수 제한에 도달하여 일시 중지"
            return False
        await self._stop_process()
        if self._closed:
            return False
        self._starts.append(now)
        self._state, self._detail = "starting", "OCR 한국어·영어 CPU 모델 초기화 중"
        env = dict(os.environ)
        env.update({"PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
        try:
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable, str(_SINGLE_FILE), "--ocr-worker", "--model-dir", str(self.model_dir),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=str(_PROJECT_ROOT), env=env,
                limit=1024 * 1024,
            )
            assert self._proc.stdout is not None
            raw = await asyncio.wait_for(self._proc.stdout.readline(), self.startup_timeout_seconds)
            payload = json.loads(raw)
            if payload.get("type") != "ready" or payload.get("status") != "ready":
                detail = str(payload.get("detail", "OCR 초기화 실패"))[:240]
                await self._unavailable(detail)
                return False
            self._ready, self._state, self._detail = True, "ready", "한국어·영어 CPU OCR 준비 완료"
            return True
        except asyncio.CancelledError:
            await self._stop_process()
            raise
        except Exception as exc:
            await self._unavailable(f"OCR 초기화 실패: {type(exc).__name__}")
            return False

    async def _stop_process(self) -> None:
        proc, self._proc = self._proc, None
        self._ready = False
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        # The child is always reaped, including timeout, cancellation and unload.
        await proc.wait()

    async def _unavailable(self, detail: str) -> None:
        await self._stop_process()
        self._state, self._detail = "unavailable", detail
        self._cooldown_until = time.monotonic() + self.restart_cooldown_seconds

    async def submit(self, data: bytes, filename: str, declared_mime: str | None = None) -> OCRResult:
        if self._closed:
            return OCRResult("closed", detail="OCR가 종료됨")
        if not isinstance(data, bytes) or not data:
            return OCRResult("invalid", detail="빈 이미지 또는 bytes가 아닌 입력")
        if len(data) > MAX_IMAGE_BYTES:
            return OCRResult("too_large", detail="이미지 최대 8 MiB 초과")
        digest = hashlib.sha256(data).hexdigest()
        if not isinstance(filename, str) or (declared_mime is not None and (
            not isinstance(declared_mime, str) or len(declared_mime) > 128
        )):
            return OCRResult("invalid", detail="이미지 파일 이름·MIME 메타데이터 형식 오류", sha256=digest)
        if not self._runner or self._runner.done():
            return OCRResult("unavailable", detail="OCR를 먼저 초기화해야 함", sha256=digest)
        if not self.ready and time.monotonic() < self._cooldown_until:
            return OCRResult("unavailable", detail=self._detail, sha256=digest)
        future = asyncio.get_running_loop().create_future()
        job = _Job(data, str(filename)[:255], declared_mime, digest, time.monotonic(), future)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            return OCRResult("busy", detail="OCR 대기열이 가득 차 검사 보류", sha256=digest)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def _run(self) -> None:
        while not self._closed:
            job = await self._queue.get()
            try:
                if job.future.cancelled():
                    continue
                if time.monotonic() - job.queued_at > self.timeout_seconds:
                    result = OCRResult("timeout", detail="OCR 대기 시간 초과", sha256=job.digest)
                else:
                    async with self._process_lock:
                        result = await self._perform(job)
                if not job.future.done():
                    job.future.set_result(result)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.set_result(OCRResult("closed", detail="OCR 종료로 검사 취소", sha256=job.digest))
                raise
            except Exception as exc:
                await self._unavailable(f"OCR 관리 오류: {type(exc).__name__}")
                if not job.future.done():
                    job.future.set_result(OCRResult("error", detail=self._detail, sha256=job.digest))
            finally:
                job.data = b""
                self._queue.task_done()

    async def _perform(self, job: _Job) -> OCRResult:
        if not await self._ensure_worker():
            return OCRResult("unavailable", detail=self._detail, sha256=job.digest)
        try:
            payload = await asyncio.wait_for(self._exchange(job), self.timeout_seconds)
            return _parse_result(payload, job.digest)
        except asyncio.CancelledError:
            await self._stop_process()
            raise
        except TimeoutError:
            await self._unavailable("OCR 처리 시간 초과: 작업 프로세스를 종료함")
            return OCRResult("timeout", detail=self._detail, sha256=job.digest)
        except (ValueError, OSError, asyncio.IncompleteReadError) as exc:
            await self._unavailable(f"OCR 작업 실패: {type(exc).__name__}")
            return OCRResult("error", detail=self._detail, sha256=job.digest)

    async def _exchange(self, job: _Job) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise OSError("OCR process unavailable")
        request = {
            "data": base64.b64encode(job.data).decode("ascii"),
            "filename": job.filename, "mime": job.mime,
        }
        proc.stdin.write(json.dumps(request, ensure_ascii=True).encode("utf-8") + b"\n")
        await proc.stdin.drain()
        raw = await proc.stdout.readline()
        if not raw:
            raise OSError("OCR worker exited before replying")
        return json.loads(raw)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = [task for task in (self._startup_task, self._runner) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._stop_process()
        while not self._queue.empty():
            job = self._queue.get_nowait()
            job.data = b""
            if not job.future.done():
                job.future.set_result(OCRResult("closed", detail="OCR 종료로 대기 검사 취소", sha256=job.digest))
            self._queue.task_done()
        self._state, self._detail = "closed", "OCR 종료 완료"


def _parse_result(payload: dict, digest: str) -> OCRResult:
    if not isinstance(payload, dict):
        raise ValueError("Invalid OCR result")
    status = payload.get("status")
    if status not in {"ok", "no_text", "unsupported", "invalid", "too_large", "error", "truncated"}:
        raise ValueError("Invalid OCR result status")
    raw_lines = payload.get("lines", [])
    if not isinstance(raw_lines, list) or len(raw_lines) > MAX_OCR_LINES:
        raise ValueError("Invalid OCR line count")
    lines: list[OCRLine] = []
    total = 0
    for line in raw_lines:
        text, score = line["text"], float(line["confidence"])
        if not isinstance(text, str) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid OCR evidence")
        total += len(text)
        if total > MAX_OCR_CHARACTERS:
            raise ValueError("OCR evidence exceeds limit")
        bbox = tuple(tuple(float(n) for n in point) for point in line.get("bbox", []))
        if len(bbox) != 4 or any(len(point) != 2 or not all(math.isfinite(n) for n in point) for point in bbox):
            raise ValueError("Invalid OCR bounding box")
        lines.append(OCRLine(text, score, bbox))
    width, height = int(payload.get("width", 0)), int(payload.get("height", 0))
    if width < 0 or height < 0 or width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError("Invalid OCR dimensions")
    return OCRResult(status, lines, str(payload.get("detail", ""))[:240], width, height, digest)



# ===========================================================================
# ocr_worker
# ===========================================================================

"""Private JSON-lines worker. Run via OCRPool; never downloads remote images.

Pillow validates actual file headers and decodes in this disposable process.
EasyOCR loads once with Korean+English, CPU only and downloads disabled.
"""

import argparse
import base64
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import socket
import sys
import warnings


_FORMATS = {
    "PNG": ({".png"}, {"image/png"}),
    "JPEG": ({".jpg", ".jpeg", ".jpe"}, {"image/jpeg", "image/jpg"}),
    "WEBP": ({".webp"}, {"image/webp"}),
}
_MAX_REQUEST = (MAX_IMAGE_BYTES * 4 // 3) + 8192


class ImageRejected(ValueError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _decode_image(data: bytes, filename: str, declaredmime: str | None):
    """Return a fully decoded RGB image; caller closes it. No files are created."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageRejected("too_large" if data else "invalid", "빈 파일 또는 최대 8 MiB 초과")
    mime = (declaredmime or "").split(";", 1)[0].strip().lower()
    extension = Path(filename).suffix.lower()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                actual = probe.format
                if actual not in _FORMATS:
                    raise ImageRejected("unsupported", "PNG·JPEG·정지 WEBP만 지원; GIF·영상은 검사하지 않음")
                extensions, mimes = _FORMATS[actual]
                if extension and extension not in extensions:
                    raise ImageRejected("invalid", "파일 확장자와 실제 이미지 형식 불일치")
                if mime and mime not in mimes:
                    raise ImageRejected("invalid", "선언된 MIME과 실제 이미지 형식 불일치")
                if getattr(probe, "is_animated", False) or getattr(probe, "n_frames", 1) != 1:
                    raise ImageRejected("unsupported", "움직이는 이미지의 일부 프레임만으로 판단하지 않음")
                width, height = probe.size
                if min(width, height) <= 0 or max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
                    raise ImageRejected("too_large", "최대 8192px/변 또는 1200만 화소 초과")
                probe.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                with ImageOps.exif_transpose(source) as oriented:
                    return oriented.convert("RGB")
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageRejected("invalid", f"이미지 안전 디코딩 실패: {type(exc).__name__}") from exc


def recognize(reader, data: bytes, filename: str, declaredmime: str | None) -> dict:
    """Pure worker operation, injectable reader permits deterministic unit tests."""
    width = height = 0
    try:
        image = _decode_image(data, filename, declaredmime)
    except ImageRejected as exc:
        return {"status": exc.status, "detail": exc.detail, "lines": []}
    try:
        import numpy as np

        with image:
            width, height = image.size
            pixels = np.asarray(image)
            raw_results = reader.readtext(
                pixels, detail=1, paragraph=False, decoder="greedy", batch_size=1,
                workers=0, canvas_size=2048, mag_ratio=1.0,
            )
        lines = []
        characters = 0
        truncated = False
        for bbox, text, score in raw_results:
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()
            score = float(score)
            if not math.isfinite(score) or not 0 <= score <= 1:
                return {"status": "error", "detail": "OCR 모델 점수 형식 오류", "lines": [], "width": width, "height": height}
            if len(lines) >= MAX_OCR_LINES or characters + len(text) > MAX_OCR_CHARACTERS:
                truncated = True
                break
            points = [[float(x), float(y)] for x, y in bbox]
            if len(points) != 4 or not all(math.isfinite(n) for point in points for n in point):
                return {"status": "error", "detail": "OCR 영역 좌표 형식 오류", "lines": [], "width": width, "height": height}
            lines.append({"text": text, "confidence": score, "bbox": points})
            characters += len(text)
        status = "truncated" if truncated else ("ok" if lines else "no_text")
        detail = "인식 결과 제한 초과: 자동 제재 없이 검토 필요" if truncated else (
            "문자를 인식하지 못함; 안전하다는 판정이 아님" if not lines else "한국어·영어 OCR 완료"
        )
        return {"status": status, "lines": lines, "detail": detail, "width": width, "height": height}
    except Exception as exc:
        return {"status": "error", "lines": [], "detail": f"OCR 추론 오류: {type(exc).__name__}", "width": width, "height": height}


def _block_runtime_network() -> None:
    def denied(*args, **kwargs):
        raise OSError("OCR runtime network is disabled")

    socket.create_connection = denied
    socket.getaddrinfo = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied


def _send(stream, payload: dict) -> None:
    stream.write(json.dumps(payload, ensure_ascii=True, allow_nan=False) + "\n")
    stream.flush()


def _ocr_worker_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private offline AutoMod OCR worker")
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    protocol_out = sys.stdout
    model_dir = args.model_dir.resolve()
    _block_runtime_network()
    try:
        if not model_dir.is_dir():
            raise FileNotFoundError("모델 디렉터리가 없음; automod.py --install-ocr-models를 먼저 실행하세요")
        with redirect_stdout(sys.stderr):
            import torch
            import numpy as np
            import easyocr

            torch.set_num_threads(2)
            torch.set_num_interop_threads(1)
            reader = easyocr.Reader(
                ["ko", "en"], gpu=False, model_storage_directory=str(model_dir),
                user_network_directory=str(model_dir / "user_network"),
                download_enabled=False, verbose=False,
            )
            # Warm up the actual detector; startup also has a hard parent timeout.
            reader.readtext(np.full((64, 256, 3), 255, dtype=np.uint8), workers=0, batch_size=1)
        _send(protocol_out, {"type": "ready", "status": "ready"})
    except Exception as exc:
        _send(protocol_out, {"type": "ready", "status": "unavailable", "detail":
                            f"OCR 초기화 실패: {type(exc).__name__}; 설치 스크립트·모델 파일을 확인하세요"})
        return 2
    while True:
        raw = sys.stdin.buffer.readline(_MAX_REQUEST + 1)
        if not raw:
            return 0
        if len(raw) > _MAX_REQUEST or not raw.endswith(b"\n"):
            _send(protocol_out, {"status": "invalid", "lines": [], "detail": "OCR 요청 크기 초과"})
            return 3
        try:
            request = json.loads(raw)
            data = base64.b64decode(request["data"], validate=True)
            with redirect_stdout(sys.stderr):
                result = recognize(reader, data, str(request.get("filename", "")), request.get("mime"))
            del data
        except Exception as exc:
            result = {"status": "invalid", "lines": [], "detail": f"OCR 입력 오류: {type(exc).__name__}"}
        del raw
        _send(protocol_out, result)
        # Do not retain the previous request's base64 image or recognized text.
        request = None
        result = None



# ===========================================================================
# 명시적인 최초 모델 설치
# ===========================================================================

"""Explicit one-time download/validation of EasyOCR Korean+English CPU models.

Run from any directory: python /path/to/cogs/automod.py --install-ocr-models
Normal bot operation always uses download_enabled=False.
"""

import argparse
import hashlib
import json
from pathlib import Path


def _install_models_main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Install EasyOCR Korean+English CPU model files")
    parser.add_argument("--model-dir", type=Path, default=MODELS_PATH)
    parser.add_argument("--check-only", action="store_true", help="Validate local files without downloading")
    args = parser.parse_args(argv)
    model_dir = args.model_dir.expanduser().resolve()
    try:
        import torch
        import easyocr

        model_dir.mkdir(parents=True, exist_ok=True)
        torch.set_num_threads(2)
        reader = easyocr.Reader(
            ["ko", "en"], gpu=False, model_storage_directory=str(model_dir),
            user_network_directory=str(model_dir / "user_network"),
            download_enabled=not args.check_only, verbose=False,
        )
        del reader
        manifest = {
            "easyocr_version": easyocr.__version__, "languages": ["ko", "en"], "device": "cpu",
            "models": [
                {"file": path.name, "bytes": path.stat().st_size,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(model_dir.glob("*.pth"))
            ],
        }
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print("OCR 모델 확인 완료. 봇 실행 중에는 모델을 내려받지 않습니다.")
        return 0
    except Exception as exc:
        print(f"OCR 모델 설치/확인 실패: {type(exc).__name__}: {exc}")
        print("EasyOCR·CPU PyTorch 패키지 설치 및 네트워크/모델 경로를 확인하세요.")
        return 1



# ===========================================================================
# 외부 연결 없는 CLI / OCR 진입점
# ===========================================================================

def _single_file_cli() -> int:
    """Local diagnostics/model setup only; never loads Discord/Firebase clients."""
    from importlib import metadata

    parser = argparse.ArgumentParser(
        description="단일 파일 Discord 자동관리 Cog: 설치·로컬 점검 도구",
        epilog="봇은 기존 bot.py로 실행하세요. 이 CLI는 Discord에 로그인하지 않습니다.",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="패키지·내장 사전·모델 파일·환경변수 유무 확인")
    actions.add_argument("--install-ocr-models", action="store_true", help="한국어·영어 CPU 모델 최초 다운로드")
    actions.add_argument("--check-ocr-models", action="store_true", help="네트워크 없이 실제 모델 초기화 확인")
    actions.add_argument("--ocr-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--model-dir", type=Path, default=MODELS_PATH, help="OCR 모델 폴더")
    args = parser.parse_args()
    model_dir = args.model_dir.expanduser()
    if not model_dir.is_absolute():
        model_dir = ROOT / model_dir
    model_dir = model_dir.resolve()
    if args.ocr_worker:
        return _ocr_worker_main(["--model-dir", str(model_dir)])
    if args.install_ocr_models or args.check_ocr_models:
        install_args = ["--model-dir", str(model_dir)]
        if args.check_ocr_models:
            install_args.append("--check-only")
        return _install_models_main(install_args)
    if args.check:
        required = ["discord.py", "firebase-admin", "google-cloud-firestore",
                    "google-cloud-storage", "aiohttp", "easyocr", "torch",
                    "torchvision", "numpy", "Pillow", "tzdata"]
        installed, missing, conflicting = {}, [], []
        for name in required:
            try:
                installed[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                missing.append(name)
        for name in ("py-cord", "discord.py-self"):
            try:
                conflicting.append(f"{name}=={metadata.version(name)}")
            except metadata.PackageNotFoundError:
                pass
        model_files = {name: (model_dir / name).is_file()
                       for name in ("craft_mlt_25k.pth", "korean_g2.pth")}
        engine = Engine.from_file()
        values = {
            "version": __version__, "python": sys.version.split()[0],
            "packages": installed, "missing_packages": missing,
            "conflicting_discord_packages": conflicting,
            "embedded_rule_groups": len(engine.rules),
            "model_dir": str(model_dir), "model_files_present": model_files,
            "firebase_project_configured": bool(os.environ.get("FIREBASE_PROJECT_ID", "").strip()),
            "credentials": "explicit_path" if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") else "ADC",
            "external_operations": "none",
            "note": "패키지·파일 존재 확인이며 OCR 실제 인식·Firebase 연결·Discord 권한 검증이 아닙니다.",
        }
        print(json.dumps(values, ensure_ascii=False, indent=2))
        return 0 if (not missing and not conflicting and all(model_files.values())
                     and values["firebase_project_configured"]) else 1
    parser.print_help()
    return 0


# Exit before any Firebase/Discord imports in CLI/worker mode. The bot imports
# this file as an extension and continues into the service and Cog definitions.
if __name__ == "__main__":
    raise SystemExit(_single_file_cli())



# ===========================================================================
# firebase_runtime
# ===========================================================================

"""Real Firebase Admin/async Firestore runtime and opt-in private image storage.

ADC is resolved off the event loop. One named Firebase app/client is reused for
Cog reloads; close() releases a lease and never closes another Cog's client.
Storage uses a dedicated two-thread executor with no unbounded submission queue.
Timed-out operations retain their slot until the SDK call really finishes.
"""

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

import firebase_admin
from firebase_admin import credentials, firestore_async, storage
from google.api_core import exceptions as google_exceptions
from google.api_core.retry import Retry
from google.auth.credentials import AnonymousCredentials

APP_NAME = "discord-automod"
EVIDENCE_PREFIX = "discord-moderation-evidence"
MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_OBJECT = re.compile(r"discord-moderation-evidence/[0-9]{1,22}/[A-Za-z0-9_-]{1,128}/[0-9]{1,22}-[0-9a-f]{64}\.bin\Z")
_FIREBASE_REGISTRY_ATTRIBUTE = "_discord_automod_runtime_registry_v1"
_SETTINGS_FIELDS = ("project_id", "storage_bucket", "credentials_path", "emulator_host")


class FirebaseConfigurationError(RuntimeError):
    pass


class FirebaseStorageError(RuntimeError):
    def __init__(self, message: str, *, code: str, object_path: str | None = None,
                 may_have_succeeded: bool = False):
        super().__init__(message)
        self.code = code
        self.object_path = object_path
        self.may_have_succeeded = may_have_succeeded


def _settings_key(value) -> tuple | None:
    """Compare primitive fields even when a Cog reload replaces the dataclass.

    Earlier modular versions marked the named Firebase app with their own
    FirebaseSettings instance. Reading its attributes avoids class-identity
    equality and does not require importing that earlier module.
    """
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == len(_SETTINGS_FIELDS):
        key = value
    elif isinstance(value, dict) and all(name in value for name in _SETTINGS_FIELDS):
        key = tuple(value[name] for name in _SETTINGS_FIELDS)
    else:
        try:
            key = tuple(getattr(value, name) for name in _SETTINGS_FIELDS)
        except AttributeError as exc:
            raise FirebaseConfigurationError("공유 Firebase 설정 표식을 확인할 수 없습니다. 프로세스를 재시작하세요.") from exc
    if not isinstance(key[0], str) or any(item is not None and not isinstance(item, str) for item in key[1:]):
        raise FirebaseConfigurationError("공유 Firebase 설정 표식의 형식이 올바르지 않습니다.")
    return key


def _runtime_registry() -> dict:
    """Keep ownership on the SDK module, which outlives a reloaded extension.

    No helper file is loaded. The optional legacy handoff only inspects an
    already-loaded modular runtime so its running Storage work keeps occupying
    the same bounded slots during an in-process switch to this single file.
    """
    candidate = {"schema": 1, "lock": threading.RLock(), "auth_executor": None,
                 "init_future": None, "config": None, "state": None, "legacy_module": None}
    registry = firebase_admin.__dict__.setdefault(_FIREBASE_REGISTRY_ATTRIBUTE, candidate)
    required = {"schema", "lock", "auth_executor", "init_future", "config", "state"}
    if not isinstance(registry, dict) or not required <= registry.keys() or registry["schema"] != 1:
        raise FirebaseConfigurationError("공유 Firebase 런타임 버전이 다릅니다. 프로세스를 재시작하세요.")
    with registry["lock"]:
        if registry["auth_executor"] is None:
            legacy = sys.modules.get("automod_core.firebase_runtime")
            if legacy is not None and getattr(legacy, "_AUTH_EXECUTOR", None) is not None:
                # The old module can retain live coroutines while its Cog is
                # unloading. Share actual objects instead of copying counters.
                with legacy._REGISTRY_LOCK:
                    registry.update(auth_executor=legacy._AUTH_EXECUTOR,
                        init_future=getattr(legacy, "_INIT_FUTURE", None),
                        config=_settings_key(getattr(legacy, "_CONFIG", None)),
                        state=getattr(legacy, "_STATE", None), legacy_module=legacy)
            else:
                registry["auth_executor"] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="automod-firebase-auth")
    return registry


_REGISTRY = _runtime_registry()
_REGISTRY_LOCK = _REGISTRY["lock"]
# These private names remain useful for diagnostics; the SDK-owned dictionary
# is canonical, so replacing an extension module never resets shared ownership.
_AUTH_EXECUTOR = _REGISTRY["auth_executor"]
_INIT_FUTURE: Future | None = _REGISTRY["init_future"]
_CONFIG = _REGISTRY["config"]
_STATE = _REGISTRY["state"]


def original_path(guild_id: int | str, incident_id: str, attachment_id: int | str, sha256: str) -> str:
    """Deterministic path for a durable Firestore intent before any upload."""
    guild, attachment = str(guild_id), str(attachment_id)
    if (not re.fullmatch(r"[1-9][0-9]{0,21}", guild)
            or not re.fullmatch(r"[1-9][0-9]{0,21}", attachment)
            or not isinstance(incident_id, str) or not isinstance(sha256, str)
            or not _DIGEST.fullmatch(sha256)):
        raise FirebaseStorageError("원본 경로 식별자 또는 SHA-256 형식 오류", code="invalid_identifier")
    segment = incident_id
    if ":" in incident_id:
        match = re.fullmatch(r"([1-9][0-9]{0,21}):([1-9][0-9]{0,21})", incident_id)
        if match is None or match[1] != guild:
            raise FirebaseStorageError("사건 ID의 서버와 저장 대상 서버가 다릅니다.", code="invalid_identifier")
        segment = match[2]
    if not _SEGMENT.fullmatch(segment):
        raise FirebaseStorageError("원본 사건 ID 형식 오류", code="invalid_identifier")
    return f"{EVIDENCE_PREFIX}/{guild}/{segment}/{attachment}-{sha256}.bin"


@dataclass(frozen=True, slots=True)
class FirebaseSettings:
    project_id: str
    storage_bucket: str | None
    credentials_path: str | None
    emulator_host: str | None

    @classmethod
    def from_env(cls) -> "FirebaseSettings":
        project = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
        if not _PROJECT.fullmatch(project):
            raise FirebaseConfigurationError("FIREBASE_PROJECT_ID에 실제 프로젝트 ID(6~30자)를 설정하세요.")
        bucket = os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip() or None
        if bucket is not None and not _BUCKET.fullmatch(bucket):
            raise FirebaseConfigurationError("FIREBASE_STORAGE_BUCKET에는 gs:// 또는 경로 없이 버킷 이름만 입력하세요.")
        credential_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip() or None
        # The host bot may already have initialized the default Firebase app
        # using FIREBASE_KEY. In that case, the separate ADC path is unused.
        try:
            existing_default = firebase_admin.get_app()
        except ValueError:
            existing_default = None
        if existing_default is not None:
            credential_path = None
        elif credential_path and not Path(credential_path).expanduser().is_absolute():
            raise FirebaseConfigurationError("GOOGLE_APPLICATION_CREDENTIALS는 키 파일의 절대 경로여야 합니다.")
        emulator = os.environ.get("FIRESTORE_EMULATOR_HOST", "").strip() or None
        if emulator:
            if not project.startswith("demo-"):
                raise FirebaseConfigurationError("에뮬레이터에서는 demo-로 시작하는 테스트 프로젝트 ID만 허용합니다.")
            try:
                parsed = urlsplit("//" + emulator)
                loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
                valid = (loopback and parsed.port is not None and 1 <= parsed.port <= 65535
                         and not parsed.path and not parsed.query and not parsed.fragment
                         and parsed.username is None and parsed.password is None and "://" not in emulator)
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise FirebaseConfigurationError("FIRESTORE_EMULATOR_HOST는 127.0.0.1:포트 같은 로컬 주소여야 합니다.")
        if not emulator and (os.environ.get("STORAGE_EMULATOR_HOST") or os.environ.get("FIREBASE_STORAGE_EMULATOR_HOST")):
            raise FirebaseConfigurationError("운영 모드에서 Storage 에뮬레이터 환경변수를 함께 사용하지 마세요.")
        return cls(project, bucket, credential_path, emulator)


class _EmulatorCredential(credentials.Base):
    """Constructed only after loopback/demo-project validation above."""
    def __init__(self):
        self._credential = AnonymousCredentials()

    def get_credential(self):
        return self._credential


def _prepare_app(config: FirebaseSettings):
    """Runs once on the dedicated auth thread; no image/Firestore operations."""
    try:
        app = firebase_admin.get_app(APP_NAME)
    except ValueError:
        if config.emulator_host:
            credential = _EmulatorCredential()
            credential.get_credential()
        else:
            # Reuse the credential already initialized by bot.py's
            # init_firebase() (including its FIREBASE_KEY JSON credential).
            try:
                default_app = firebase_admin.get_app()
            except ValueError:
                default_app = None
            if default_app is not None:
                if default_app.project_id != config.project_id:
                    raise FirebaseConfigurationError(
                        f"기존 Firebase 프로젝트({default_app.project_id})와 "
                        f"AutoMod 프로젝트({config.project_id})가 다릅니다."
                    )
                credential = default_app.credential
            else:
                credential = credentials.ApplicationDefault()
                credential.get_credential()
        options = {"projectId": config.project_id, "httpTimeout": 30}
        if config.storage_bucket:
            options["storageBucket"] = config.storage_bucket
        app = firebase_admin.initialize_app(credential, options=options, name=APP_NAME)
        setattr(app, "_discord_automod_settings", _settings_key(config))
    if _settings_key(getattr(app, "_discord_automod_settings", None)) != _settings_key(config):
        raise FirebaseConfigurationError("이미 존재하는 discord-automod 앱 설정이 다릅니다. 설정 확인 후 프로세스를 재시작하세요.")
    # Upgrade a marker from the earlier modular dataclass to stable primitives.
    setattr(app, "_discord_automod_settings", _settings_key(config))
    # No Cloud Storage operation is allowed from the anonymous emulator runtime.
    bucket = storage.bucket(config.storage_bucket, app=app) if config.storage_bucket and not config.emulator_host else None
    return app, bucket


@dataclass
class _SharedState:
    settings: FirebaseSettings
    app: object
    firestore: object
    bucket: object | None
    loop: asyncio.AbstractEventLoop
    executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=2, thread_name_prefix="automod-storage"))
    pending: set[asyncio.Future] = field(default_factory=set)
    leases: int = 0


class FirebaseRuntime:
    original_path = staticmethod(original_path)

    def __init__(self, state: _SharedState):
        self._state = state
        self._closed = False
        with _REGISTRY_LOCK:
            state.leases += 1

    @classmethod
    async def from_env(cls) -> "FirebaseRuntime":
        global _CONFIG, _INIT_FUTURE, _STATE
        config = FirebaseSettings.from_env()
        key = _settings_key(config)
        registry = _REGISTRY
        with _REGISTRY_LOCK:
            if registry["config"] is not None and _settings_key(registry["config"]) != key:
                raise FirebaseConfigurationError("실행 중 Firebase 환경 설정을 바꿀 수 없습니다. 봇 프로세스를 재시작하세요.")
            registry["config"] = key
            legacy = registry.get("legacy_module")
            if legacy is not None:
                with legacy._REGISTRY_LOCK:
                    old_key = _settings_key(getattr(legacy, "_CONFIG", None))
                    if old_key is not None and old_key != key:
                        raise FirebaseConfigurationError("기존 Firebase 런타임의 설정이 변경되었습니다. 프로세스를 재시작하세요.")
                    legacy._CONFIG = legacy.FirebaseSettings(*key)
                    if registry["init_future"] is None:
                        registry["init_future"] = getattr(legacy, "_INIT_FUTURE", None)
                    if registry["init_future"] is None:
                        registry["init_future"] = registry["auth_executor"].submit(_prepare_app, config)
                    legacy._INIT_FUTURE = registry["init_future"]
            elif registry["init_future"] is None:
                registry["init_future"] = registry["auth_executor"].submit(_prepare_app, config)
            initial = registry["init_future"]
            _CONFIG, _INIT_FUTURE = key, initial
        try:
            wrapped = asyncio.wrap_future(initial)
            # Retain and observe the underlying future if startup is cancelled.
            wrapped.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
            app, bucket = await asyncio.wait_for(asyncio.shield(wrapped), timeout=45)
        except FirebaseConfigurationError:
            raise
        except Exception as exc:
            raise FirebaseConfigurationError(f"Firebase 초기화 실패({type(exc).__name__}). ADC·프로젝트·키 경로를 확인하세요.") from exc
        if _settings_key(FirebaseSettings.from_env()) != key:
            raise FirebaseConfigurationError("Firebase 초기화 중 환경 설정이 변경되었습니다. 프로세스를 재시작하세요.")
        loop = asyncio.get_running_loop()
        with _REGISTRY_LOCK:
            legacy = registry.get("legacy_module")
            if registry["state"] is None and legacy is not None:
                registry["state"] = getattr(legacy, "_STATE", None)
            if registry["state"] is None:
                # ADC is already resolved. SDK client creation performs no RPC.
                client = firestore_async.client(app=app)
                registry["state"] = _SharedState(config, app, client, bucket, loop)
                if legacy is not None and getattr(legacy, "_STATE", None) is None:
                    legacy._STATE = registry["state"]
            _STATE = registry["state"]
            if _settings_key(_STATE.settings) != key or _STATE.app is not app:
                raise FirebaseConfigurationError("공유 Firebase 연결과 앱 설정이 일치하지 않습니다. 프로세스를 재시작하세요.")
            if _STATE.loop is not loop:
                raise FirebaseConfigurationError("Firebase 비동기 클라이언트는 생성한 봇 이벤트 루프에서만 사용하세요.")
            return cls(_STATE)

    @property
    def firestore(self):
        self._require_open()
        return self._state.firestore

    @property
    def bucket(self):
        self._require_open()
        return self._state.bucket

    @property
    def settings(self) -> FirebaseSettings:
        return self._state.settings

    @property
    def health(self) -> dict:
        return {
            "project_id": self.settings.project_id, "emulator": bool(self.settings.emulator_host),
            "storage_configured": self._state.bucket is not None,
            "storage_inflight": len(self._state.pending), "leases": self._state.leases,
            "closed": self._closed,
        }

    def _require_open(self) -> None:
        if self._closed:
            raise FirebaseConfigurationError("이미 해제한 FirebaseRuntime입니다.")
        if asyncio.get_running_loop() is not self._state.loop:
            raise FirebaseConfigurationError("FirebaseRuntime을 다른 이벤트 루프에서 사용하지 마세요.")

    async def _storage_call(self, operation: Callable, *, object_path: str, may_write: bool):
        self._require_open()
        if self._state.bucket is None:
            raise FirebaseStorageError("원본 저장 버킷이 없거나 에뮬레이터 모드입니다.", code="not_configured")
        if len(self._state.pending) >= 2:
            raise FirebaseStorageError("원본 저장 작업 한도에 도달했습니다. 나중에 재시도하세요.", code="busy", object_path=object_path)
        future = self._state.loop.run_in_executor(self._state.executor, operation)
        self._state.pending.add(future)

        def finished(done):
            self._state.pending.discard(done)
            if not done.cancelled():
                done.exception()  # Retrieve failures even if the caller was cancelled.

        future.add_done_callback(finished)
        try:
            # Timeout does NOT free capacity until the real SDK call finishes.
            return await asyncio.wait_for(asyncio.shield(future), timeout=35)
        except FirebaseStorageError:
            raise
        except TimeoutError as exc:
            raise FirebaseStorageError("Storage 작업 제한시간 초과; 성공 여부를 단정할 수 없습니다.",
                                       code="timeout", object_path=object_path,
                                       may_have_succeeded=may_write) from exc
        except Exception as exc:
            raise FirebaseStorageError(f"Storage 작업 실패({type(exc).__name__})",
                                       code="sdk_error", object_path=object_path,
                                       may_have_succeeded=may_write) from exc

    async def upload_original(self, guild_id: int, incident_id: str, attachment_id: int | str,
                              data: bytes, sha256: str, *, enabled: bool = False) -> dict:
        if enabled is not True:
            raise FirebaseStorageError("원본 저장은 관리자가 별도로 켜야 합니다.", code="disabled")
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ORIGINAL_BYTES:
            raise FirebaseStorageError("원본은 0~8 MiB의 bytes여야 합니다.", code="invalid_data")
        if not isinstance(sha256, str) or not _DIGEST.fullmatch(sha256) or hashlib.sha256(data).hexdigest() != sha256:
            raise FirebaseStorageError("원본 SHA-256 검증 실패", code="digest_mismatch")
        guild, attachment = str(guild_id), str(attachment_id)
        object_path = original_path(guild_id, incident_id, attachment_id, sha256)
        return await self._storage_call(
            lambda: _upload_sync(self._state.bucket, object_path, data, sha256, guild, incident_id, attachment),
            object_path=object_path, may_write=True,
        )

    async def check_storage(self) -> dict:
        """Read-only live bucket privacy proof, used before enabling originals."""
        def check():
            _prove_private(self._state.bucket, time.monotonic() + 30)
            return {"configured": True, "private": True, "bucket": self._state.bucket.name}
        return await self._storage_call(check, object_path="", may_write=False)

    async def lookup_original(self, object_path: str, sha256: str) -> dict | None:
        """Resolve an uncertain upload's generation by verified metadata only."""
        if (not isinstance(object_path, str) or not _OBJECT.fullmatch(object_path)
                or not isinstance(sha256, str) or not _DIGEST.fullmatch(sha256)
                or not object_path.endswith(f"-{sha256}.bin")):
            raise FirebaseStorageError("조회할 원본 경로·해시 형식 오류", code="invalid_path")
        return await self._storage_call(
            lambda: _lookup_sync(self._state.bucket, object_path, sha256),
            object_path=object_path, may_write=False,
        )

    async def delete_original(self, object_path: str, generation: int | str) -> bool:
        if not isinstance(object_path, str) or not _OBJECT.fullmatch(object_path):
            raise FirebaseStorageError("관리 증거 경로 외의 파일은 삭제하지 않습니다.", code="invalid_path")
        if isinstance(generation, bool) or not re.fullmatch(r"[1-9][0-9]{0,24}", str(generation)):
            raise FirebaseStorageError("정확한 Storage generation이 필요합니다.", code="invalid_generation")
        return await self._storage_call(
            lambda: _delete_sync(self._state.bucket, object_path, int(generation)),
            object_path=object_path, may_write=True,
        )

    async def close(self) -> None:
        with _REGISTRY_LOCK:
            if self._closed:
                return
            self._closed = True
            self._state.leases = max(0, self._state.leases - 1)
        # Process-lifetime SDK app/client and bounded executor remain reusable.
        # In-flight Storage futures stay tracked; one Cog cannot close another's
        # transport or falsely report a cancelled remote write as unsuccessful.


def _request_options(deadline: float) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Storage operation deadline exceeded")
    single = min(10.0, remaining / 2)
    return {"timeout": (min(5.0, single), single),
            "retry": Retry(initial=0.5, maximum=2, multiplier=2, deadline=min(remaining, 10.0))}


def _prove_private(bucket, deadline: float) -> None:
    bucket.reload(**_request_options(deadline))
    iam = bucket.iam_configuration
    if not iam.uniform_bucket_level_access_enabled:
        raise FirebaseStorageError("버킷 Uniform bucket-level access가 켜져 있지 않아 원본 저장을 거부합니다.", code="privacy_unverified")
    if iam.public_access_prevention != "enforced":
        raise FirebaseStorageError("버킷 Public access prevention을 명시적으로 enforced로 설정하세요.", code="privacy_unverified")
    policy = bucket.get_iam_policy(requested_policy_version=3, **_request_options(deadline))
    for binding in policy.bindings:
        if {"allUsers", "allAuthenticatedUsers"}.intersection(binding.get("members", ())):
            raise FirebaseStorageError("버킷에 공개 IAM 주체가 있어 원본 저장을 거부합니다.", code="public_binding")


def _upload_sync(bucket, path: str, data: bytes, digest: str, guild: str, incident: str, attachment: str) -> dict:
    deadline = time.monotonic() + 30
    try:
        _prove_private(bucket, deadline)
    except FirebaseStorageError:
        raise
    except Exception as exc:
        raise FirebaseStorageError(f"버킷 비공개 상태 조회 실패({type(exc).__name__}); 업로드하지 않았습니다.",
                                   code="privacy_unverified", object_path=path) from exc
    blob = bucket.blob(path)
    blob.metadata = {"sha256": digest, "guild_id": guild, "incident_id": incident, "attachment_id": attachment}
    blob.cache_control = "no-store"
    blob.content_disposition = 'attachment; filename="moderation-evidence.bin"'
    try:
        blob.upload_from_string(data, content_type="application/octet-stream", if_generation_match=0,
                                checksum="auto", **_request_options(deadline))
    except google_exceptions.PreconditionFailed:
        # Same content-addressed object can be reused, but never overwritten.
        blob.reload(**_request_options(deadline))
    metadata = _verified_metadata(blob, path, digest)
    if metadata["size"] != len(data) or (blob.metadata or {}).get("firebaseStorageDownloadTokens"):
        raise FirebaseStorageError("기존/업로드 원본 메타데이터를 검증할 수 없습니다.", code="metadata_mismatch",
                                   object_path=path, may_have_succeeded=True)
    return metadata


def _verified_metadata(blob, path: str, digest: str) -> dict:
    metadata = blob.metadata or {}
    try:
        identities_match = original_path(metadata["guild_id"], metadata["incident_id"], metadata["attachment_id"], digest) == path
        size, generation = int(blob.size), int(blob.generation)
    except (KeyError, TypeError, ValueError, FirebaseStorageError):
        identities_match, size, generation = False, 0, 0
    if metadata.get("sha256") != digest or not identities_match or not 0 < size <= MAX_ORIGINAL_BYTES or generation <= 0:
        raise FirebaseStorageError("원본 식별자·해시·generation 메타데이터 검증 실패", code="metadata_mismatch",
                                   object_path=path, may_have_succeeded=True)
    return {"object_path": path, "generation": generation, "sha256": digest, "size": size}


def _lookup_sync(bucket, path: str, digest: str) -> dict | None:
    deadline = time.monotonic() + 30
    blob = bucket.blob(path)
    try:
        blob.reload(**_request_options(deadline))
    except google_exceptions.NotFound:
        return None
    return _verified_metadata(blob, path, digest)


def _delete_sync(bucket, path: str, generation: int) -> bool:
    deadline = time.monotonic() + 30
    blob = bucket.blob(path, generation=generation)
    try:
        blob.delete(if_generation_match=generation, **_request_options(deadline))
        return True
    except google_exceptions.NotFound:
        return True
    except google_exceptions.PreconditionFailed:
        return False



# ===========================================================================
# firestore_store
# ===========================================================================

"""Canonical Cloud Firestore state, using the real async Google/Firebase client.

Transactions contain Firestore reads/writes ONLY. Discord and Storage calls are
outside retryable callbacks. Minimal incidents and member counters never expire;
separate evidence documents have TTL timestamps. There is no SQLite fallback.
"""

import asyncio
import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.api_core import exceptions as google_errors
from google.cloud.firestore_v1 import AsyncClient, async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

UTC = timezone.utc
ROOT_COLLECTION = "discord_moderation_guilds"
SCHEMA_VERSION = 1
DEFAULT_CONFIG = {
    "mode": "off", "setup_complete": False, "log_channel_id": None,
    "warning_limit": 1, "timeout_seconds": 3600, "image_enabled": True,
    "retain_originals": False, "retention_days": 30, "category_modes": {},
    "exempt_channel_ids": [], "exempt_role_ids": [], "operator_role_ids": [],
    "public_fallback": True, "public_delete_after": 15,
    "notice_cooldown_seconds": 60, "display_timezone": "Asia/Seoul",
}
CATEGORY_CODES = {"profanity", "shorthand", "insult", "family", "sexual", "disability",
              "gender", "origin", "mockery", "threat", "english"}
ACTIONS = {"preflight", "delete", "timeout", "violation", "warning", "dm", "public", "log", "original"}
STATUSES = {"pending", "succeeded", "failed", "skipped", "unknown"}


class StorageUnavailable(RuntimeError):
    """No reliable canonical state: do not issue further automatic sanctions."""


@dataclass(frozen=True)
class PreparedResult:
    incident_id: str
    new_violation: bool
    count: int
    generation: int
    mode: str
    plan: dict
    created: bool
    reason: str = ""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _time(value: datetime | None = None) -> datetime:
    value = value or utc_now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("시간대가 있는 datetime이 필요합니다.")
    return value.astimezone(UTC)


def _external(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {key: _external(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_external(item) for item in value]
    return value


def _id(value: int | str) -> int:
    if isinstance(value, bool):
        raise ValueError("Discord ID가 올바르지 않습니다.")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError("Discord ID가 올바르지 않습니다.")
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("Discord ID가 올바르지 않습니다.") from exc
    if not 0 < number < 2**63 or str(number) != str(value):
        raise ValueError("Discord ID가 올바르지 않습니다.")
    return number


def _integer(value, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name}: {minimum}~{maximum} 사이 정수여야 합니다.")
    return value


def validate_config(config: dict) -> dict:
    unknown = set(config) - set(DEFAULT_CONFIG) - {"guild_id", "config_version"}
    if unknown:
        raise ValueError("지원하지 않는 설정: " + ", ".join(sorted(unknown)))
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update(copy.deepcopy(config))
    if result["mode"] not in {"off", "observe", "enforce"}:
        raise ValueError("모드는 off/observe/enforce 중 하나여야 합니다.")
    for key in ("setup_complete", "image_enabled", "retain_originals", "public_fallback"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key}: 참/거짓 설정이 필요합니다.")
    if result["mode"] == "enforce" and not result["setup_complete"]:
        raise ValueError("최초 설정과 권한·OCR 점검을 먼저 완료하세요.")
    if result["log_channel_id"] is not None:
        _id(result["log_channel_id"])
    _integer(result["warning_limit"], 0, 10000, "초기 경고 횟수")
    _integer(result["timeout_seconds"], 60, 28 * 86400, "고정 타임아웃 초")
    _integer(result["retention_days"], 1, 3650, "증거 보관 일수")
    _integer(result["public_delete_after"], 5, 300, "공개 안내 삭제 초")
    _integer(result["notice_cooldown_seconds"], 1, 86400, "알림 간격 초")
    for key in ("exempt_channel_ids", "exempt_role_ids", "operator_role_ids"):
        if not isinstance(result[key], (list, tuple)) or len(result[key]) > 100:
            raise ValueError(f"{key}: 최대 100개 ID를 설정할 수 있습니다.")
        result[key] = sorted({_id(value) for value in result[key]})
    modes = result["category_modes"]
    if not isinstance(modes, dict) or set(modes) - CATEGORY_CODES or any(value not in {"off", "review", "enforce"} for value in modes.values()):
        raise ValueError("분류 설정이 올바르지 않습니다.")
    try:
        ZoneInfo(result["display_timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("IANA 표시 시간대를 입력하세요. 예: Asia/Seoul") from exc
    return result


def incident_id(guild_id: int, message_id: int) -> str:
    return f"{_id(guild_id)}:{_id(message_id)}"


def parse_incident_id(value: str) -> tuple[int, int]:
    try:
        guild, message = value.split(":")
        return _id(guild), _id(message)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("사건 ID는 서버ID:메시지ID 형식입니다.") from exc


class Store:
    def __init__(self, client: AsyncClient, *, runtime=None):
        if not isinstance(client, AsyncClient):
            raise TypeError("실제 Firestore AsyncClient가 필요합니다.")
        self.client = client
        self.runtime = runtime
        self._healthy = False
        self._closed = False

    @property
    def healthy(self) -> bool:
        return self._healthy and not self._closed

    def _require(self) -> None:
        if not self.healthy:
            raise StorageUnavailable("Firestore 연결 상태가 불확실해 자동 처벌을 중지했습니다.")

    def guild_ref(self, guild_id: int):
        return self.client.collection(ROOT_COLLECTION).document(str(_id(guild_id)))

    def _settings(self, guild_id):
        return self.guild_ref(guild_id).collection("settings").document("config")

    def _case_ref(self, value: str):
        guild, message = parse_incident_id(value)
        return self.guild_ref(guild).collection("incidents").document(str(message))

    def _evidence_ref(self, value: str):
        guild, message = parse_incident_id(value)
        return self.guild_ref(guild).collection("evidence").document(str(message))

    def _member_ref(self, guild, user):
        return self.guild_ref(guild).collection("members").document(str(_id(user)))

    @staticmethod
    def _action_key(kind: str, key: str) -> str:
        if kind not in ACTIONS or not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("조치 종류 또는 키가 올바르지 않습니다.")
        return kind + "_" + hashlib.sha256(key.encode()).hexdigest()[:32]

    def _action_ref(self, case: str, kind: str, key: str = "once"):
        return self._case_ref(case).collection("actions").document(self._action_key(kind, key))

    async def open(self) -> None:
        if self._closed:
            raise StorageUnavailable("닫힌 Firestore 저장소는 다시 사용할 수 없습니다.")
        await self.probe()

    async def probe(self) -> bool:
        if self._closed:
            return False
        try:
            # Admin SDK has no local document cache: this is a real server read.
            await self.client.collection("discord_moderation_meta").document("health").get(retry=None, timeout=10)
            self._healthy = True
            return True
        except (google_errors.GoogleAPICallError, OSError, TimeoutError) as exc:
            self._healthy = False
            raise StorageUnavailable("Firestore 실제 연결 점검에 실패했습니다.") from exc

    async def recover(self) -> bool:
        return await self.probe()

    async def close(self) -> None:
        self._healthy = False
        self._closed = True
        # FirebaseRuntime owns the shared client/app lease.

    async def _read(self, reference):
        self._require()
        try:
            snapshot = await reference.get(retry=None, timeout=15)
            return snapshot.to_dict() if snapshot.exists else None
        except (google_errors.GoogleAPICallError, OSError, TimeoutError) as exc:
            self._healthy = False
            raise StorageUnavailable("Firestore 조회에 실패했습니다.") from exc

    async def _query(self, query) -> list:
        self._require()
        try:
            return [snapshot async for snapshot in query.stream(retry=None, timeout=20)]
        except (google_errors.GoogleAPICallError, OSError, TimeoutError) as exc:
            self._healthy = False
            raise StorageUnavailable("Firestore 목록 조회에 실패했습니다.") from exc

    async def _transaction(self, callback: Callable):
        self._require()
        try:
            for attempt in range(3):
                try:
                    return await async_transactional(callback)(self.client.transaction(max_attempts=12))
                except google_errors.Aborted:
                    # A read can be aborted before the SDK reaches its commit
                    # retry loop. Every callback here is pure Firestore work.
                    if attempt == 2:
                        raise
                except ValueError as exc:
                    # The SDK wraps exhausted commit-abort retries in ValueError.
                    # Aborted transactions did not commit and may safely retry;
                    # permission/network/unknown commit errors never enter here.
                    if not isinstance(exc.__cause__, google_errors.Aborted) or attempt == 2:
                        raise
                # Avoid synchronized contenders repeatedly reacquiring the same
                # pessimistic member lock, including across process instances.
                await asyncio.sleep(0.05 * (2 ** attempt) + (uuid.uuid4().int % 100) / 1000)
            raise AssertionError("unreachable transaction retry state")
        except asyncio.CancelledError:
            self._healthy = False
            raise
        except StorageUnavailable:
            self._healthy = False
            raise
        except ValueError as exc:
            if isinstance(exc.__cause__, google_errors.Aborted):
                self._healthy = False
                raise StorageUnavailable("Firestore 트랜잭션 충돌 재시도 한도를 초과했습니다.") from exc
            raise
        except (google_errors.GoogleAPICallError, OSError, TimeoutError) as exc:
            self._healthy = False
            raise StorageUnavailable("Firestore 트랜잭션 결과가 불확실해 자동 처벌을 중지했습니다.") from exc

    @staticmethod
    async def _get(transaction, reference) -> dict | None:
        snapshot = await reference.get(transaction=transaction, retry=None, timeout=15)
        return snapshot.to_dict() if snapshot.exists else None

    @staticmethod
    def _config(raw: dict | None, guild: int) -> dict:
        try:
            raw = raw or {}
            values = raw.get("values", {})
            return validate_config({**values, "guild_id": guild, "config_version": int(raw.get("version", 0))})
        except (ValueError, TypeError) as exc:
            raise StorageUnavailable("Firestore 서버 설정이 손상되었습니다.") from exc

    @staticmethod
    def _member(raw: dict | None) -> dict:
        data = dict(raw or {})
        data.setdefault("generation", 0)
        data.setdefault("violation_count", 0)
        data.setdefault("pending_incident", None)
        if any(isinstance(data[key], bool) or not isinstance(data[key], int) or data[key] < 0 for key in ("generation", "violation_count")):
            raise StorageUnavailable("회원 누적 기록이 손상되었습니다.")
        return data

    def _audit_ref(self, guild: int, operation: str | None = None):
        return self.guild_ref(guild).collection("audit").document(operation or uuid.uuid4().hex)

    @staticmethod
    def _audit(actor, action: str, detail, now: datetime) -> dict:
        encoded = json.dumps(detail, ensure_ascii=False, default=str)
        if len(encoded) > 12000:
            raise ValueError("관리 이력이 너무 큽니다.")
        return {"actor_id": actor, "action": str(action)[:80], "detail": detail, "at": now}

    async def get_config(self, guild_id: int) -> dict:
        guild = _id(guild_id)
        return self._config(await self._read(self._settings(guild)), guild)

    async def update_config(self, guild_id: int, *, actor_id: int | None = None, reason: str = "", **changes) -> dict:
        guild = _id(guild_id)
        if actor_id is not None:
            _id(actor_id)
        if set(changes) - set(DEFAULT_CONFIG):
            raise ValueError("지원하지 않는 서버 설정입니다.")
        settings, audit, now = self._settings(guild), self._audit_ref(guild), utc_now()
        async def run(transaction):
            config = self._config(await self._get(transaction, settings), guild)
            before = copy.deepcopy(config)
            config.update(changes)
            config = validate_config(config)
            differences = {key: {"before": before[key], "after": config[key]} for key in changes if before[key] != config[key]}
            if not differences:
                return config
            config["config_version"] += 1
            transaction.set(settings, {"values": {k: config[k] for k in DEFAULT_CONFIG}, "version": config["config_version"], "updated_at": now})
            transaction.set(self.guild_ref(guild), {"guild_id": str(guild), "schema_version": SCHEMA_VERSION}, merge=True)
            transaction.set(audit, self._audit(actor_id, "config", {"changes": differences, "reason": str(reason)[:300]}, now))
            return config
        return await self._transaction(run)

    @staticmethod
    def _rule_id(rule_id: str) -> str:
        if not isinstance(rule_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", rule_id) or rule_id in {".", ".."}:
            raise ValueError("규칙 ID가 올바르지 않습니다.")
        return rule_id

    async def list_rules(self, guild_id: int) -> dict:
        rows = await self._query(self.guild_ref(guild_id).collection("rules"))
        return {"custom_rules": [row.to_dict()["rule"] for row in rows if row.to_dict().get("rule")],
                "disabled_ids": [row.id for row in rows if row.to_dict().get("disabled", False)]}

    async def _policy_mutation(self, guild: int, reference, build: Callable, *, actor_id=None, action: str, detail):
        settings, audit, now = self._settings(guild), self._audit_ref(guild), utc_now()
        async def run(transaction):
            config = self._config(await self._get(transaction, settings), guild)
            old = await self._get(transaction, reference)
            replacement = build(old)
            if replacement == old:
                return False
            if replacement is None:
                transaction.delete(reference)
            else:
                transaction.set(reference, replacement)
            transaction.set(settings, {"values": {k: config[k] for k in DEFAULT_CONFIG}, "version": config["config_version"] + 1, "updated_at": now})
            transaction.set(self.guild_ref(guild), {"guild_id": str(guild), "schema_version": SCHEMA_VERSION}, merge=True)
            transaction.set(audit, self._audit(actor_id, action, detail, now))
            return True
        return await self._transaction(run)

    async def add_rule(self, guild_id: int, rule: dict, *, actor_id=None) -> str:
        guild = _id(guild_id)
        saved = copy.deepcopy(rule)
        key = self._rule_id(saved.setdefault("id", "custom_" + uuid.uuid4().hex))
        if not key.startswith("custom_") or len(json.dumps(saved, ensure_ascii=False)) > 16000:
            raise ValueError("추가 규칙 ID·길이를 확인하세요.")
        existing = await self.list_rules(guild)
        if len(existing["custom_rules"]) >= 200:
            raise ValueError("추가 규칙은 서버별 최대 200개입니다.")
        def build(old):
            if old and old.get("rule"):
                raise ValueError("같은 규칙 ID가 이미 있습니다.")
            return {"rule": saved, "disabled": False}
        await self._policy_mutation(guild, self.guild_ref(guild).collection("rules").document(key), build,
                                    actor_id=actor_id, action="rule_add", detail={"rule_id": key})
        return key

    async def set_rule_disabled(self, guild_id: int, rule_id: str, disabled=True, *, actor_id=None):
        guild, key = _id(guild_id), self._rule_id(rule_id)
        if not isinstance(disabled, bool):
            raise ValueError("비활성화 여부는 참/거짓이어야 합니다.")
        await self._policy_mutation(guild, self.guild_ref(guild).collection("rules").document(key),
            lambda old: {**(old or {}), "disabled": disabled}, actor_id=actor_id,
            action="rule_disable" if disabled else "rule_restore", detail={"rule_id": key})

    @staticmethod
    def _allow(term):
        if not isinstance(term, str) or not 2 <= len(term.strip()) <= 100 or any(ord(c) < 32 for c in term):
            raise ValueError("허용어는 제어문자 없는 2~100자여야 합니다.")
        return term.strip()

    async def list_allow(self, guild_id: int) -> list[str]:
        return [row.to_dict()["term"] for row in await self._query(self.guild_ref(guild_id).collection("allow_terms"))]

    async def add_allow(self, guild_id: int, term: str, *, actor_id=None):
        guild, term = _id(guild_id), self._allow(term)
        if len(await self.list_allow(guild)) >= 200:
            raise ValueError("허용어는 서버별 최대 200개입니다.")
        key = hashlib.sha256(term.encode()).hexdigest()
        await self._policy_mutation(guild, self.guild_ref(guild).collection("allow_terms").document(key), lambda old: {"term": term},
                                    actor_id=actor_id, action="allow_add", detail={"term": term})

    async def remove_allow(self, guild_id: int, term: str, *, actor_id=None) -> bool:
        guild, term = _id(guild_id), self._allow(term)
        key = hashlib.sha256(term.encode()).hexdigest()
        return await self._policy_mutation(guild, self.guild_ref(guild).collection("allow_terms").document(key), lambda old: None,
                                          actor_id=actor_id, action="allow_remove", detail={"term": term})

    async def get_user_generation(self, guild_id: int, user_id: int) -> int:
        return self._member(await self._read(self._member_ref(guild_id, user_id)))["generation"]

    async def get_member(self, guild_id: int, user_id: int) -> dict:
        return _external(self._member(await self._read(self._member_ref(guild_id, user_id))))

    get_member_state = get_member

    async def count_violations(self, guild_id: int, user_id: int) -> int:
        return self._member(await self._read(self._member_ref(guild_id, user_id)))["violation_count"]

    @staticmethod
    def _evidence(findings: list[dict]) -> list[dict]:
        allowed = {"rule_id", "category", "start", "end", "matched", "severity", "confidence", "reason", "recommendation", "source", "attachment_id"}
        if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
            raise ValueError("탐지 결과가 올바르지 않습니다.")
        ordered = sorted(findings, key=lambda item: not (item.get("confidence") == "clear" and item.get("recommendation") == "enforce"))
        return [{key: (value[:240] if isinstance(value, str) else value) for key, value in item.items() if key in allowed} for item in ordered[:40]]

    async def prepare_incident(self, guild_id: int, message_id: int, user_id: int, channel_id: int,
        content_hash: str, findings: list[dict], excerpt: str, *, expected_generation: int,
        expected_config_version: int, user_name: str = "", inspection_notes: str = "", message_signature: str = "", attachment_metadata=(),
        message_created_at: datetime | None = None, message_edited_at: datetime | None = None, now: datetime | None = None) -> PreparedResult:
        return await self._prepare(guild_id, message_id, user_id, channel_id, content_hash, findings, excerpt,
            expected_generation=expected_generation, expected_config_version=expected_config_version,
            user_name=user_name, inspection_notes=inspection_notes, message_signature=message_signature, attachment_metadata=attachment_metadata,
            message_created_at=message_created_at, message_edited_at=message_edited_at, now=now, enforce=True)

    async def record_review(self, guild_id: int, message_id: int, user_id: int, channel_id: int,
        content_hash: str, findings: list[dict], excerpt: str, *, expected_generation: int,
        expected_config_version: int, user_name: str = "", inspection_notes: str = "", message_signature: str = "", attachment_metadata=(),
        message_created_at: datetime | None = None, message_edited_at: datetime | None = None, now: datetime | None = None) -> PreparedResult:
        return await self._prepare(guild_id, message_id, user_id, channel_id, content_hash, findings, excerpt,
            expected_generation=expected_generation, expected_config_version=expected_config_version,
            user_name=user_name, inspection_notes=inspection_notes, message_signature=message_signature, attachment_metadata=attachment_metadata,
            message_created_at=message_created_at, message_edited_at=message_edited_at, now=now, enforce=False)

    async def _prepare(self, guild_id, message_id, user_id, channel_id, content_hash, findings, excerpt, *,
        expected_generation, expected_config_version, user_name, inspection_notes, message_signature, attachment_metadata,
        message_created_at, message_edited_at, now, enforce):
        guild, message, user, channel = map(_id, (guild_id, message_id, user_id, channel_id))
        if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise ValueError("SHA-256 내용 해시가 필요합니다.")
        evidence = self._evidence(findings)
        candidate = any(item.get("confidence") == "clear" and item.get("recommendation") == "enforce" for item in evidence)
        key, moment = incident_id(guild, message), _time(now)
        sent_at = _time(message_created_at) if message_created_at is not None else None
        edited_at = _time(message_edited_at) if message_edited_at is not None else None
        case_ref, member_ref, settings_ref = self._case_ref(key), self._member_ref(guild, user), self._settings(guild)
        audit = self._audit_ref(guild)
        async def run(transaction):
            existing = await self._get(transaction, case_ref)
            member = self._member(await self._get(transaction, member_ref))
            config = self._config(await self._get(transaction, settings_ref), guild)
            generation, count = member["generation"], member["violation_count"]
            empty = lambda reason: PreparedResult("", False, count, generation, config["mode"], {}, False, reason)
            if generation != expected_generation:
                return empty("generation_changed")
            if config["config_version"] != expected_config_version:
                return empty("config_changed")
            if config["mode"] == "off":
                return empty("off")
            promote_review = False
            if existing:
                if existing["user_id"] != user or existing["channel_id"] != channel:
                    raise StorageUnavailable("같은 메시지의 사용자·채널 기록이 일치하지 않습니다.")
                changed = existing.get("current_content_hash", existing.get("content_hash")) != content_hash
                if sent_at is not None:
                    existing["message_created_at"] = sent_at
                if edited_at is not None:
                    existing["message_edited_at"] = edited_at
                promote_review = bool(changed and enforce and candidate and not existing.get("counted_ever")
                                      and not existing.get("imported") and existing.get("state") == "review")
                if changed and existing.get("count_applied") and existing.get("state") not in {"completed", "imported"}:
                    if existing["generation"] == generation:
                        count = max(0, count - 1)
                        transaction.set(member_ref, {**member, "violation_count": count, "pending_incident": None if member.get("pending_incident") == key else member.get("pending_incident"), "updated_at": moment})
                    existing.update(count_applied=False, state="voided", void_reason="message_changed", voided_at=moment, recovery_needed=False)
                    transaction.set(audit, self._audit(None, "void_violation", {"incident_id": key, "reason": "message_changed"}, moment))
                if changed and not promote_review:
                    existing.update(current_content_hash=content_hash, updated_at=moment,
                        evidence_expires_at=moment + timedelta(days=config["retention_days"]),
                        retention_next_attempt_at=moment + timedelta(days=config["retention_days"]),
                        evidence_deleted=not bool(evidence or inspection_notes), retention_state="waiting")
                    transaction.set(case_ref, existing)
                    if evidence or inspection_notes:
                        transaction.set(self._evidence_ref(key), self._evidence_document(evidence, excerpt, user_name, inspection_notes, content_hash, config, moment, attachment_metadata))
                    else:
                        transaction.delete(self._evidence_ref(key))
                if not promote_review:
                    if not changed and (sent_at is not None or edited_at is not None):
                        transaction.set(case_ref, existing)
                    return PreparedResult(key, False, count, generation, existing.get("mode", config["mode"]), _external(existing.get("plan", {})), False, "existing")
            if not evidence and not inspection_notes:
                return empty("clean")
            if enforce and (not candidate or config["mode"] != "enforce" or not config["setup_complete"]):
                return empty("not_enforcing")
            if enforce and member.get("pending_incident") and member["pending_incident"] != key:
                return empty("user_busy")
            plan = {}
            state = "review"
            if enforce:
                count += 1
                requires_timeout = count > config["warning_limit"]
                plan = {
                    "count": count, "count_after": count, "warning_limit": config["warning_limit"],
                    "timeout_seconds": config["timeout_seconds"], "should_timeout": requires_timeout,
                    "timeout_until": moment + timedelta(seconds=config["timeout_seconds"]) if requires_timeout else None,
                    "config_version": config["config_version"], "generation": generation,
                    "original_content_hash": content_hash, "log_channel_id": config["log_channel_id"],
                    "original_message_fingerprint": message_signature or content_hash,
                    "public_fallback": config["public_fallback"], "public_delete_after": config["public_delete_after"],
                    "notice_cooldown_seconds": config["notice_cooldown_seconds"], "prepared_at": moment,
                }
                member.update(violation_count=count, pending_incident=key, user_id=user, updated_at=moment)
                transaction.set(member_ref, member)
                state = "prepared"
            document = {
                "incident_id": key, "guild_id": guild, "message_id": message, "user_id": user, "channel_id": channel,
                "generation": generation, "config_version": config["config_version"], "mode": config["mode"],
                "content_hash": content_hash, "original_content_hash": content_hash, "current_content_hash": content_hash,
                "plan": plan, "state": state, "count_applied": enforce, "counted_ever": enforce,
                "imported": False, "created_at": moment, "updated_at": moment, "canceled_at": None,
                "message_created_at": sent_at, "message_edited_at": edited_at,
                "recovery_needed": enforce,
                "log_status": "not_started", "log_attempt_at": moment,
                "rule_ids": sorted({str(item.get("rule_id", "")) for item in evidence}),
                "evidence_expires_at": moment + timedelta(days=config["retention_days"]),
                "retention_next_attempt_at": moment + timedelta(days=config["retention_days"]),
                "evidence_deleted": False,
                "retention_state": "waiting", "retention_cleanup": {"storage_objects": [], "log_messages": []},
            }
            if promote_review:
                document["created_at"] = existing["created_at"]
                document["retention_cleanup"] = existing.get("retention_cleanup", document["retention_cleanup"])
                transaction.set(case_ref, document)
            else:
                transaction.create(case_ref, document)
            transaction.set(self._evidence_ref(key), self._evidence_document(evidence, excerpt, user_name, inspection_notes, content_hash, config, moment, attachment_metadata))
            transaction.set(self.guild_ref(guild), {"guild_id": str(guild), "schema_version": SCHEMA_VERSION}, merge=True)
            return PreparedResult(key, enforce, count, generation, config["mode"], _external(plan), True)
        return await self._transaction(run)

    @staticmethod
    def _evidence_document(findings, excerpt, name, notes, digest, config, now, attachment_metadata=()):
        return {"findings": findings, "excerpt": str(excerpt)[:240], "user_name": str(name)[:100],
                "inspection_notes": str(notes)[:1000], "content_hash": digest,
                "expires_at": now + timedelta(days=config["retention_days"]), "updated_at": now,
                "originals": [], "automod_schema": SCHEMA_VERSION,
                "attachment_metadata": [{k: (v[:256] if isinstance(v, str) else v) for k, v in item.items()
                    if k in {"attachment_id", "sha256", "status", "filename", "size", "content_type"}}
                    for item in list(attachment_metadata)[:4] if isinstance(item, dict)]}

    @staticmethod
    def _eligibility(case: dict | None, member: dict, config: dict) -> tuple[bool, str]:
        if case is None:
            return False, "missing"
        if case.get("imported") or case.get("state") == "imported":
            return False, "imported_do_not_execute"
        if case.get("state") in {"voided", "canceled", "review"} or not case.get("count_applied"):
            return False, "not_active"
        if case["generation"] != member["generation"]:
            return False, "generation_changed"
        if case["config_version"] != config["config_version"]:
            return False, "config_changed"
        if config["mode"] != "enforce" or not config["setup_complete"]:
            return False, "off"
        if case.get("current_content_hash") != case.get("original_content_hash") and case.get("state") != "completed":
            return False, "message_changed"
        return True, "completed" if case.get("state") == "completed" else "ready"

    async def execution_status(self, value: str) -> dict:
        guild, _ = parse_incident_id(value)
        async def run(transaction):
            case = await self._get(transaction, self._case_ref(value))
            if case is None:
                return {"eligible": False, "reason": "missing", "incident": None}
            member = self._member(await self._get(transaction, self._member_ref(guild, case["user_id"])))
            config = self._config(await self._get(transaction, self._settings(guild)), guild)
            eligible, reason = self._eligibility(case, member, config)
            return {"eligible": eligible, "reason": reason, "incident": _external(case)}
        return await self._transaction(run)

    async def claim_action(self, value: str, kind: str, *, key="once", allow_retry=False) -> bool:
        guild, _ = parse_incident_id(value)
        case_ref, action_ref = self._case_ref(value), self._action_ref(value, kind, key)
        now = utc_now()
        async def run(transaction):
            case = await self._get(transaction, case_ref)
            action = await self._get(transaction, action_ref)
            if case is None:
                return False
            if kind not in {"log", "preflight"}:
                member = self._member(await self._get(transaction, self._member_ref(guild, case["user_id"])))
                config = self._config(await self._get(transaction, self._settings(guild)), guild)
                if not self._eligibility(case, member, config)[0]:
                    return False
            if action and not (kind == "log" and allow_retry and action["status"] == "failed"):
                return False
            transaction.set(action_ref, {**(action or {}), "incident_id": value, "kind": kind, "action_key": key,
                "status": "pending", "code": "", "detail": "", "attempts": (action or {}).get("attempts", 0) + 1,
                "created_at": (action or {}).get("created_at", now), "claimed_at": now, "updated_at": now})
            if kind == "log":
                transaction.update(case_ref, {"log_status": "pending", "log_attempt_at": now})
            return True
        return await self._transaction(run)

    async def finish_action(self, value: str, kind: str, status: str, detail="", *, key="once", code="", discord_message_id=None, discord_channel_id=None):
        if status not in STATUSES - {"pending"}:
            raise ValueError("조치 완료 상태가 올바르지 않습니다.")
        guild, _ = parse_incident_id(value)
        case_ref, action_ref, now = self._case_ref(value), self._action_ref(value, kind, key), utc_now()
        async def run(transaction):
            case = await self._get(transaction, case_ref)
            action = await self._get(transaction, action_ref)
            if case is None or action is None:
                raise StorageUnavailable("예약된 조치 기록을 찾을 수 없습니다.")
            if action["status"] != "pending":
                return
            member = self._member(await self._get(transaction, self._member_ref(guild, case["user_id"])))
            other = None
            if kind in {"delete", "timeout"}:
                other_kind = "timeout" if kind == "delete" else "delete"
                other = await self._get(transaction, self._action_ref(value, other_kind))
            action.update(status=status, code=str(code)[:80], detail=str(detail)[:300], updated_at=now)
            if discord_message_id is not None:
                action["discord_message_id"] = _id(discord_message_id)
            if discord_channel_id is not None:
                action["discord_channel_id"] = _id(discord_channel_id)
            transaction.set(action_ref, action)
            if kind == "log":
                transaction.update(case_ref, {"log_status": status, "log_attempt_at": now})
            if kind == "log" and discord_message_id is not None:
                channel = discord_channel_id or case.get("plan", {}).get("log_channel_id")
                if channel is not None:
                    cleanup = copy.deepcopy(case.get("retention_cleanup", {"storage_objects": [], "log_messages": []}))
                    logs = cleanup.setdefault("log_messages", [])
                    if not any(item["message_id"] == int(discord_message_id) for item in logs):
                        logs.append({"channel_id": int(channel), "message_id": int(discord_message_id), "status": "waiting"})
                    transaction.update(case_ref, {"retention_cleanup": cleanup, "retention_state": "waiting"})
            if kind in {"delete", "timeout"} and case.get("count_applied"):
                deletion = action if kind == "delete" else other
                timeout = action if kind == "timeout" else other
                timeout_ok = not case["plan"].get("should_timeout") or (timeout and (timeout["status"] == "succeeded" or (timeout["status"] == "skipped" and timeout.get("code") == "existing_longer")))
                if deletion and deletion["status"] == "succeeded" and timeout_ok:
                    transaction.update(case_ref, {"state": "completed", "completed_at": now, "updated_at": now})
                    if member.get("pending_incident") == value:
                        transaction.update(self._member_ref(guild, case["user_id"]), {"pending_incident": None, "updated_at": now})
        await self._transaction(run)

    async def get_actions(self, value: str) -> list[dict]:
        return [_external(row.to_dict()) for row in await self._query(self._case_ref(value).collection("actions"))]

    async def reconcile_log(self, value: str, discord_message_id: int, channel_id: int) -> bool:
        reference, now = self._action_ref(value, "log"), utc_now()
        async def run(transaction):
            action = await self._get(transaction, reference)
            case = await self._get(transaction, self._case_ref(value))
            if action is None:
                return False
            if action.get("discord_message_id") is not None and action["discord_message_id"] != int(discord_message_id):
                return False
            transaction.update(reference, {"status": "failed", "code": "reconciled_existing", "discord_message_id": _id(discord_message_id), "discord_channel_id": _id(channel_id), "updated_at": now})
            transaction.update(self._case_ref(value), {"log_status": "failed", "log_attempt_at": now})
            if case is not None:
                cleanup = copy.deepcopy(case.get("retention_cleanup", {"storage_objects": [], "log_messages": []}))
                logs = cleanup.setdefault("log_messages", [])
                if not any(item["message_id"] == int(discord_message_id) for item in logs):
                    logs.append({"channel_id": int(channel_id), "message_id": int(discord_message_id), "status": "waiting"})
                transaction.update(self._case_ref(value), {"retention_cleanup": cleanup, "retention_state": "waiting"})
            return True
        return await self._transaction(run)

    async def _void(self, guild_id, value, reason, actor_id, cancellation, now):
        guild, _ = parse_incident_id(value)
        if guild != _id(guild_id):
            return False
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 300:
            raise ValueError("사유를 1~300자로 입력하세요.")
        moment, audit = _time(now), self._audit_ref(guild)
        pending_audit = self._audit_ref(guild)
        async def run(transaction):
            case = await self._get(transaction, self._case_ref(value))
            if case is None or not case.get("count_applied"):
                return False
            reference = self._member_ref(guild, case["user_id"])
            member = self._member(await self._get(transaction, reference))
            pending_id = member.get("pending_incident")
            pending_case = None
            if cancellation and case["generation"] == member["generation"] and pending_id and pending_id != value:
                pending_case = await self._get(transaction, self._case_ref(pending_id))
            if case["generation"] == member["generation"]:
                member["violation_count"] = max(0, member["violation_count"] - 1)
            if (pending_case and pending_case.get("state") == "prepared"
                    and pending_case.get("count_applied") and pending_case["generation"] == member["generation"]):
                # Its immutable escalation plan included the now-canceled older
                # violation. Discard that unexecuted plan instead of recomputing
                # a different punishment or leaving a stale timeout executable.
                member["violation_count"] = max(0, member["violation_count"] - 1)
                member["pending_incident"] = None
                transaction.update(self._case_ref(pending_id), {
                    "count_applied": False, "state": "voided", "recovery_needed": False,
                    "void_reason": "prior_violation_canceled", "voided_at": moment,
                    "updated_at": moment, "needs_review": True,
                })
                transaction.set(pending_audit, self._audit(actor_id, "void_violation", {
                    "incident_id": pending_id, "reason": "prior_violation_canceled",
                    "canceled_incident_id": value,
                }, moment))
            if member.get("pending_incident") == value:
                member["pending_incident"] = None
            member["updated_at"] = moment
            transaction.set(reference, member)
            transaction.update(self._case_ref(value), {"count_applied": False, "state": "canceled" if cancellation else "voided",
                "canceled_at": moment if cancellation else None, "canceled_by": actor_id,
                "cancellation_reason": reason, "void_reason": reason, "updated_at": moment,
                "recovery_needed": False,
                "needs_review": reason.startswith("action_unknown") or "unknown" in reason})
            transaction.set(audit, self._audit(actor_id, "cancel_violation" if cancellation else "void_violation", {"incident_id": value, "reason": reason}, moment))
            return True
        return await self._transaction(run)

    async def void_violation(self, guild_id, value, reason, *, actor_id=None, now=None):
        return await self._void(guild_id, value, reason, actor_id, False, now)

    async def cancel_violation(self, guild_id, value, actor_id, reason, *, now=None):
        _id(actor_id)
        return await self._void(guild_id, value, reason, actor_id, True, now)

    async def reset_violations(self, guild_id, user_id, actor_id, reason, *, now=None) -> int:
        guild, user, actor = map(_id, (guild_id, user_id, actor_id))
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 300:
            raise ValueError("초기화 사유가 필요합니다.")
        reference, audit, moment = self._member_ref(guild, user), self._audit_ref(guild), _time(now)
        async def run(transaction):
            member = self._member(await self._get(transaction, reference))
            before = member["generation"]
            member.update(generation=before + 1, violation_count=0, pending_incident=None, updated_at=moment, reset_at=moment, user_id=user)
            transaction.set(reference, member)
            transaction.set(audit, self._audit(actor, "reset_violations", {"user_id": user, "old_generation": before, "new_generation": before + 1, "reason": reason}, moment))
            return before + 1
        return await self._transaction(run)

    async def get_incident(self, guild_id, message_id):
        return await self.get_incident_by_id(guild_id, incident_id(guild_id, message_id))

    async def get_incident_by_id(self, guild_id, value):
        guild, _ = parse_incident_id(value)
        if guild != _id(guild_id):
            return None
        case = await self._read(self._case_ref(value))
        if case is None:
            return None
        evidence = await self._read(self._evidence_ref(value))
        if evidence and evidence.get("expires_at") and evidence["expires_at"] <= utc_now():
            evidence = None
        return _external({**case, "findings": (evidence or {}).get("findings", []),
            "excerpt": (evidence or {}).get("excerpt", ""), "user_name": (evidence or {}).get("user_name", ""),
            "inspection_notes": (evidence or {}).get("inspection_notes", ""), "attachment_metadata": (evidence or {}).get("attachment_metadata", []), "evidence_purged": evidence is None})

    async def list_violations(self, guild_id, user_id, *, limit=20, include_previous=True):
        guild, user = _id(guild_id), _id(user_id)
        _integer(limit, 1, 100, "조회 개수")
        member = self._member(await self._read(self._member_ref(guild, user)))
        query = self.guild_ref(guild).collection("incidents").where(filter=FieldFilter("user_id", "==", user))
        query = query.where(filter=FieldFilter("counted_ever", "==", True))
        if not include_previous:
            query = query.where(filter=FieldFilter("generation", "==", member["generation"]))
        rows = await self._query(query.order_by("created_at", direction="DESCENDING").limit(limit))
        return [_external({**row.to_dict(), "issued_at": row.to_dict()["created_at"],
            "active": row.to_dict().get("count_applied", False) and row.to_dict()["generation"] == member["generation"]}) for row in rows]

    async def list_pending_cases(self, guild_id=None, *, limit=100):
        _integer(limit, 1, 500, "조회 개수")
        guilds = [_id(guild_id)] if guild_id is not None else [int(row.id) for row in await self._query(self.client.collection(ROOT_COLLECTION))]
        result = []
        for guild in guilds:
            query = self.guild_ref(guild).collection("incidents").where(filter=FieldFilter("recovery_needed", "==", True))
            for row in await self._query(query.limit(limit - len(result))):
                result.append(_external(row.to_dict()))
            if len(result) >= limit:
                break
        return result

    async def mark_processed(self, value: str) -> bool:
        async def run(transaction):
            case = await self._get(transaction, self._case_ref(value))
            if case is None:
                return False
            if case.get("count_applied") and case.get("state") not in {"completed", "imported"}:
                return False
            transaction.update(self._case_ref(value), {"recovery_needed": False, "processed_at": utc_now()})
            return True
        return await self._transaction(run)

    async def record_admin_audit(self, guild_id, actor_id, action, detail, *, now=None):
        guild, actor = _id(guild_id), _id(actor_id)
        reference, data = self._audit_ref(guild), self._audit(actor, action, detail, _time(now))
        async def run(transaction):
            transaction.create(reference, data)
        await self._transaction(run)

    async def get_admin_audit(self, guild_id, *, limit=30):
        _integer(limit, 1, 100, "조회 개수")
        rows = await self._query(self.guild_ref(guild_id).collection("audit").order_by("at", direction="DESCENDING").limit(limit))
        return [_external({"audit_id": row.id, **row.to_dict()}) for row in rows]

    async def get_warning_audit(self, guild_id, value):
        guild, _ = parse_incident_id(value)
        if guild != _id(guild_id):
            return []
        rows = await self._query(self.guild_ref(guild).collection("audit").where(filter=FieldFilter("detail.incident_id", "==", value)))
        return [_external({"audit_id": row.id, **row.to_dict()}) for row in rows]

    async def get_reset_audit(self, guild_id, user_id):
        rows = await self._query(self.guild_ref(guild_id).collection("audit").where(filter=FieldFilter("detail.user_id", "==", _id(user_id))))
        return [_external({"audit_id": row.id, **row.to_dict()}) for row in rows if row.to_dict().get("action") == "reset_violations"]

    async def list_pending_logs(self, guild_id: int, *, limit=25):
        _integer(limit, 1, 100, "조회 개수")
        query = self.guild_ref(guild_id).collection("incidents").where(filter=FieldFilter("log_status", "in", ["failed", "pending", "unknown", "not_started"]))
        return [_external(row.to_dict()) for row in await self._query(query.order_by("log_attempt_at").limit(limit))]

    async def mark_log_deferred(self, value: str, reason: str = ""):
        now = utc_now()
        async def run(transaction):
            case = await self._get(transaction, self._case_ref(value))
            if case is None:
                return False
            transaction.update(self._case_ref(value), {"log_attempt_at": now, "log_deferred_reason": str(reason)[:200]})
            return True
        return await self._transaction(run)

    async def _count_query(self, query) -> int:
        self._require()
        try:
            result = await query.count().get(timeout=15)
            return int(result[0][0].value)
        except (google_errors.GoogleAPICallError, OSError, TimeoutError) as exc:
            self._healthy = False
            raise StorageUnavailable("Firestore 운영 상태 집계에 실패했습니다.") from exc

    async def storage_status(self, guild_id: int) -> dict:
        cases = self.guild_ref(guild_id).collection("incidents")
        result = {"backend": "cloud_firestore", "connected": self.healthy,
                  "incident_documents": await self._count_query(cases),
                  "evidence_documents": await self._count_query(self.guild_ref(guild_id).collection("evidence")),
                  "pending_cases": await self._count_query(cases.where(filter=FieldFilter("recovery_needed", "==", True)))}
        for state in ("pending", "failed", "unknown"):
            result[state + "_logs"] = await self._count_query(cases.where(filter=FieldFilter("log_status", "==", state)))
        return result

    async def save_original(self, guild_id, value, attachment_id, payload, sha256, *, now=None):
        guild, _ = parse_incident_id(value)
        if guild != _id(guild_id):
            return False
        config = await self.get_config(guild)
        if not config["retain_originals"] or self.runtime is None:
            return False
        if not isinstance(payload, bytes) or not 0 < len(payload) <= 8 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != sha256:
            raise ValueError("원본 이미지 크기·해시가 올바르지 않습니다.")
        path = self.runtime.original_path(guild, value, attachment_id, sha256)
        moment = _time(now)
        intent = {"attachment_id": str(attachment_id), "object_path": path, "generation": None,
                  "sha256": sha256, "size": len(payload), "status": "waiting", "upload_status": "pending", "created_at": moment}
        async def reserve(transaction):
            case = await self._get(transaction, self._case_ref(value))
            evidence = await self._get(transaction, self._evidence_ref(value))
            settings = self._config(await self._get(transaction, self._settings(guild)), guild)
            if not evidence or not case or case.get("state") != "completed" or not case.get("count_applied") or not settings["retain_originals"]:
                return False
            cleanup = copy.deepcopy(case.get("retention_cleanup", {"storage_objects": [], "log_messages": []}))
            files = cleanup.setdefault("storage_objects", [])
            if len(files) >= 4 or any(item.get("attachment_id") == str(attachment_id) for item in files):
                return False
            files.append(intent)
            transaction.update(self._case_ref(value), {"retention_cleanup": cleanup, "retention_state": "waiting"})
            return True
        if not await self._transaction(reserve):
            return False
        metadata = None
        failure = None
        try:
            # Intent is durable BEFORE this external Storage call.
            metadata = await self.runtime.upload_original(guild, value, attachment_id, payload, sha256, enabled=True)
        except Exception as exc:
            failure = {"upload_status": "unknown" if getattr(exc, "may_have_succeeded", False) else "failed",
                       "upload_error": type(exc).__name__}
        async def finish(transaction):
            case = await self._get(transaction, self._case_ref(value))
            evidence = await self._get(transaction, self._evidence_ref(value))
            if case is None:
                raise StorageUnavailable("원본 저장 의도 기록을 찾을 수 없습니다.")
            cleanup = copy.deepcopy(case["retention_cleanup"])
            replacement = {**intent, **(metadata or failure or {}), "upload_status": "succeeded" if metadata else failure["upload_status"]}
            cleanup["storage_objects"] = [replacement if item["object_path"] == path else item for item in cleanup["storage_objects"]]
            transaction.update(self._case_ref(value), {"retention_cleanup": cleanup})
            if evidence is not None and metadata:
                files = [item for item in evidence.get("originals", []) if item.get("attachment_id") != str(attachment_id)]
                transaction.update(self._evidence_ref(value), {"originals": [*files, replacement]})
        await self._transaction(finish)
        return metadata is not None

    async def due_retention(self, guild_id=None, *, now=None, limit=100):
        _integer(limit, 1, 500, "조회 개수")
        moment = _time(now)
        guilds = [_id(guild_id)] if guild_id is not None else [int(row.id) for row in await self._query(self.client.collection(ROOT_COLLECTION))]
        result = []
        for guild in guilds:
            query = self.guild_ref(guild).collection("incidents").where(filter=FieldFilter("retention_state", "in", ["waiting", "retry"]))
            query = query.where(filter=FieldFilter("retention_next_attempt_at", "<=", moment)).order_by("retention_next_attempt_at")
            for row in await self._query(query.limit(limit - len(result))):
                case = row.to_dict()
                cleanup = case.get("retention_cleanup", {})
                storage = [{**item, "reference": item["object_path"]} for item in cleanup.get("storage_objects", [])]
                logs = [{**item, "reference": f"{item['channel_id']}:{item['message_id']}"} for item in cleanup.get("log_messages", [])]
                result.append(_external({"incident_id": case["incident_id"], "expires_at": case["evidence_expires_at"],
                    "storage_objects": storage, "log_messages": logs, "evidence_deleted": case.get("evidence_deleted", False)}))
            if len(result) >= limit:
                break
        return result

    async def record_retention_result(self, value, kind, reference, status, detail=""):
        if kind not in {"storage", "log"} or status not in {"succeeded", "failed", "unknown", "skipped"}:
            raise ValueError("만료 정리 결과가 올바르지 않습니다.")
        now = utc_now()
        async def run(transaction):
            case = await self._get(transaction, self._case_ref(value))
            evidence = await self._get(transaction, self._evidence_ref(value))
            if case is None:
                return False
            cleanup = copy.deepcopy(case.get("retention_cleanup", {}))
            items = cleanup.get("storage_objects" if kind == "storage" else "log_messages", [])
            found = False
            for item in items:
                key = item["object_path"] if kind == "storage" else f"{item['channel_id']}:{item['message_id']}"
                if key == reference:
                    item.update(status=status, cleanup_detail=str(detail)[:200], cleaned_at=now)
                    found = True
            if not found:
                return False
            done = all(item.get("status") in {"succeeded", "skipped"} for item in [*cleanup.get("storage_objects", []), *cleanup.get("log_messages", [])])
            evidence_deleted = bool(case.get("evidence_deleted") or (evidence is None and case["evidence_expires_at"] <= now))
            state = "completed" if done and evidence_deleted else "retry"
            transaction.update(self._case_ref(value), {"retention_cleanup": cleanup, "retention_state": state,
                                                       "evidence_deleted": evidence_deleted})
            return True
        return await self._transaction(run)

    async def mark_retention_attempt(self, value: str, *, now=None, retry_seconds=600):
        """Rotate a failed remote cleanup behind other due work, without TTL delay."""
        _integer(retry_seconds, 60, 86400, "정리 재시도 간격")
        moment = _time(now)
        async def run(transaction):
            case = await self._get(transaction, self._case_ref(value))
            evidence = await self._get(transaction, self._evidence_ref(value))
            if case is None or case["evidence_expires_at"] > moment:
                return False
            cleanup = case.get("retention_cleanup", {})
            done = all(item.get("status") in {"succeeded", "skipped"} for item in
                [*cleanup.get("storage_objects", []), *cleanup.get("log_messages", [])])
            transaction.update(self._case_ref(value), {
                "retention_last_attempt_at": moment,
                "retention_next_attempt_at": moment + timedelta(seconds=retry_seconds),
                "evidence_deleted": evidence is None,
                "retention_state": "completed" if done and evidence is None else "retry",
            })
            return True
        return await self._transaction(run)

    async def purge_expired(self, *, now=None, guild_id=None):
        """Purge evidence only; durable Storage/log cleanup metadata survives TTL."""
        moment = _time(now)
        # Query the evidence collection independently of remote cleanup backoff.
        # Storage/Discord permission failures must never postpone Firestore TTL.
        guilds = [_id(guild_id)] if guild_id is not None else [int(row.id) for row in await self._query(self.client.collection(ROOT_COLLECTION))]
        due = []
        for guild in guilds:
            query = self.guild_ref(guild).collection("evidence").where(filter=FieldFilter("expires_at", "<=", moment)).order_by("expires_at")
            due.extend(incident_id(guild, row.id) for row in await self._query(query.limit(500 - len(due))))
            if len(due) >= 500:
                break
        deleted = 0
        for value in due:
            async def run(transaction):
                case = await self._get(transaction, self._case_ref(value))
                evidence = await self._get(transaction, self._evidence_ref(value))
                if case is None or case["evidence_expires_at"] > moment:
                    return False
                if evidence is not None and evidence["expires_at"] > moment:
                    return False
                if evidence is not None:
                    transaction.delete(self._evidence_ref(value))
                cleanup = case.get("retention_cleanup", {})
                done = all(entry.get("status") in {"succeeded", "skipped"} for entry in [*cleanup.get("storage_objects", []), *cleanup.get("log_messages", [])])
                transaction.update(self._case_ref(value), {"evidence_deleted": True, "retention_state": "completed" if done else "retry"})
                return evidence is not None
            deleted += int(await self._transaction(run))
        return deleted

    count_warnings = count_violations
    list_warnings = list_violations
    cancel_warning = cancel_violation



# ===========================================================================
# images
# ===========================================================================

"""Bounded Discord attachment downloads and conservative OCR interpretation.

Only URLs from actual Discord Attachment objects are accepted. Image decoding
and OCR run in the separate, killable OCRPool process, never in this module.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import aiohttp


MAX_IMAGES = 4
MAX_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MIN_OCR_CONFIDENCE = 0.85
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
OTHER_IMAGE_EXTENSIONS = {".gif", ".apng", ".bmp", ".tiff", ".tif", ".svg", ".avif", ".heic"}
CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}


class ImageDownloadError(ValueError):
    pass


def is_image_candidate(attachment) -> bool:
    extension = PurePosixPath(attachment.filename.lower()).suffix
    return extension in IMAGE_EXTENSIONS | OTHER_IMAGE_EXTENSIONS or (
        (attachment.content_type or "").lower().startswith("image/")
    )


def attachment_signature(message) -> tuple:
    # Attachment IDs identify immutable uploads; signed URL expiry is not an edit.
    return tuple((int(a.id), a.filename, int(a.size)) for a in message.attachments)


def message_fingerprint(message) -> str:
    payload = [message.content, attachment_signature(message)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def valid_attachment_url(url: str, attachment_id: int) -> bool:
    try:
        parsed = urlsplit(url)
        parts = parsed.path.split("/")
        return bool(
            len(url) <= 4096 and parsed.scheme == "https"
            and parsed.hostname in CDN_HOSTS and parsed.port in {None, 443}
            and parsed.username is None and parsed.password is None
            and len(parts) >= 5 and parts[1] == "attachments"
            and parts[2].isdigit() and parts[3] == str(attachment_id)
        )
    except (ValueError, TypeError):
        return False


async def download_attachment(session: aiohttp.ClientSession, attachment) -> bytes:
    if not 0 < attachment.size <= MAX_BYTES:
        raise ImageDownloadError("첨부 용량 한도 초과 또는 빈 파일")
    if PurePosixPath(attachment.filename.lower()).suffix not in IMAGE_EXTENSIONS:
        raise ImageDownloadError("지원하지 않는 첨부 확장자(PNG/JPEG/WebP만 검사)")
    if not valid_attachment_url(attachment.url, attachment.id):
        raise ImageDownloadError("Discord 직접 첨부 URL 검증 실패")
    try:
        timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=8)
        async with session.get(attachment.url, allow_redirects=False, timeout=timeout) as response:
            if response.status != 200:
                raise ImageDownloadError(f"첨부 다운로드 HTTP {response.status}")
            declared = response.content_length
            if declared is not None and declared > MAX_BYTES:
                raise ImageDownloadError("응답 용량 한도 초과")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                if len(data) + len(chunk) > MAX_BYTES:
                    raise ImageDownloadError("실제 다운로드 용량 한도 초과")
                data.extend(chunk)
            if not data:
                raise ImageDownloadError("빈 이미지 응답")
            return bytes(data)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ImageDownloadError(f"첨부 다운로드 실패({type(exc).__name__})") from exc


@dataclass
class InspectedImage:
    attachment_id: str
    filename: str
    result: OCRResult
    data: bytes = field(repr=False)


@dataclass
class Inspection:
    images: list[InspectedImage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blocked: bool = False

    def revision_hash(self, message) -> str:
        material = [message_fingerprint(message), [(image.attachment_id, image.result.sha256) for image in self.images]]
        return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode("utf-8")).hexdigest()


class AttachmentInspector:
    def __init__(self, session: aiohttp.ClientSession, pool: OCRPool):
        self.session = session
        self.pool = pool

    async def inspect(self, message) -> Inspection:
        candidates = [a for a in message.attachments if is_image_candidate(a)]
        inspection = Inspection()
        if len(candidates) > MAX_IMAGES:
            return Inspection(notes=[f"이미지 {len(candidates)}장: 메시지당 {MAX_IMAGES}장 한도 초과, 검토 필요"], blocked=True)
        total = 0
        for attachment in candidates:
            try:
                if total + attachment.size > MAX_TOTAL_BYTES:
                    raise ImageDownloadError("메시지 전체 이미지 용량 한도 초과")
                data = await download_attachment(self.session, attachment)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise ImageDownloadError("실제 메시지 전체 용량 한도 초과")
                result = await self.pool.submit(data, filename=attachment.filename, declared_mime=attachment.content_type)
                inspection.images.append(InspectedImage(str(attachment.id), attachment.filename, result, data))
                if result.status not in {"ok", "no_text"}:
                    inspection.blocked = True
                    inspection.notes.append(f"첨부 {attachment.id}: OCR {result.status} · {result.detail}")
                elif result.status == "no_text":
                    inspection.notes.append(f"첨부 {attachment.id}: 읽을 수 있는 글자 없음(이미지 의미 판정은 하지 않음)")
            except ImageDownloadError as exc:
                inspection.blocked = True
                inspection.notes.append(f"첨부 {attachment.id}: {exc}")
        return inspection


def reported_image_context(caption: str) -> bool:
    """A bare '신고합니다' is insufficient. This only downgrades IMAGE findings.

    Evidence/report framing with a separate content descriptor is inherently
    ambiguous. Text attacks elsewhere in the caption still undergo full checks.
    """
    return bool(
        re.search(r"신고|교육|설명|예시|도움|피해", caption)
        and re.search(r"받았|들었|당했|대화|캡처|증거|욕설|발언|메시지|이런\s*말", caption)
    )


def image_findings(engine: Engine, inspection: Inspection, caption: str, category_modes: dict) -> list[Finding]:
    findings: list[Finding] = []
    report = reported_image_context(caption)
    for image in inspection.images:
        if not image.result.lines:
            continue
        text = "\n".join(line.text for line in image.result.lines)
        if len(text) > 20000:
            # Never treat an OCR output truncated before context as certain.
            text = text[:20000]
            inspection.blocked = True
            inspection.notes.append(f"첨부 {image.attachment_id}: OCR 글자 수 한도 초과")
        quality = min(line.confidence for line in image.result.lines)
        uncertain = image.result.status != "ok" or quality < MIN_OCR_CONFIDENCE or report
        for finding in engine.scan(text, category_modes=category_modes):
            extra = ""
            if report:
                extra = "이미지에 대한 신고·교육 설명 정황: 관리자 검토"
            elif uncertain:
                extra = f"OCR 판독 품질/상태 부족({quality:.3f}, {image.result.status}): 관리자 검토"
            findings.append(replace(
                finding, source="image", attachment_id=image.attachment_id,
                confidence="review" if uncertain else finding.confidence,
                recommendation="review" if uncertain else finding.recommendation,
                reason=finding.reason + ("; " + extra if extra else "; OCR 판독 기준 통과"),
            ))
    return findings



# ===========================================================================
# service
# ===========================================================================

"""Discord-independent orchestration: detect, commit, then perform claimed actions.

The database is the source of warning idempotency.  The in-memory digest cache is
only an optimization and never substitutes for the database's unique constraint.
No chat content that has never produced a finding is written to the database.
"""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol


LOGGER = logging.getLogger(__name__)
UTC = timezone.utc
ACTION_TIMEOUT_SECONDS = 45
PENDING_GRACE_SECONDS = 120


@dataclass(frozen=True)
class MessageEvent:
    guild_id: int
    channel_id: int
    message_id: int
    user_id: int
    content: str
    created_at: datetime
    edited_at: datetime | None = None
    role_ids: tuple[int, ...] = ()
    parent_channel_id: int | None = None
    is_bot: bool = False
    is_webhook: bool = False
    image_findings: tuple[Finding, ...] = ()
    ocr_inputs: tuple[dict, ...] = ()
    inspection_notes: tuple[str, ...] = ()
    content_revision_hash: str | None = None
    expected_generation: int | None = None
    user_name: str = ""
    enforcement_blocked: bool = False
    message_signature: str | None = None
    attachment_metadata: tuple[dict, ...] = ()

    @property
    def revision_time(self) -> datetime:
        return self.edited_at or self.created_at


@dataclass(frozen=True)
class ActionResult:
    status: str
    detail: str = ""
    code: str = ""
    message_id: int | None = None
    channel_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProcessResult:
    incident_id: str | None = None
    findings: list[Finding] = field(default_factory=list)
    violation_added: bool = False
    count: int = 0
    actions: dict[str, ActionResult] = field(default_factory=dict)
    skipped: str = ""
    error: str | None = None
    retryable: bool = False

    @property
    def warning_added(self) -> bool:
        """Compatibility alias; the authoritative unit is a lifetime violation."""
        return self.violation_added


@dataclass(frozen=True)
class Preview:
    findings: list[Finding]
    mode: str
    recommended_action: str


class ActionAdapter(Protocol):
    """The Cog implements these methods using the *current* Discord message.

    A timeout adapter must re-fetch the member, check owner/administrator/role
    restrictions, and never shorten an already longer timeout.  Notifications
    enforce their own per-guild/user/kind cooldown without skipping detection.
    Uncertain external outcomes are recorded as unknown, without automatic retry.
    """

    async def preflight(self, incident_id: str, *, is_repeat: bool) -> ActionResult: ...

    async def delete(self, incident_id: str) -> ActionResult: ...

    async def timeout(self, until: datetime, incident_id: str) -> ActionResult: ...

    async def notify(
        self, kind: str, incident_id: str, count: int, cooldown: int, *, policy: dict
    ) -> ActionResult: ...

    async def log(
        self, incident: dict, findings: list[Finding], actions: dict[str, ActionResult], *, retry: bool = False
    ) -> ActionResult: ...

    def bind_case(self, incident: dict) -> None: ...

    async def resolve_log(self, incident: dict) -> ActionResult: ...

    async def alert(self, error: str) -> None: ...


class ModerationService:
    """One instance per Cog.  Only one running bot process is recommended.

    Firestore first commits the case, count and immutable action plan. Known or
    uncertain technical failures are compensated through an audited transaction.
    No Discord operation is performed inside a transaction callback. If a process dies
    between an API response and the database write, the action remains unknown
    and is not retried automatically.  Exactly-once delivery across Discord and
    Firestore is impossible; preventing duplicate punishment takes precedence.
    """

    def __init__(
        self,
        store: Store,
        rules_path: str | Path | None = None,
        *,
        max_seen: int = 10_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.rules_path = None if rules_path is None else Path(rules_path)
        self.max_seen = max_seen
        self.clock = clock or (lambda: datetime.now(UTC))
        self.halted = False
        self._seen: OrderedDict[
            tuple[int, int], tuple[str, datetime, int]
        ] = OrderedDict()
        self._engines: OrderedDict[int, tuple[int, Engine]] = OrderedDict()
        self._rules_epoch = 0
        self._locks = tuple(asyncio.Lock() for _ in range(128))
        self._policy_locks = tuple(asyncio.Lock() for _ in range(64))
        self._last_alert: dict[int, float] = {}

    def user_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        """Serialize warning/action workflows with administrator cancellation."""
        return self._locks[hash((guild_id, user_id)) % len(self._locks)]

    def policy_lock(self, guild_id: int) -> asyncio.Lock:
        """Serialize policy mutations with classification and incident commit.

        Lock order is user then policy. Administrative policy changes acquire
        only this lock; warning cancellation acquires only the user lock.
        No Discord side effect runs while this policy lock is held.
        """
        return self._policy_locks[guild_id % len(self._policy_locks)]

    def invalidate(self, guild_id: int) -> None:
        """Call after every guild configuration, allow-list or rule mutation."""
        self._rules_epoch += 1
        self._engines.pop(guild_id, None)
        for key in tuple(self._seen):
            if key[0] == guild_id:
                # Forget the digest but retain the highest revision timestamp:
                # a configuration command must not revive a stale original
                # event that arrived after its newer, clean edit.
                self._seen[key] = ("", self._seen[key][1], -1)

    def resume_after_reconnect(self) -> None:
        """Caller must first complete the backend's real connectivity probe."""
        if not self.store.healthy:
            raise StorageUnavailable("Firestore 연결 확인 없이 재개할 수 없습니다")
        self.halted = False
        self._engines.clear()
        self._seen.clear()

    async def get_engine(self, guild_id: int) -> Engine:
        config = await self.store.get_config(guild_id)
        version = int(config.get("config_version", 0))
        cached = self._engines.get(guild_id)
        if cached is not None and cached[0] == version:
            self._engines.move_to_end(guild_id)
            return cached[1]
        while True:
            epoch = self._rules_epoch
            state = await self.store.list_rules(guild_id)
            allows = await self.store.list_allow(guild_id)
            engine = Engine.from_file(self.rules_path, custom_rules=state["custom_rules"],
                                      disabled_ids=state["disabled_ids"], allow_terms=allows)
            fresh = await self.store.get_config(guild_id)
            current = int(fresh.get("config_version", 0))
            if epoch == self._rules_epoch and current == version:
                break
            version = current
        self._engines[guild_id] = (version, engine)
        while len(self._engines) > 256:
            self._engines.popitem(last=False)
        return engine

    async def test_text(self, guild_id: int, text: str) -> Preview:
        """A read-only preview: no incident, warning or action is created."""
        async with self.policy_lock(guild_id):
            config = await self.store.get_config(guild_id)
            engine = await self.get_engine(guild_id)
            findings = engine.scan(text, category_modes=config["category_modes"])
        clear = self._clear(findings)
        if self.halted or not self.store.healthy:
            action = "저장 상태 불확실: 자동 조치 중지"
        elif not findings:
            action = "감지 없음"
        elif config["mode"] == "off" or not config["setup_complete"]:
            action = "끄기: 실제 이벤트에서는 검사·조치하지 않음"
        elif config["mode"] == "observe":
            action = "관찰: 기록만 남김. 삭제·경고·타임아웃 없음"
        elif not clear:
            action = "관리자 검토: 자동 삭제·경고·타임아웃 없음"
        else:
            action = (
                "적용: 권한 확인 → Firestore 트랜잭션에 사건·누적·처벌 계획 저장 → "
                "저장된 계획대로 Discord 조치. 메시지당 누적 최대 1회. "
                f"처음 {config['warning_limit']}회까지 경고, 그 이후 매 위반마다 "
                f"{config['timeout_seconds']}초 타임아웃. 기술적 실패는 누적하지 않음"
            )
        return Preview(findings, config["mode"], action)

    @staticmethod
    def _clear(findings: list[Finding]) -> bool:
        return any(
            f.confidence == "clear" and f.recommendation == "enforce"
            for f in findings
        )

    @staticmethod
    def _excerpt(findings: list[Finding]) -> str:
        # No surrounding full message: only minimal matched fragments.
        fragments = list(dict.fromkeys(f.matched for f in findings))
        return " | ".join(fragments)[:180]

    def _remember(self, event: MessageEvent, digest: str, version: int = -1) -> None:
        key = (event.guild_id, event.message_id)
        self._seen[key] = (digest, event.revision_time, version)
        self._seen.move_to_end(key)
        while len(self._seen) > self.max_seen:
            self._seen.popitem(last=False)

    async def _alert(self, event: MessageEvent | int, adapter: ActionAdapter, reason: str) -> None:
        guild_id = event if isinstance(event, int) else event.guild_id
        # Console remains available even when a log channel or DB is unavailable.
        LOGGER.error("AutoMod guild=%s: %s", guild_id, reason)
        current = time.monotonic()
        if current - self._last_alert.get(guild_id, -1e10) < 60:
            return
        self._last_alert[guild_id] = current
        if len(self._last_alert) > 4096:
            self._last_alert.pop(next(iter(self._last_alert)))
        try:
            await adapter.alert(reason)
        except Exception as exc:
            LOGGER.error("AutoMod operator alert failed: %s", type(exc).__name__)

    def _require_healthy(self) -> None:
        if self.halted or not self.store.healthy:
            raise StorageUnavailable("자동관리 저장 상태 확인 필요")

    async def _inspect(self, event: MessageEvent, result: ProcessResult):
        """Called under user -> policy locks; reserve evidence without counting."""
        self._require_healthy()
        config = await self.store.get_config(event.guild_id)
        if config["mode"] == "off" or not config["setup_complete"]:
            result.skipped = "off"
            return None
        if event.channel_id in config["exempt_channel_ids"] or (
            event.parent_channel_id is not None
            and event.parent_channel_id in config["exempt_channel_ids"]
        ) or set(event.role_ids).intersection(config["exempt_role_ids"]):
            result.skipped = "exempt"
            return None
        generation = await self.store.get_user_generation(event.guild_id, event.user_id)
        if event.expected_generation is not None and event.expected_generation != generation:
            result.skipped = "generation_changed"
            return None
        digest = event.content_revision_hash or hashlib.sha256(event.content.encode("utf-8")).hexdigest()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("내용 변경 해시는 SHA-256 소문자 64자리여야 합니다")
        seen = self._seen.get((event.guild_id, event.message_id))
        if seen is not None:
            previous_digest, revision, seen_version = seen
            if event.revision_time < revision:
                result.skipped = "stale"
                return None
            if previous_digest == digest and seen_version == int(config.get("config_version", 0)):
                result.skipped = "duplicate"
                self._remember(event, digest, int(config.get("config_version", 0)))
                return None
        engine = await self.get_engine(event.guild_id)
        findings = engine.scan(event.content, category_modes=config["category_modes"])
        # OCR extracts bytes/text outside the policy lock, but classification
        # happens HERE with current rules, allow terms and category settings.
        # Never join separate images/body and invent a target across sources.
        if config["image_enabled"]:
            findings.extend(event.image_findings)
            reporting = reported_image_context(event.content)
            for image in event.ocr_inputs:
                raw_text = str(image.get("text", ""))
                text = raw_text[:20000]
                try:
                    confidence = float(image.get("confidence", 0))
                    attachment_id = int(image["attachment_id"])
                except (ValueError, TypeError, KeyError):
                    confidence, attachment_id = 0.0, None
                uncertain = (
                    image.get("status") != "ok" or not 0.85 <= confidence <= 1.0
                    or reporting or len(raw_text) > 20000
                )
                for finding in engine.scan(text, category_modes=config["category_modes"]):
                    mapped = replace(finding, source="image", attachment_id=attachment_id)
                    if uncertain:
                        mapped = replace(
                            mapped, confidence="review", recommendation="review",
                            reason=mapped.reason + "; OCR 불확실성 또는 신고·설명 문맥으로 검토",
                        )
                    findings.append(mapped)
        if event.enforcement_blocked:
            findings = [replace(
                finding, confidence="review", recommendation="review",
                reason=finding.reason + "; 이미지 검사 미완료로 자동 제재 보류",
            ) if finding.source == "image" else finding for finding in findings]
        result.findings = findings
        notes = " | ".join(event.inspection_notes)[:1000]
        if not findings and not event.enforcement_blocked:
            # A normal photograph with no readable text is not an incident.
            notes = ""
        if not findings and not notes:
            prior = await self.store.get_incident(event.guild_id, event.message_id)
            if prior is None:
                result.skipped = "clean"
                self._remember(event, digest, int(config.get("config_version", 0)))
                return None
        return config, findings, digest, generation, notes

    @staticmethod
    def _aware(value) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("시간대가 있는 저장 시각이 필요합니다")
        return value.astimezone(UTC)

    async def _failure(self, result, guild_id, adapter, error):
        self.halted = True
        result.error = "Firestore/처리 상태 불확실: 자동 처벌 중지. 연결 검증 후 미완료 사건을 복구하세요."
        result.retryable = True
        await self._alert(guild_id, adapter, result.error)
        LOGGER.error("AutoMod failure class=%s", type(error).__name__)
        return result

    async def _readonly(self, operation):
        try:
            answer = await asyncio.wait_for(operation(), ACTION_TIMEOUT_SECONDS)
            return answer if isinstance(answer, ActionResult) else ActionResult("failed", "조회 결과 형식 오류", "invalid_result")
        except StorageUnavailable:
            raise
        except Exception as exc:
            return ActionResult("failed", f"사전 조회 실패({type(exc).__name__})", "preflight_failed")

    async def process(self, event: MessageEvent, adapter: ActionAdapter) -> ProcessResult:
        result = ProcessResult()
        if not event.guild_id or event.is_bot or event.is_webhook:
            result.skipped = "out_of_scope"
            return result
        if event.revision_time.tzinfo is None or event.created_at.tzinfo is None:
            result.error = "시간대 없는 메시지는 처리하지 않음"
            return result
        async with self.user_lock(event.guild_id, event.user_id):
            try:
                async with self.policy_lock(event.guild_id):
                    inspected = await self._inspect(event, result)
                if inspected is None:
                    return result
                config, findings, digest, generation, notes = inspected
                version = int(config.get("config_version", 0))
                previous = await self.store.get_incident(event.guild_id, event.message_id)
                if previous and previous.get("plan") and previous["plan"].get("original_content_hash") == digest:
                    result.incident_id = previous["incident_id"]
                    result.count = await self.store.count_violations(event.guild_id, event.user_id)
                    await self._execute(previous, adapter, result)
                    if not result.retryable and result.error is None:
                        await self.store.mark_processed(previous["incident_id"])
                        self._remember(event, digest, version)
                    return result
                candidate = config["mode"] == "enforce" and self._clear(findings)
                preflight = None
                if candidate:
                    count = await self.store.count_violations(event.guild_id, event.user_id)
                    preflight = await self._readonly(lambda: adapter.preflight(
                        f"{event.guild_id}:{event.message_id}", is_repeat=count >= config["warning_limit"]))
                    candidate = preflight.status == "succeeded"
                    if not candidate:
                        notes = (notes + " | " + preflight.detail).strip(" |")
                method = self.store.prepare_incident if candidate else self.store.record_review
                async with self.policy_lock(event.guild_id):
                    prepared = await method(
                        event.guild_id, event.message_id, event.user_id, event.channel_id,
                        digest, [f.to_dict() for f in findings], self._excerpt(findings),
                        expected_generation=generation, expected_config_version=version,
                        user_name=event.user_name[:100], inspection_notes=notes,
                        message_signature=event.message_signature or "",
                        message_created_at=event.created_at, message_edited_at=event.edited_at,
                        attachment_metadata=list(event.attachment_metadata), now=self.clock(),
                    )
                result.incident_id = prepared.incident_id or None
                result.violation_added = prepared.new_violation
                result.count = prepared.count
                if not prepared.incident_id:
                    result.skipped = prepared.reason or "not_prepared"
                    result.retryable = result.skipped == "user_busy"
                    return result
                case = await self.store.get_incident_by_id(event.guild_id, prepared.incident_id)
                if case is None:
                    raise StorageUnavailable("기록한 사건을 Firestore에서 확인할 수 없음")
                if preflight is not None:
                    result.actions["preflight"] = await self._known(case, "preflight", preflight)
                if candidate and case.get("plan"):
                    await self._execute(case, adapter, result)
                else:
                    await self._log_case(case, adapter, result)
                if not result.retryable and result.error is None:
                    await self.store.mark_processed(case["incident_id"])
                    self._remember(event, digest, version)
                return result
            except Exception as exc:
                return await self._failure(result, event.guild_id, adapter, exc)

    async def _rows(self, incident_id: str) -> dict:
        rows = await self.store.get_actions(incident_id)
        if isinstance(rows, dict):
            return rows
        return {row["kind"]: row for row in rows
                if row.get("action_key", row.get("key", "once")) == "once"}

    def _from_row(self, row: dict) -> ActionResult:
        status = row.get("status", "unknown")
        if status == "pending":
            stamp = self._aware(row.get("claimed_at", row.get("created_at")))
            if stamp is not None and (self._aware(self.clock()) - stamp).total_seconds() < PENDING_GRACE_SECONDS:
                return ActionResult("skipped", "다른 처리 또는 복구 유예 중", "in_progress")
            return ActionResult("unknown", "이전 조치 완료 여부를 확인할 수 없음", "stale_pending")
        return ActionResult(status, row.get("detail", ""), row.get("code", ""),
                            row.get("discord_message_id", row.get("message_id")), row.get("discord_channel_id", row.get("channel_id")))

    async def _known(self, case: dict, kind: str, answer: ActionResult) -> ActionResult:
        if await self.store.claim_action(case["incident_id"], kind, key="once"):
            await self.store.finish_action(case["incident_id"], kind, answer.status, answer.detail,
                                           key="once", code=answer.code)
            return answer
        row = (await self._rows(case["incident_id"])).get(kind)
        return self._from_row(row) if row else ActionResult("skipped", "이전 기록 존재", "already_recorded")

    async def _perform(self, case, kind, operation, *, retry=False):
        self._require_healthy()
        existing = (await self._rows(case["incident_id"])).get(kind)
        if existing is not None and not retry:
            return self._from_row(existing)
        claimed = await self.store.claim_action(case["incident_id"], kind, key="once", allow_retry=retry)
        if not claimed:
            row = (await self._rows(case["incident_id"])).get(kind)
            return self._from_row(row) if row else ActionResult("skipped", "조치 예약 불가", "claim_refused")
        try:
            answer = await asyncio.wait_for(operation(), ACTION_TIMEOUT_SECONDS)
            if not isinstance(answer, ActionResult) or answer.status not in {"succeeded", "failed", "skipped", "unknown"}:
                answer = ActionResult("unknown", "API 결과를 확인할 수 없음", "invalid_result")
        except StorageUnavailable:
            self.halted = True
            raise
        except PermissionError as exc:
            answer = ActionResult("failed", f"권한 거부({type(exc).__name__})", "permission")
        except Exception as exc:
            answer = ActionResult("unknown", f"API 결과 확인 불가({type(exc).__name__})", "transport_unknown")
        await self.store.finish_action(case["incident_id"], kind, answer.status, answer.detail[:500],
                                      key="once", code=answer.code, discord_message_id=answer.message_id,
                                      discord_channel_id=answer.channel_id)
        return answer

    async def _compensate(self, case, result, reason):
        await self.store.void_violation(case["guild_id"], case["incident_id"], reason)
        result.violation_added = False
        result.count = await self.store.count_violations(case["guild_id"], case["user_id"])
        result.actions["violation"] = ActionResult("skipped", "감사 이력을 남기고 유효 누적에서 제외", reason)

    async def _current(self, case, result):
        current = await self.store.execution_status(case["incident_id"])
        if current["eligible"]:
            return True
        reason = current.get("reason", "execution_held")
        result.skipped = reason
        if case.get("count_applied") and not case.get("imported") and case.get("state") != "completed":
            await self._compensate(case, result, "execution_held:" + reason)
        return False

    async def _execute(self, case, adapter, result):
        if case.get("imported"):
            result.skipped = "imported"
            return
        if hasattr(adapter, "bind_case"):
            adapter.bind_case(case)
        rows = await self._rows(case["incident_id"])
        for kind in ("delete", "timeout"):
            if kind not in rows:
                continue
            action = self._from_row(rows[kind])
            result.actions[kind] = action
            if action.code == "in_progress":
                result.skipped, result.retryable = "in_progress", True
                return
            if action.status == "unknown":
                await self._compensate(case, result, "action_unknown:" + kind)
                await self._log_case(case, adapter, result)
                return
        if not await self._current(case, result):
            await self._log_case(case, adapter, result)
            return
        plan = case["plan"]
        if "delete" not in rows:
            checked = await self._readonly(lambda: adapter.preflight(
                case["incident_id"], is_repeat=bool(plan["should_timeout"])))
            if checked.status != "succeeded":
                result.actions["preflight"] = checked
                await self._compensate(case, result, "preflight_failed:" + checked.code)
                await self._log_case(case, adapter, result)
                return
        result.actions["delete"] = await self._perform(case, "delete", lambda: adapter.delete(case["incident_id"]))
        deleted = result.actions["delete"]
        if deleted.code == "in_progress":
            result.skipped, result.retryable = "in_progress", True
            return
        if deleted.status != "succeeded":
            await self._compensate(case, result, ("action_unknown:" if deleted.status == "unknown" else "delete_failed:") + deleted.code)
            await self._log_case(case, adapter, result)
            return
        if plan["should_timeout"]:
            if not await self._current(case, result):
                await self._log_case(case, adapter, result)
                return
            until = self._aware(plan["timeout_until"])
            prior = (await self._rows(case["incident_id"])).get("timeout")
            if prior is None and (until is None or until <= self._aware(self.clock())):
                result.actions["timeout"] = await self._known(case, "timeout", ActionResult(
                    "skipped", "저장된 종료 시각 경과: 타임아웃 기간을 다시 더하지 않음", "expired_plan"))
            else:
                result.actions["timeout"] = await self._perform(case, "timeout", lambda: adapter.timeout(until, case["incident_id"]))
            timed = result.actions["timeout"]
            if timed.code == "in_progress":
                result.skipped, result.retryable = "in_progress", True
                return
            if timed.status != "succeeded" and not (timed.status == "skipped" and timed.code == "existing_longer"):
                await self._compensate(case, result, ("action_unknown:" if timed.status == "unknown" else "timeout_failed:") + timed.code)
                await self._log_case(case, adapter, result)
                return
        else:
            result.actions["timeout"] = await self._known(case, "timeout", ActionResult(
                "skipped", "저장된 계획의 경고 허용 횟수 이내", "warning_only"))
        result.actions["violation"] = ActionResult("succeeded", "Firestore 사건·누적·계획 저장 완료", "committed")
        result.count = await self.store.count_violations(case["guild_id"], case["user_id"])
        policy = dict(plan)
        policy.update(timeout_status=result.actions["timeout"].status, timeout_code=result.actions["timeout"].code)
        policy.setdefault("public_delete_after", 15)
        cooldown = int(plan.get("notice_cooldown_seconds", 60))
        decision_count = int(plan.get("count", plan.get("count_after", result.count)))
        result.actions["dm"] = await self._perform(case, "dm", lambda: adapter.notify(
            "dm", case["incident_id"], decision_count, cooldown, policy=policy))
        if result.actions["dm"].status == "failed" and plan.get("public_fallback", True):
            result.actions["public"] = await self._perform(case, "public", lambda: adapter.notify(
                "public", case["incident_id"], decision_count, cooldown, policy=policy))
        else:
            result.actions["public"] = await self._known(case, "public", ActionResult(
                "skipped", "확정 DM 실패 대체 안내 조건 아님", "no_fallback"))
        await self._log_case(case, adapter, result)

    @staticmethod
    def _case_findings(case):
        fields = Finding.__dataclass_fields__
        return [Finding(**{key: value for key, value in item.items() if key in fields})
                for item in case.get("findings", [])]

    async def _log_case(self, case, adapter, result, *, retry=False):
        latest = await self.store.get_incident_by_id(case["guild_id"], case["incident_id"])
        if latest is not None:
            case = latest
        rows = await self._rows(case["incident_id"])
        for kind, row in rows.items():
            result.actions.setdefault(kind, self._from_row(row))
        prior_log = rows.get("log") or {}
        if not retry and prior_log.get("status") == "succeeded" and prior_log.get("discord_message_id"):
            channel_id = prior_log.get("discord_channel_id") or (case.get("plan") or {}).get("log_channel_id")
            if channel_id and await self.store.reconcile_log(
                case["incident_id"], int(prior_log["discord_message_id"]), int(channel_id)
            ):
                # A review may become a confirmed violation after an edit, or
                # the latest evidence/actions may change. Refresh the known
                # Discord log in place; never create another message here.
                retry = True
        findings = self._case_findings(case)
        if not result.findings:
            result.findings = findings
        payload = dict(case)
        payload.update(count=result.count, warning_count=result.count,
                       new_violation=result.violation_added, new_warning=result.violation_added,
                       settings=case.get("plan") or {}, inspection_notes=case.get("inspection_notes", []))
        for field in ("created_at", "message_created_at", "updated_at"):
            if payload.get(field) is not None:
                payload[field] = self._aware(payload[field]).isoformat()
        result.actions["log"] = await self._perform(case, "log", lambda: adapter.log(
            payload, findings, dict(result.actions), retry=retry), retry=retry)
        if result.actions["log"].status in {"failed", "unknown"}:
            await self._alert(case["guild_id"], adapter, "Discord 운영 로그 전송 실패·불확실. Firestore 원본 사건과 전달 상태를 확인하세요.")

    async def recover_case(self, guild_id, incident_id, adapter):
        """Resume saved phases only; no count increment or new timeout deadline."""
        result = ProcessResult(incident_id=incident_id)
        try:
            self._require_healthy()
            case = await self.store.get_incident_by_id(guild_id, incident_id)
            if case is None or case.get("imported"):
                result.skipped = "missing_case" if case is None else "imported"
                return result
            async with self.user_lock(guild_id, case["user_id"]):
                result.count = await self.store.count_violations(guild_id, case["user_id"])
                if case.get("plan"):
                    await self._execute(case, adapter, result)
                else:
                    await self._log_case(case, adapter, result)
                if not result.retryable and result.error is None:
                    await self.store.mark_processed(case["incident_id"])
            return result
        except Exception as exc:
            return await self._failure(result, guild_id, adapter, exc)

    async def retry_log(self, guild_id, incident_id, adapter):
        """Explicit LOG ONLY retry/edit. No deletion, timeout, DM or count change."""
        result = ProcessResult(incident_id=incident_id)
        try:
            self._require_healthy()
            case = await self.store.get_incident_by_id(guild_id, incident_id)
            if case is None:
                result.skipped = "missing_case"
                return result
            async with self.user_lock(guild_id, case["user_id"]):
                result.count = await self.store.count_violations(guild_id, case["user_id"])
                row = (await self._rows(incident_id)).get("log")
                known_id = case.get("log_message_id") or (row or {}).get("discord_message_id")
                channel_id = case.get("log_channel_id") or (row or {}).get("discord_channel_id") or (case.get("plan") or {}).get("log_channel_id")
                if known_id:
                    await self.store.reconcile_log(incident_id, int(known_id), int(channel_id) if channel_id else None)
                elif row and row.get("status") in {"pending", "unknown", "succeeded"}:
                    resolver = getattr(adapter, "resolve_log", None)
                    found = await self._readonly(lambda: resolver(case)) if resolver else ActionResult("unknown")
                    if found.message_id is None:
                        result.actions["log"] = ActionResult("unknown", found.detail or "기존 전송 확인 불가: 중복 전송 보류", "needs_reconciliation")
                        await self.store.mark_log_deferred(incident_id, found.code or "needs_reconciliation")
                        return result
                    await self.store.reconcile_log(incident_id, found.message_id, found.channel_id)
                await self._log_case(case, adapter, result, retry=True)
                action = result.actions.get("log")
                if action is not None and action.status != "succeeded":
                    # Rotate unresolved receipts after one bounded read-only
                    # check so they cannot starve newer recoverable logs.
                    await self.store.mark_log_deferred(incident_id, action.code or action.status)
            return result
        except Exception as exc:
            return await self._failure(result, guild_id, adapter, exc)



# ===========================================================================
# discord_io
# ===========================================================================

"""Discord permission checks, redacted logging and real moderation actions."""

import io
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord


UTC = timezone.utc
NO_MENTIONS = discord.AllowedMentions.none()
MODES = {"observe": "관찰", "enforce": "적용", "off": "끄기"}


def safe_text(value: object, limit: int = 1500) -> str:
    text = "".join(ch if ch in "\n\t" or unicodedata.category(ch) not in {"Cc", "Cf"} else "�" for ch in str(value))
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))[:limit]


def shown_time(value: str | None, timezone_name: str) -> str:
    if not value:
        return "없음"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return "시간대 없는 값(확인 필요)"
        return stamp.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, ZoneInfoNotFoundError):
        return "시각 표시 실패"


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]{0,5})([mhd])", value.strip().lower())
    if not match:
        raise ValueError("시간은 30m, 1h, 2h, 1d 형식으로 입력하세요.")
    seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
    if not 60 <= seconds <= 28 * 86400:
        raise ValueError("타임아웃은 1분 이상 28일 이하여야 합니다.")
    return seconds


def duration_label(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}일"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}시간"
    return f"{seconds // 60}분"


async def respond(interaction: discord.Interaction, text: str) -> None:
    kwargs: dict = {"ephemeral": True, "allowed_mentions": NO_MENTIONS}
    if len(text) > 1900:
        kwargs.update(content="전체 결과를 첨부했습니다. 본인에게만 표시됩니다.",
                      file=discord.File(io.BytesIO(text.encode("utf-8")), "automod-result.txt"))
    else:
        kwargs["content"] = text
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def private_log_proof(channel, config: dict, bot_id: int) -> tuple[bool, str]:
    """Use a freshly fetched channel; any uncertain ordinary-user grant fails.

    Manage Guild alone is intentionally NOT an allowed operating role. Owner,
    Administrator, explicitly selected operator roles and this bot are trusted.
    The channel's effective own overwrites include synchronized category grants.
    """
    if not isinstance(channel, discord.TextChannel) or channel.is_news():
        return False, "일반 비공개 텍스트 채널이 아님"
    guild = channel.guild
    try:
        roles = {role.id: role for role in await guild.fetch_roles()}
        default = roles.get(guild.id)
        if default is None or default.permissions.administrator:
            return False, "기본 역할 권한 확인 불가 또는 전체 관리자 권한"
        overwrites = channel.overwrites
        everyone = next((ow for target, ow in overwrites.items() if target.id == guild.id), None)
        if everyone is None or everyone.view_channel is not False:
            return False, "@everyone의 채널 보기 거부가 명시되지 않음"
        allowed_roles = set(config["operator_role_ids"])

        def trusted(role) -> bool:
            return bool(role.permissions.administrator or role.id in allowed_roles
                        or (role.tags is not None and role.tags.bot_id == bot_id))

        for target, overwrite in overwrites.items():
            if overwrite.view_channel is not True:
                continue
            if isinstance(target, discord.Role) or (isinstance(target, discord.Object) and target.type is discord.Role):
                role = roles.get(target.id)
                if role is None or not trusted(role):
                    return False, "일반 역할 또는 확인되지 않은 역할이 로그를 볼 수 있음"
            else:
                if target.id in {bot_id, guild.owner_id}:
                    continue
                member = await guild.fetch_member(target.id)
                if not any(trusted(roles[r.id]) for r in member.roles if r.id in roles):
                    return False, "일반 사용자에게 개별 보기 권한이 허용됨"
        # Prove the designated role can actually access the channel, too.
        for role_id in allowed_roles:
            role = roles.get(role_id)
            if role is None:
                return False, "지정 운영 역할이 삭제되었거나 확인되지 않음"
            if not role.permissions.administrator:
                overwrite = next((ow for target, ow in overwrites.items() if target.id == role_id), None)
                if overwrite is None or overwrite.view_channel is not True:
                    return False, "지정 운영 역할의 채널 보기 허용 필요"
        return True, "소유자·Administrator·지정 운영 역할·봇만 접근 가능"
    except (discord.HTTPException, discord.InvalidData, AttributeError):
        return False, "권한 조회 실패: 민감한 로그 전송 중단"


class UnavailableMessageChannel:
    """Known 403/404 for the original channel, without blocking other phases.

    A recovery adapter can still send a DM or write the private operating log.
    Any attempt to read/delete or publicly notify in the original channel must
    preserve the observed failure; it must never invent message contents.
    """

    def __init__(self, channel_id: int, error: discord.NotFound | discord.Forbidden,
                 *, parent_id: int | None = None):
        if not isinstance(error, (discord.NotFound, discord.Forbidden)):
            raise TypeError("Known channel NotFound or Forbidden is required")
        self.id = int(channel_id)
        self.parent_id = parent_id
        self._error = error

    async def fetch_message(self, message_id: int):
        raise self._error

    async def send(self, *args, **kwargs):
        raise self._error

    def permissions_for(self, member) -> discord.Permissions:
        return discord.Permissions.none()


class DiscordAdapter:
    def __init__(self, cog, message):
        self.cog = cog
        self.message = message
        self.guild = message.guild
        self.fingerprint = message_fingerprint(message)

    def bind_case(self, incident: dict) -> None:
        """Use the version actually analysed, including after a process restart."""
        saved = incident.get("plan", {}).get("original_message_fingerprint")
        self.fingerprint = saved or "missing-saved-fingerprint-do-not-delete"

    async def _current_target(self):
        config = await self.cog.store.get_config(self.guild.id)
        if config["mode"] != "enforce" or not config["setup_complete"]:
            return config, None, None, ActionResult("skipped", "현재 적용 모드가 아님", "mode_changed")
        member = await self.guild.fetch_member(self.message.author.id)
        me = await self.guild.fetch_member(self.cog.bot.user.id)
        if {self.message.channel.id, getattr(self.message.channel, "parent_id", None)} & set(config["exempt_channel_ids"]):
            return config, member, me, ActionResult("skipped", "현재 예외 채널", "exempt")
        if {r.id for r in member.roles} & set(config["exempt_role_ids"]):
            return config, member, me, ActionResult("skipped", "현재 예외 역할", "exempt")
        return config, member, me, None

    async def preflight(self, incident_id: str, *, is_repeat: bool) -> ActionResult:
        try:
            config, member, me, stop = await self._current_target()
            if stop:
                return stop
            fresh = await self.message.channel.fetch_message(self.message.id)
            if message_fingerprint(fresh) != self.fingerprint:
                return ActionResult("skipped", "검사 후 메시지 본문·첨부가 변경됨", "stale_message")
            permissions = self.message.channel.permissions_for(me)
            if not (permissions.view_channel and permissions.read_message_history and permissions.manage_messages):
                return ActionResult("failed", "채널 보기·기록 보기·메시지 관리 권한 필요", "permission")
            if is_repeat:
                if member.id == self.guild.owner_id or member.guild_permissions.administrator:
                    return ActionResult("skipped", "소유자·Administrator는 타임아웃할 수 없음", "protected_member")
                if not me.guild_permissions.moderate_members or me.top_role <= member.top_role:
                    return ActionResult("failed", "Moderate Members 권한 또는 역할 계층 부족", "permission")
            # Missing/private log configuration is diagnosed before destructive work.
            log = await self.guild.fetch_channel(config["log_channel_id"])
            private, reason = await private_log_proof(log, config, me.id)
            if not private:
                return ActionResult("failed", reason, "log_privacy")
            log_perms = log.permissions_for(me)
            if not (log_perms.view_channel and log_perms.send_messages and log_perms.embed_links and log_perms.read_message_history):
                return ActionResult("failed", "운영 로그 전송·기록 조회 권한 부족", "permission")
            return ActionResult("succeeded", "현재 메시지·예외·조치 권한 확인", "ready")
        except discord.NotFound:
            return ActionResult("skipped", "메시지·멤버·로그 채널을 찾을 수 없음", "not_found")
        except discord.Forbidden:
            return ActionResult("failed", "현재 정보 조회 권한 부족", "permission")
        except discord.HTTPException as exc:
            return ActionResult("failed", f"권한 사전 점검 HTTP {exc.status}", "preflight_http")

    async def delete(self, incident_id: str) -> ActionResult:
        try:
            _, _, me, stop = await self._current_target()
            if stop:
                return stop
            fresh = await self.message.channel.fetch_message(self.message.id)
            if message_fingerprint(fresh) != self.fingerprint:
                return ActionResult("skipped", "삭제 직전 메시지가 변경되어 이전 결과 폐기", "stale_message")
            if not self.message.channel.permissions_for(me).manage_messages:
                return ActionResult("failed", "Manage Messages 권한 부족", "permission")
            await fresh.delete()
            return ActionResult("succeeded", "원본 메시지 전체 삭제 완료", "deleted")
        except discord.NotFound:
            return ActionResult("skipped", "이미 삭제되었거나 메시지를 조회할 수 없음", "not_found")
        except discord.Forbidden:
            return ActionResult("failed", "메시지 삭제 권한 거부", "permission")
        except discord.HTTPException as exc:
            return ActionResult("unknown" if exc.status >= 500 else "failed", f"삭제 HTTP {exc.status}", "delete_http")

    async def timeout(self, until: datetime, incident_id: str) -> ActionResult:
        try:
            _, member, me, stop = await self._current_target()
            if stop:
                return stop
            if member.id == self.guild.owner_id or member.guild_permissions.administrator:
                return ActionResult("skipped", "서버 소유자 또는 Administrator", "protected_member")
            if not me.guild_permissions.moderate_members or me.top_role <= member.top_role:
                return ActionResult("failed", "Moderate Members 권한 또는 역할 계층 부족", "permission")
            if not isinstance(until, datetime) or until.tzinfo is None:
                return ActionResult("failed", "저장된 UTC 종료 시각이 필요함", "invalid_duration")
            now = datetime.now(UTC)
            if until <= now:
                return ActionResult("skipped", "최초 처벌 계획의 종료 시각이 이미 지남", "deadline_expired")
            if until > now + timedelta(days=28):
                return ActionResult("failed", "허용 타임아웃 기간 초과", "invalid_duration")
            existing = member.timed_out_until
            # Fetched Discord state is authoritative; do not add duration to it.
            if existing is not None and existing >= until:
                return ActionResult("skipped", "더 긴 기존 타임아웃을 유지", "existing_longer")
            await member.timeout(until, reason=f"AutoMod incident {incident_id}")
            return ActionResult("succeeded", f"최초 계획에 저장된 종료 시각 {until.isoformat()}", "applied")
        except discord.NotFound:
            return ActionResult("skipped", "대상 멤버가 서버에 없음", "not_found")
        except discord.Forbidden:
            return ActionResult("failed", "타임아웃 권한 거부", "permission")
        except discord.HTTPException as exc:
            return ActionResult("unknown" if exc.status >= 500 else "failed", f"타임아웃 HTTP {exc.status}", "timeout_http")

    async def notify(self, kind: str, incident_id: str, count: int, cooldown: int, *, policy: dict) -> ActionResult:
        if not self.cog.reserve_notice(self.guild.id, self.message.author.id, kind, cooldown):
            return ActionResult("skipped", "알림 쿨다운(검사·집계는 계속됨)", "cooldown")
        duration = duration_label(policy["timeout_seconds"])
        limit = policy["warning_limit"]
        if count <= limit:
            future = f"다음 위반부터 {duration} 타임아웃됩니다." if count == limit else f"누적 {limit + 1}회째부터 {duration} 타임아웃됩니다."
            text = f"금지 표현이 포함된 메시지가 삭제되었습니다. 현재 {count}회 경고이며, {future}"
        elif policy.get("timeout_code") == "existing_longer":
            text = f"금지 표현이 포함된 메시지가 삭제되었습니다. 누적 위반 {count}회이며 더 긴 기존 타임아웃을 유지했습니다."
        else:
            text = f"금지 표현이 포함된 메시지가 삭제되었습니다. 누적 위반 {count}회이며 {duration} 타임아웃이 적용되었습니다."
        text += f"\n사건 ID: {incident_id}. 이의가 있으면 운영자에게 알려 주세요."
        try:
            if kind == "dm":
                recipient = self.message.author
                if not callable(getattr(recipient, "send", None)):
                    # After our own successful deletion, recovery has only a
                    # saved author ID. Resolve a real DM-capable User rather
                    # than treating the minimal placeholder as a Member.
                    lookup = getattr(self.cog.bot, "get_user", None)
                    recipient = lookup(recipient.id) if callable(lookup) else None
                    if recipient is None:
                        recipient = await self.cog.bot.fetch_user(self.message.author.id)
                await recipient.send(text, allowed_mentions=NO_MENTIONS)
            else:
                # Only a DM failure reaches this path. No user mention or raw phrase.
                short = f"규칙 위반 메시지가 삭제되었습니다. DM 안내를 전달하지 못했습니다. 사건 ID: {incident_id} · 운영자에게 문의해 주세요."
                sent = await self.message.channel.send(short, allowed_mentions=NO_MENTIONS)
                self.cog.schedule_notice_delete(sent, int(policy.get("public_delete_after", 15)))
            return ActionResult("succeeded", "안내 전달 완료", "sent")
        except discord.NotFound:
            return ActionResult("failed", "안내 대상 사용자 또는 채널이 없음", "not_found")
        except discord.Forbidden:
            return ActionResult("failed", "DM 차단 또는 안내 전송 권한 부족", "dm_forbidden" if kind == "dm" else "permission")
        except discord.HTTPException as exc:
            return ActionResult("unknown" if exc.status >= 500 else "failed", f"안내 HTTP {exc.status}", "notice_http")

    async def _log_destination(self, incident: dict):
        config = await self.cog.store.get_config(self.guild.id)
        self.cog._log_channels[self.guild.id] = config["log_channel_id"]
        records = await self.cog.store.get_actions(incident["incident_id"])
        prior = next((item for item in records if item["kind"] == "log"), {})
        channel_id = prior.get("discord_channel_id") or incident.get("discord_log_channel_id") or config["log_channel_id"]
        message_id = prior.get("discord_message_id") or incident.get("discord_log_message_id")
        if not channel_id:
            return config, None, None, ActionResult("failed", "운영 로그 채널 미설정", "no_log")
        channel = await self.guild.fetch_channel(int(channel_id))
        private, reason = await private_log_proof(channel, config, self.cog.bot.user.id)
        if not private:
            await self.cog.privacy_alert(self.guild.id, int(channel_id))
            return config, None, None, ActionResult("failed", reason, "log_privacy")
        me = await self.guild.fetch_member(self.cog.bot.user.id)
        permissions = channel.permissions_for(me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links and permissions.read_message_history):
            return config, None, None, ActionResult("failed", "로그 보기·전송·기록·임베드 권한 부족", "permission")
        return config, channel, int(message_id) if message_id else None, None

    @staticmethod
    def _is_case_log(message, bot_id: int, case_id: str) -> bool:
        marker = f"사건 ID: `{case_id}`"
        return (message.author.id == bot_id and any(marker in (embed.description or "") for embed in message.embeds))

    async def resolve_log(self, incident: dict) -> ActionResult:
        """Read only. A lost send response never causes an unverified resend."""
        try:
            _, channel, message_id, stop = await self._log_destination(incident)
            if stop:
                return stop
            if message_id:
                message = await channel.fetch_message(message_id)
                if self._is_case_log(message, self.cog.bot.user.id, incident["incident_id"]):
                    return ActionResult("succeeded", "기존 사건 로그 확인", "found", message_id=message.id, channel_id=channel.id)
                return ActionResult("unknown", "저장된 로그 ID의 작성자·사건이 일치하지 않음", "log_identity")
            created = incident.get("created_at")
            after = datetime.fromisoformat(created) - timedelta(seconds=2) if isinstance(created, str) else created
            async for message in channel.history(limit=1000, after=after, oldest_first=False):
                if self._is_case_log(message, self.cog.bot.user.id, incident["incident_id"]):
                    return ActionResult("succeeded", "응답이 유실된 기존 사건 로그 발견", "found", message_id=message.id, channel_id=channel.id)
            return ActionResult("unknown", "제한된 기록 조회에서 전송 결과를 확인하지 못함; 자동 재전송 보류", "unresolved_log")
        except discord.NotFound:
            return ActionResult("failed", "저장된 로그 또는 채널이 없음", "not_found")
        except discord.Forbidden:
            return ActionResult("failed", "로그 기록 조회 권한 부족", "permission")
        except discord.HTTPException as exc:
            return ActionResult("unknown", f"기존 로그 확인 HTTP {exc.status}", "log_lookup_http")

    async def log(self, incident: dict, findings: list, actions: dict, *, retry: bool = False) -> ActionResult:
        try:
            config, channel, message_id, stop = await self._log_destination(incident)
            if stop:
                return stop
            def field(item, key, default=""):
                return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
            mode = incident.get("mode", "observe")
            embed = discord.Embed(title=f"자동관리 사건 · {MODES.get(mode, mode)}",
                                  colour=0x9C6333, description=f"사건 ID: `{incident['incident_id']}`",
                                  timestamp=datetime.now(UTC))
            name = incident.get("user_name") or "보관 만료 또는 미기록"
            embed.add_field(name="사용자", value=f"{safe_text(name, 100)}\nID {incident['user_id']}", inline=False)
            embed.add_field(name="발생 채널 / 메시지", value=f"{incident['channel_id']} / {incident['message_id']}", inline=False)
            occurred = shown_time(incident.get("message_created_at") or incident.get("created_at"), config["display_timezone"])
            if incident.get("message_edited_at"):
                occurred += "\n수정: " + shown_time(incident["message_edited_at"], config["display_timezone"])
            embed.add_field(name="발생 시각", value=occurred, inline=False)
            sources = sorted({field(f, "source", "text") for f in findings})
            embed.add_field(name="검사 종류", value=", ".join(sources) or "검사 진단 / 발췌 만료", inline=False)
            settings = incident.get("plan") or incident.get("settings") or config
            current_count = await self.cog.store.count_violations(self.guild.id, incident["user_id"])
            count_snapshot = settings.get("count", incident.get("count", 0))
            status = incident.get("state", "review")
            embed.add_field(name="횟수 / 확정 당시 정책", value=(
                f"현재 유효 누적 {current_count}회 / 사건 확정 당시 {count_snapshot}회\n"
                f"경고 허용 {settings.get('warning_limit', config['warning_limit'])}회 / "
                f"고정 {duration_label(settings.get('timeout_seconds', config['timeout_seconds']))}\n"
                f"설정 버전 {settings.get('config_version', incident.get('config_version', 0))} / 사건 상태 {status}"
            ), inline=False)
            details = "\n".join(f"{kind}: {field(result, 'status')} / {field(result, 'code')} / {field(result, 'detail')}" for kind, result in actions.items())
            embed.add_field(name="실제 조치 결과", value=safe_text(details, 1024) or "기록만 수행", inline=False)
            if not incident.get("evidence_purged", False):
                embed.add_field(name="필요한 최소 발췌", value=safe_text(incident.get("excerpt", ""), 600) or "없음", inline=False)
                reasons = "\n".join(f"{field(f, 'rule_id')} / {field(f, 'category')} / {field(f, 'source', 'text')} / {field(f, 'confidence')}: {field(f, 'reason')}" for f in findings[:6])
                notes = incident.get("inspection_notes", [])
                if notes:
                    reasons += "\n" + ("\n".join(notes) if isinstance(notes, (list, tuple)) else str(notes))
                embed.add_field(name="판정·검사 근거", value=safe_text(reasons, 1024) or "기술 진단", inline=False)
            else:
                embed.add_field(name="증거 보관", value="발췌 보관 기한 만료 · 누적 횟수와 사건 ID는 유지", inline=False)
            embed.add_field(name="Firebase / Discord 이중 기록", value=(
                "Firestore 사건·처벌 계획: 저장 확인\n"
                "Discord 로그: 이 메시지로 연결됨\n"
                "전송 응답 이후 메시지 ID·성공 결과를 Firestore에 별도 기록합니다. "
                "후속 저장의 불확실 상태는 /자동관리 저장상태 또는 /위반 사건에서 확인하세요."
            ), inline=False)
            embed.set_footer(text="원본 이미지 재게시 없음 · 확정 처벌 계획을 재계산하지 않음 · OCR 점수는 정확도 확률이 아님")
            if message_id:
                prior = await channel.fetch_message(message_id)
                if not self._is_case_log(prior, self.cog.bot.user.id, incident["incident_id"]):
                    return ActionResult("unknown", "기존 로그의 작성자·사건 ID 불일치", "log_identity")
                await prior.edit(content=None, embed=embed, allowed_mentions=NO_MENTIONS, attachments=[])
                sent = prior
                code = "updated"
            else:
                sent = await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
                code = "sent"
            return ActionResult("succeeded", "비공개 운영 로그 기록 완료", code, message_id=sent.id, channel_id=channel.id)
        except discord.NotFound:
            return ActionResult("failed", "운영 로그 채널 또는 기존 로그가 삭제됨", "not_found")
        except discord.Forbidden:
            return ActionResult("failed", "운영 로그 전송·수정 권한 거부", "permission")
        except discord.HTTPException as exc:
            return ActionResult("unknown" if exc.status >= 500 else "failed", f"로그 HTTP {exc.status}", "log_http")

    async def alert(self, error: str) -> None:
        await self.cog.operator_alert(self.guild.id, error)



# ===========================================================================
# Discord Cog / 관리자 슬래시 명령 / 확장 setup
# ===========================================================================

"""Add-on discord.py 2.x Cog. No token/main.py replacement or command redispatch."""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


LOGGER = logging.getLogger(__name__)
CATEGORY_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in CATEGORIES.items()]


async def admin_check(interaction: discord.Interaction) -> bool:
    cog = interaction.client.get_cog('AutoMod')
    if cog is None:
        await respond(interaction, '자동관리 확장이 로드되지 않았습니다.')
        return False
    return await cog.authorize(interaction)


class AdminGroup(app_commands.Group):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await admin_check(interaction)


@dataclass(frozen=True)
class ImageJob:
    message: discord.Message
    generation: int
    queue_key: tuple | None = None

    def __post_init__(self):
        if self.queue_key is None:
            object.__setattr__(self, 'queue_key', (self.message.guild.id, self.message.id,
                                                 message_fingerprint(self.message), self.generation))


class AutoMod(commands.Cog):
    automatic = AdminGroup(name='자동관리', description='자동 검열과 처벌 설정', guild_only=True,
                           default_permissions=None)
    banned = AdminGroup(name='금칙어', description='서버 감지 규칙 관리', guild_only=True,
                       default_permissions=None)
    allowed = AdminGroup(name='허용어', description='해당 구간만 검사에서 제외', guild_only=True,
                        default_permissions=None)
    exceptions = AdminGroup(name='예외', description='검사 제외 채널과 역할', guild_only=True,
                           default_permissions=None)
    violations = AdminGroup(name='위반', description='누적 위반과 사건 조회·취소·초기화', guild_only=True,
                           default_permissions=None)

    def __init__(self, bot: commands.Bot, *, store: Store | None = None,
                 runtime: FirebaseRuntime | None = None, rules_path: Path | None = RULES_PATH, ocr_pool=None):
        self.bot = bot
        self.rules_path = None if rules_path is None else Path(rules_path)
        self.store = store
        self.runtime = runtime
        self.service = ModerationService(self.store, self.rules_path)
        self.ocr = ocr_pool or OCRPool(MODELS_PATH, max_queue=8, timeout_seconds=30)
        self._http: aiohttp.ClientSession | None = None
        self._inspector: AttachmentInspector | None = None
        self._image_jobs: asyncio.Queue[ImageJob] = asyncio.Queue(maxsize=32)
        self._queued_versions: set[tuple[int, int, str, int]] = set()
        self._background: set[asyncio.Task] = set()
        self._active: set[asyncio.Task] = set()
        self._notice_tasks: set[asyncio.Task] = set()
        self._closing = False
        self._notices: OrderedDict[tuple[int, int, str], float] = OrderedDict()
        self._alert_times: dict[tuple[int, str], float] = {}
        self._log_channels: dict[int, int | None] = {}
        # Only message IDs and arrival times; never an offline violation counter.
        self._deferred: OrderedDict[tuple[int, int], dict] = OrderedDict()
        self._last_retention = 0.0
        self._recovering = asyncio.Lock()

    def _task(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background_done)
        return task

    def _background_done(self, task: asyncio.Task) -> None:
        self._background.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None and not self._closing:
                self.service.halted = True
                LOGGER.critical('AutoMod background task stopped: %s (%s)', task.get_name(), type(error).__name__)

    async def cog_load(self) -> None:
        if not (self.bot.intents.guilds and self.bot.intents.guild_messages and self.bot.intents.message_content):
            raise RuntimeError('AutoMod에는 guilds/guild_messages/message_content Intent가 필요합니다.')
        Engine.from_file(self.rules_path)
        try:
            if self.store is None:
                self.runtime = self.runtime or await FirebaseRuntime.from_env()
                self.store = Store(self.runtime.firestore, runtime=self.runtime)
                self.service = ModerationService(self.store, self.rules_path)
            try:
                await self.store.open()
            except StorageUnavailable:
                self.service.halted = True
                LOGGER.error('AutoMod Firestore unavailable at startup; sanctions held, recovery loop active')
            self._http = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=4), trust_env=False)
            self._inspector = AttachmentInspector(self._http, self.ocr)
            self._task(self._start_ocr(), 'automod-ocr-start')
            self._task(self._housekeeping(), 'automod-retention')
            for index in range(2):
                self._task(self._image_worker(), f'automod-images-{index}')
        except BaseException:
            await self.cog_unload()
            raise

    async def _start_ocr(self) -> None:
        try:
            ready = await self.ocr.start()
            if not ready:
                LOGGER.error('AutoMod OCR not ready: %s', self.ocr.last_error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error('AutoMod OCR startup failed: %s', type(exc).__name__)

    async def cog_unload(self) -> None:
        self._closing = True
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        active = [t for t in self._active if t is not asyncio.current_task()]
        if active:
            _, pending = await asyncio.wait(active, timeout=15)
            for task in pending:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
        notices = list(self._notice_tasks)
        for task in notices:
            task.cancel()
        await asyncio.gather(*notices, return_exceptions=True)
        try:
            await self.ocr.close()
        except Exception as exc:
            LOGGER.error('AutoMod OCR shutdown failed: %s', type(exc).__name__)
        finally:
            try:
                if self._http is not None:
                    await self._http.close()
            finally:
                if self.store is not None:
                    await self.store.close()
                if self.runtime is not None:
                    await self.runtime.close()
        self._queued_versions.clear()
        self._deferred.clear()
        while not self._image_jobs.empty():
            self._image_jobs.get_nowait()
            self._image_jobs.task_done()

    async def _housekeeping(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                if not self.store.healthy:
                    await self.store.recover()
                    self.service.resume_after_reconnect()
                await self._recover_work()
                if time.monotonic() - self._last_retention > 3600:
                    await self._retention_pass()
                    self._last_retention = time.monotonic()
            except StorageUnavailable:
                self.service.halted = True
                for guild in self.bot.guilds:
                    await self.operator_alert(guild.id, 'Firestore 연결 장애; 새 자동 처벌을 보류하고 재연결을 기다립니다')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.service.halted = True
                LOGGER.error('AutoMod recovery failure: %s', type(exc).__name__)
            await asyncio.sleep(60)

    def _defer_message(self, message, generation: int | None = None) -> None:
        key = (message.guild.id, message.id)
        self._deferred.setdefault(key, {'guild_id': message.guild.id, 'channel_id': message.channel.id,
                                       'message_id': message.id, 'user_id': message.author.id,
                                       'generation': generation, 'arrived_at': datetime.now(timezone.utc)})
        while len(self._deferred) > 256:
            self._deferred.popitem(last=False)
            LOGGER.warning('AutoMod offline ID queue full; oldest uncommitted event dropped without sanction')

    async def _case_adapter(self, case: dict, *, logs_only: bool = False):
        guild = self.bot.get_guild(int(case['guild_id']))
        if guild is None:
            return None
        if logs_only:
            message = SimpleNamespace(id=int(case['message_id']), guild=guild,
                channel=SimpleNamespace(id=int(case['channel_id']), parent_id=None),
                author=SimpleNamespace(id=int(case['user_id']), name='기록 사용자'),
                content='', attachments=[])
            return DiscordAdapter(self, message)
        try:
            channel = guild.get_channel_or_thread(int(case['channel_id'])) or await guild.fetch_channel(int(case['channel_id']))
        except (discord.NotFound, discord.Forbidden) as exc:
            channel = UnavailableMessageChannel(int(case['channel_id']), exc)
        try:
            message = await channel.fetch_message(int(case['message_id']))
        except (discord.NotFound, discord.Forbidden):
            # Deleted content is never guessed. A shell permits only already
            # prepared non-content phases; delete will still fetch and fail closed.
            author = SimpleNamespace(id=int(case['user_id']), name='기록 사용자')
            message = SimpleNamespace(id=int(case['message_id']), guild=guild, channel=channel,
                                      author=author, content='', attachments=[], created_at=None, edited_at=None)
        adapter = DiscordAdapter(self, message)
        adapter.bind_case(case)
        return adapter

    async def _recover_work(self) -> None:
        if self._recovering.locked():
            return
        async with self._recovering:
            for guild in self.bot.guilds:
                for case in await self.store.list_pending_cases(guild.id, limit=25):
                    try:
                        adapter = await self._case_adapter(case)
                        if adapter is not None:
                            await self.service.recover_case(guild.id, case['incident_id'], adapter)
                    except discord.HTTPException as exc:
                        LOGGER.warning('AutoMod pending case lookup failed: %s', type(exc).__name__)
                for case in await self.store.list_pending_logs(guild.id, limit=25):
                    try:
                        adapter = await self._case_adapter(case, logs_only=True)
                        if adapter is not None:
                            await self.service.retry_log(guild.id, case['incident_id'], adapter)
                    except discord.HTTPException as exc:
                        LOGGER.warning('AutoMod pending log lookup failed: %s', type(exc).__name__)
            for key, job in list(self._deferred.items())[:25]:
                guild = self.bot.get_guild(job['guild_id'])
                if guild is None:
                    self._deferred.pop(key, None)
                    continue
                member_state = await self.store.get_member_state(guild.id, job['user_id'])
                reset_at = member_state.get('reset_at')
                if isinstance(reset_at, str):
                    reset_at = datetime.fromisoformat(reset_at)
                if ((job['generation'] is not None and job['generation'] != member_state['generation'])
                        or (reset_at and reset_at >= job['arrived_at'])):
                    self._deferred.pop(key, None)
                    continue
                try:
                    channel = guild.get_channel_or_thread(job['channel_id']) or await guild.fetch_channel(job['channel_id'])
                    message = await channel.fetch_message(job['message_id'])
                except (discord.NotFound, discord.Forbidden):
                    self._deferred.pop(key, None)
                    continue
                self._deferred.pop(key, None)
                await self._handle_message(message, expected_generation=member_state['generation'])

    async def _retention_pass(self, guild_id: int | None = None) -> int:
        for case in await self.store.due_retention(guild_id=guild_id, limit=100):
            for original in case.get('storage_objects', []):
                if original.get('status') in {'succeeded', 'skipped'}:
                    continue
                path = original['object_path']
                try:
                    if self.runtime is None:
                        raise RuntimeError('Storage runtime unavailable')
                    generation = original.get('generation')
                    if generation is None:
                        metadata = await self.runtime.lookup_original(path, original['sha256'])
                        generation = metadata['generation'] if metadata else None
                    ok = generation is None or await self.runtime.delete_original(path, generation)
                    status, detail = ('succeeded', '삭제 또는 이미 없음') if ok else ('failed', 'Storage 세대가 달라 삭제하지 않음')
                except Exception as exc:
                    status, detail = 'failed', f'Storage 정리 {type(exc).__name__}'
                await self.store.record_retention_result(case['incident_id'], kind='storage', reference=path, status=status, detail=detail)
            for item in case.get('log_messages', []):
                if item.get('status') in {'succeeded', 'skipped'}:
                    continue
                channel_id, message_id = int(item['channel_id']), int(item['message_id'])
                try:
                    guild = self.bot.get_guild(int(case['incident_id'].split(':')[0]))
                    if guild is None:
                        raise RuntimeError('Guild unavailable')
                    channel = await guild.fetch_channel(channel_id)
                    message = await channel.fetch_message(message_id)
                    if not DiscordAdapter._is_case_log(message, self.bot.user.id, case['incident_id']):
                        status, detail = 'failed', '봇 작성 사건 로그가 아니므로 수정하지 않음'
                    else:
                        embed = discord.Embed(title='자동관리 · 증거 보관 기한 만료',
                                              description=f"사건 ID: `{case['incident_id']}`\n상세 발췌를 삭제했습니다. 누적 위반과 최소 사건 기록은 유지합니다.")
                        # Redaction is allowed even if the channel became public:
                        # it only removes sensitive material and sends no evidence.
                        await message.edit(content=None, embed=embed, attachments=[], allowed_mentions=NO_MENTIONS)
                        status, detail = 'succeeded', '로그 발췌·첨부 제거'
                except discord.NotFound:
                    status, detail = 'succeeded', '로그 또는 채널이 이미 삭제됨'
                except Exception as exc:
                    status, detail = 'failed', f'Discord 정리 {type(exc).__name__}'
                await self.store.record_retention_result(case['incident_id'], kind='log', reference=f'{channel_id}:{message_id}', status=status, detail=detail)
            await self.store.mark_retention_attempt(case['incident_id'])
        return await self.store.purge_expired(guild_id=guild_id)

    def reserve_notice(self, guild_id: int, user_id: int, kind: str, cooldown: int) -> bool:
        key = (guild_id, user_id, kind)
        now = time.monotonic()
        if now - self._notices.get(key, -1e10) < cooldown:
            return False
        self._notices[key] = now
        self._notices.move_to_end(key)
        while len(self._notices) > 8192:
            self._notices.popitem(last=False)
        return True

    def schedule_notice_delete(self, message, seconds: int) -> None:
        if seconds <= 0:
            return

        async def later():
            try:
                await asyncio.sleep(seconds)
            finally:
                try:
                    await asyncio.wait_for(message.delete(), timeout=5)
                except (discord.HTTPException, TimeoutError, OSError):
                    LOGGER.warning('AutoMod public notice deletion failed channel=%s message=%s', message.channel.id, message.id)

        task = asyncio.create_task(later(), name='automod-notice-delete')
        self._notice_tasks.add(task)
        task.add_done_callback(self._notice_tasks.discard)

    def _alert_allowed(self, guild_id: int, kind: str) -> bool:
        key = (guild_id, kind)
        now = time.monotonic()
        if now - self._alert_times.get(key, -1e10) < 300:
            return False
        self._alert_times[key] = now
        if len(self._alert_times) > 4096:
            self._alert_times.pop(next(iter(self._alert_times)))
        return True

    async def privacy_alert(self, guild_id: int, channel_id: int) -> None:
        LOGGER.error('AutoMod sensitive logs suspended: log privacy invalid guild=%s', guild_id)
        if not self._alert_allowed(guild_id, 'privacy'):
            return
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            with contextlib.suppress(discord.HTTPException):
                await channel.send('자동관리 로그 채널의 비공개 권한을 확인할 수 없어 상세 로그와 자동 처벌을 보류했습니다. 관리자가 /자동관리 권한점검을 실행해 주세요.', allowed_mentions=NO_MENTIONS)

    async def operator_alert(self, guild_id: int, error: str) -> None:
        LOGGER.error('AutoMod operator attention guild=%s: %s', guild_id, error[:180])
        if not self._alert_allowed(guild_id, 'operator'):
            return
        channel_id = self._log_channels.get(guild_id)
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is not None:
            with contextlib.suppress(discord.HTTPException):
                await channel.send('자동관리 처리에 문제가 발생했습니다. DB 오류이면 자동 처벌이 중단됩니다. 운영자는 /자동관리 상태와 프로세스 로그를 확인해 주세요.', allowed_mentions=NO_MENTIONS)

    async def authorize(self, interaction: discord.Interaction, *, owner_admin_only: bool = False) -> bool:
        if interaction.guild is None:
            await respond(interaction, '서버 안에서만 사용할 수 있습니다.')
            return False
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
            roles = {role.id: role for role in await interaction.guild.fetch_roles()}
            role_ids = {role.id for role in member.roles}
            admin = member.id == interaction.guild.owner_id or any(roles[r].permissions.administrator for r in role_ids if r in roles)
            if admin:
                return True
            if not owner_admin_only:
                config = await self.store.get_config(interaction.guild_id)
                if role_ids & set(config['operator_role_ids']):
                    return True
            await respond(interaction, '서버 소유자·Administrator 또는 지정 운영 역할만 사용할 수 있습니다.')
        except discord.HTTPException:
            await respond(interaction, '현재 권한을 조회하지 못해 명령을 실행하지 않았습니다.')
        except StorageUnavailable:
            await respond(interaction, 'DB 상태를 확인하지 못해 운영 역할 권한을 확인할 수 없습니다.')
        return False

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        cause = getattr(error, 'original', error)
        if isinstance(cause, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await respond(interaction, '명령 실행 권한이 없습니다.')
            return
        if isinstance(cause, StorageUnavailable):
            self.service.halted = True
            await respond(interaction, 'DB 저장 상태가 불확실해 자동 처벌을 중지했습니다. 성공 여부를 추정하지 말고 프로세스 로그와 DB를 확인하세요.')
            if interaction.guild is not None:
                await self.operator_alert(interaction.guild_id, '관리 명령 중 DB 상태 확인 실패')
        elif isinstance(cause, (ValueError, ZoneInfoNotFoundError)):
            await respond(interaction, f'입력을 확인해 주세요: {safe_text(cause)}')
        else:
            LOGGER.error('AutoMod admin command failed: %s', type(cause).__name__)
            await respond(interaction, f'처리 중 오류({type(cause).__name__}). 성공 여부는 /자동관리 상태에서 확인하세요.')

    @staticmethod
    def _out_of_scope(message) -> bool:
        return message.guild is None or message.author.bot or message.webhook_id is not None

    async def _member_and_config(self, message, *, fresh: bool = False):
        config = await self.store.get_config(message.guild.id)
        member = message.author
        if fresh or not isinstance(member, discord.Member):
            member = await message.guild.fetch_member(member.id)
        excluded = config['mode'] == 'off' or (
            {message.channel.id, getattr(message.channel, 'parent_id', None)} & set(config['exempt_channel_ids'])
        ) or ({r.id for r in member.roles} & set(config['exempt_role_ids']))
        return member, config, bool(excluded)

    async def _handle_message(self, message: discord.Message, *, expected_generation: int | None = None) -> None:
        if self._closing or self._out_of_scope(message) or not isinstance(message.content, str):
            return
        task = asyncio.current_task()
        self._active.add(task)
        try:
            _, config, excluded = await self._member_and_config(message)
            if excluded:
                return
            generation = await self.store.get_user_generation(message.guild.id, message.author.id)
            if expected_generation is not None and generation != expected_generation:
                return
            candidates = any(is_image_candidate(a) for a in message.attachments)
            if candidates and config['image_enabled']:
                key = (message.guild.id, message.id, message_fingerprint(message), generation)
                if key in self._queued_versions:
                    return
                try:
                    self._image_jobs.put_nowait(ImageJob(message, generation, key))
                    self._queued_versions.add(key)
                except asyncio.QueueFull:
                    await self._process_current(message, generation, Inspection(notes=['이미지 대기열 한도 초과: 검토 필요'], blocked=True))
            else:
                await self._process_current(message, generation)
        except discord.HTTPException as exc:
            LOGGER.warning('AutoMod identity read failed: %s', type(exc).__name__)
        except StorageUnavailable:
            self.service.halted = True
            self._defer_message(message)
            await self.operator_alert(message.guild.id, 'Firestore 상태 확인 실패; 메시지 ID만 보류, 새 횟수·처벌 판단 없음')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.service.halted = True
            await self.operator_alert(message.guild.id, f'검사 처리 오류({type(exc).__name__}); 자동 처벌 중지')
        finally:
            self._active.discard(task)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._handle_message(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Only raw edit listener: cached and uncached edits follow the same path.
        # Attachment-only user edits matter; embed-only updates do not.
        if self._closing or payload.guild_id is None or not ({'content', 'attachments'} & payload.data.keys()):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        task = asyncio.current_task()
        self._active.add(task)
        try:
            channel = guild.get_channel_or_thread(payload.channel_id) or await guild.fetch_channel(payload.channel_id)
            if hasattr(channel, 'fetch_message'):
                await self._handle_message(await channel.fetch_message(payload.message_id))
        except discord.HTTPException as exc:
            LOGGER.warning('AutoMod raw edit unavailable guild=%s message=%s: %s', payload.guild_id, payload.message_id, type(exc).__name__)
        finally:
            self._active.discard(task)

    async def _image_worker(self) -> None:
        while True:
            job = await self._image_jobs.get()
            key = job.queue_key
            try:
                await self._run_image_job(job)
            except asyncio.CancelledError:
                raise
            except StorageUnavailable:
                self.service.halted = True
                self._defer_message(job.message, job.generation)
                await self.operator_alert(job.message.guild.id, '이미지 검사 중 DB 오류; 자동 처벌 중지')
            except Exception as exc:
                # OCR failures are diagnostic, never invented violations.
                LOGGER.error('AutoMod image job failure: %s', type(exc).__name__)
                try:
                    current = await job.message.channel.fetch_message(job.message.id)
                    await self._process_current(current, job.generation, Inspection(notes=[f'이미지 검사 실패({type(exc).__name__})'], blocked=True))
                except asyncio.CancelledError:
                    raise
                except StorageUnavailable:
                    self.service.halted = True
                    await self.operator_alert(job.message.guild.id, '이미지 실패 진단 중 DB 오류; 자동 처벌 중지')
                except Exception as fallback_error:
                    # Failure to re-fetch means there is no current content to
                    # punish. Keep this bounded worker alive for later messages.
                    LOGGER.warning('AutoMod image fallback unavailable: %s', type(fallback_error).__name__)
            finally:
                self._queued_versions.discard(key)
                self._image_jobs.task_done()

    async def _run_image_job(self, job: ImageJob) -> None:
        message = job.message
        for attempt in range(2):
            if self._closing:
                return
            if await self.store.get_user_generation(message.guild.id, message.author.id) != job.generation:
                return
            _, config, excluded = await self._member_and_config(message, fresh=True)
            if excluded:
                return
            if not config['image_enabled']:
                current = await message.channel.fetch_message(message.id)
                await self._process_current(current, job.generation)
                return
            # Cached discord.Message objects can mutate while OCR awaits. Freeze
            # the actual analyzed attachment revision before yielding control.
            analyzed_signature = attachment_signature(message)
            inspection = await self._inspector.inspect(message)
            try:
                current = await message.channel.fetch_message(message.id)
            except (discord.NotFound, discord.Forbidden):
                return  # current state unknown; no stale-image enforcement
            if attachment_signature(current) != analyzed_signature:
                message = current
                continue  # old images removed/replaced; bounded reanalysis
            await self._process_current(current, job.generation, inspection)
            return
        await self._process_current(message, job.generation,
                                    Inspection(notes=['분석 중 첨부가 반복 변경됨: 옛 결과 폐기, 검토 필요'], blocked=True))

    async def _process_current(self, message, generation: int, inspection: Inspection | None = None):
        if self._closing or self._out_of_scope(message):
            return None
        member, config, excluded = await self._member_and_config(message, fresh=inspection is not None)
        if excluded or await self.store.get_user_generation(message.guild.id, member.id) != generation:
            return None
        self._log_channels[message.guild.id] = config['log_channel_id']
        inputs = []
        if inspection is not None and config['image_enabled']:
            for image in inspection.images:
                if image.result.lines:
                    inputs.append({'text': '\n'.join(line.text for line in image.result.lines),
                                   'confidence': min(line.confidence for line in image.result.lines),
                                   'status': image.result.status, 'attachment_id': image.attachment_id,
                                   'sha256': image.result.sha256})
        event = MessageEvent(
            guild_id=message.guild.id, channel_id=message.channel.id, message_id=message.id,
            user_id=member.id, user_name=str(member), content=message.content,
            created_at=message.created_at, edited_at=message.edited_at,
            role_ids=tuple(r.id for r in member.roles), parent_channel_id=getattr(message.channel, 'parent_id', None),
            expected_generation=generation, ocr_inputs=tuple(inputs),
            content_revision_hash=inspection.revision_hash(message) if inspection and config['image_enabled'] else message_fingerprint(message),
            inspection_notes=tuple(inspection.notes) if inspection and config['image_enabled'] else (),
            enforcement_blocked=bool(inspection and inspection.blocked and config['image_enabled']),
            message_signature=message_fingerprint(message),
            attachment_metadata=tuple({'attachment_id': str(image.attachment_id),
                'sha256': image.result.sha256, 'status': image.result.status}
                for image in inspection.images) if inspection and config['image_enabled'] else (),
        )
        result = await self.service.process(event, DiscordAdapter(self, message))
        if result.retryable:
            self._defer_message(message, generation)
        if result.violation_added and inspection is not None:
            # Save only on explicit opt-in. Byte buffers otherwise die with this job.
            for image in inspection.images:
                if image.result.status not in {'ok', 'no_text'} or not image.data or len(image.result.sha256) != 64:
                    continue
                try:
                    await self.store.save_original(message.guild.id, result.incident_id, image.attachment_id,
                                                   image.data, image.result.sha256)
                except FirebaseStorageError as exc:
                    await self.operator_alert(message.guild.id, f'선택적 이미지 보관 실패({exc.code}); 처벌 재실행 없음')
        return result

    async def allows_command(self, message: discord.Message) -> bool:
        """Optional Bot.check hook for TEXT only; image OCR cannot gate other listeners."""
        if self._out_of_scope(message) or self.service.halted:
            return True
        try:
            _, config, excluded = await self._member_and_config(message)
            if excluded or config['mode'] != 'enforce':
                return True
            preview = await self.service.test_text(message.guild.id, message.content)
            return not any(f.confidence == 'clear' and f.recommendation == 'enforce' for f in preview.findings)
        except (StorageUnavailable, discord.HTTPException):
            return True

    async def audit_notice(self, guild, actor_id: int, action: str, detail: str) -> bool:
        config = await self.store.get_config(guild.id)
        self._log_channels[guild.id] = config['log_channel_id']
        if not config['log_channel_id']:
            return False
        try:
            channel = await guild.fetch_channel(config['log_channel_id'])
            private, _ = await private_log_proof(channel, config, self.bot.user.id)
            if not private:
                await self.privacy_alert(guild.id, config['log_channel_id'])
                return False
            await channel.send(f'관리 변경 · {safe_text(action, 100)}\n담당자 ID: {actor_id}\n{safe_text(detail, 1200)}', allowed_mentions=NO_MENTIONS)
            return True
        except discord.HTTPException:
            LOGGER.warning('AutoMod admin audit channel delivery failed guild=%s', guild.id)
            return False

    async def _change(self, interaction: discord.Interaction, **changes) -> dict:
        async with self.service.policy_lock(interaction.guild_id):
            config = await self.store.update_config(interaction.guild_id, actor_id=interaction.user.id,
                                                    reason='관리자 슬래시 명령', **changes)
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '설정 변경', json.dumps(changes, ensure_ascii=False))
        return config

    async def permission_report(self, guild, config: dict, channel=None) -> tuple[bool, str]:
        lines = []
        good = True
        try:
            me = await guild.fetch_member(self.bot.user.id)
            roles = {r.id: r for r in await guild.fetch_roles()}
            values = 0
            for role in me.roles:
                if role.id in roles:
                    values |= roles[role.id].permissions.value
            permissions = discord.Permissions(values)
            for label, present in [('Manage Messages', permissions.manage_messages or permissions.administrator),
                                   ('Moderate Members', permissions.moderate_members or permissions.administrator)]:
                lines.append(f'{label}: {"정상" if present else "필요"}')
                good = good and present
            if channel is None and config['log_channel_id']:
                channel = await guild.fetch_channel(config['log_channel_id'])
            if channel is None:
                lines.append('운영 로그 채널: 미설정')
                good = False
            else:
                private, reason = await private_log_proof(channel, config, me.id)
                lines.append('로그 비공개: ' + reason)
                perms = channel.permissions_for(me)
                log_ok = perms.view_channel and perms.send_messages and perms.read_message_history and perms.embed_links
                lines.append('로그 보기/전송/기록/임베드: ' + ('정상' if log_ok else '권한 필요'))
                good = bool(good and private and log_ok)
            lines.append('OCR: ' + ('준비됨(CPU ko+en)' if self.ocr.ready else f'준비 안 됨 · {self.ocr.last_error}'))
            good = bool(good and self.ocr.ready)
            lines.append('일반 채널·스레드의 개별 권한과 대상 역할 계층은 사건 처리 직전에 다시 검사합니다.')
        except discord.HTTPException as exc:
            lines.append(f'권한 조회 실패({type(exc).__name__})')
            good = False
        return good, '\n'.join(lines)

    async def configure(self, interaction, channel, role) -> None:
        if role.is_default():
            raise ValueError('@everyone을 운영 역할로 지정할 수 없습니다.')
        config = await self.store.get_config(interaction.guild_id)
        config.update(log_channel_id=channel.id, operator_role_ids=[role.id])
        fresh = await interaction.guild.fetch_channel(channel.id)
        good, report = await self.permission_report(interaction.guild, config, fresh)
        if not good:
            await respond(interaction, '적용 모드를 시작하지 않았습니다. 아래 항목을 해결하고 다시 설정하세요.\n' + report)
            return
        await self._change(interaction, log_channel_id=channel.id, operator_role_ids=[role.id],
                           setup_complete=True, mode='enforce')
        await respond(interaction, '설정을 저장하고 적용 모드를 시작했습니다. 기본은 첫 위반 경고, 두 번째부터 매번 고정 1시간입니다. 기존에 변경한 처벌 설정은 유지됩니다.\n' + report)

    @automatic.command(name='로그생성', description='비공개 운영 채널 생성 후 검사 설정을 저장합니다')
    async def create_log(self, interaction: discord.Interaction, 운영역할: discord.Role, 이름: str = '관리로그') -> None:
        if not await self.authorize(interaction, owner_admin_only=True):
            return
        if 운영역할.is_default():
            raise ValueError('@everyone을 운영 역할로 지정할 수 없습니다.')
        if not 1 <= len(이름) <= 100:
            raise ValueError('채널 이름은 1~100자여야 합니다.')
        me = await interaction.guild.fetch_member(self.bot.user.id)
        if not me.guild_permissions.manage_channels:
            raise ValueError('로그생성에는 봇의 Manage Channels 권한이 필요합니다. 기존 채널은 /자동관리 설정으로 지정할 수 있습니다.')
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            운영역할: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True),
            me: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, embed_links=True),
        }
        channel = await interaction.guild.create_text_channel(이름, overwrites=overwrites, category=None,
                                                             reason=f'AutoMod setup by {interaction.user.id}')
        await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'create_log_channel', str(channel.id))
        await self.configure(interaction, channel, 운영역할)

    @automatic.command(name='설정', description='기존 비공개 로그와 운영 역할 확인 후 적용 모드로 시작합니다')
    async def configure_existing(self, interaction: discord.Interaction, 로그채널: discord.TextChannel, 운영역할: discord.Role) -> None:
        if await self.authorize(interaction, owner_admin_only=True):
            await self.configure(interaction, 로그채널, 운영역할)

    @automatic.command(name='상태', description='설정·누적 정책·OCR·DB 상태를 확인합니다')
    async def status(self, interaction: discord.Interaction) -> None:
        if not self.store.healthy or self.service.halted:
            await respond(interaction, 'Firestore/처리 오류 보호 상태입니다. 새 자동 처벌을 보류했습니다. /자동관리 저장상태와 연결점검으로 확인하세요. DB 장애는 60초 간격으로 재연결합니다.')
            return
        config = await self.store.get_config(interaction.guild_id)
        lines = [f'모드: {MODES[config["mode"]]} / 최초 설정 완료: {config["setup_complete"]}',
                 f'정책: 경고 {config["warning_limit"]}회, 이후 매번 고정 {duration_label(config["timeout_seconds"])}',
                 '누적 위반은 자동 초기화하지 않습니다. 증거 만료와 누적 집계는 별개입니다.',
                 f'이미지 검사: {config["image_enabled"]} / OCR 준비: {self.ocr.ready} / 메시지 대기 {self._image_jobs.qsize()}/32',
                 f'로그 ID: {config["log_channel_id"]} / 운영 역할 ID: {config["operator_role_ids"]}',
                 f'예외 채널 ID: {config["exempt_channel_ids"]} / 예외 역할 ID: {config["exempt_role_ids"]}',
                 f'증거 보관: {config["retention_days"]}일 / 원본 보관: {config["retain_originals"]}',
                 f'DM 실패 시 공개 안내: {config["public_fallback"]} / 삭제 {config["public_delete_after"]}초 / 쿨다운 {config["notice_cooldown_seconds"]}초',
                 f'표시 시간대: {config["display_timezone"]} / OCR 상태: {self.ocr.last_error or "정상"}']
        await respond(interaction, '\n'.join(lines))

    @automatic.command(name='저장상태', description='Firestore·비공개 로그·OCR·대기 및 실패 상태를 확인합니다')
    async def storage_status(self, interaction: discord.Interaction) -> None:
        lines = [f'Firestore 연결 확인: {self.store.healthy} / 자동 처벌 보호 중지: {self.service.halted}',
                 f'OCR 준비: {self.ocr.ready} / 이미지 메시지 대기: {self._image_jobs.qsize()}/32',
                 f'미확정 메시지 ID 보류: {len(self._deferred)}/256 (누적 횟수 캐시 아님)']
        if self.store.healthy:
            config = await self.store.get_config(interaction.guild_id)
            stats = await self.store.storage_status(interaction.guild_id)
            lines.append('Firestore 상태: ' + json.dumps(stats, ensure_ascii=False))
            if config['log_channel_id']:
                try:
                    channel = await interaction.guild.fetch_channel(config['log_channel_id'])
                    private, reason = await private_log_proof(channel, config, self.bot.user.id)
                    lines.append(f'로그 채널 비공개 확인: {private} / {reason}')
                except discord.HTTPException as exc:
                    lines.append(f'로그 채널 조회 실패: {type(exc).__name__}')
            else:
                lines.append('로그 채널: 미설정')
        else:
            lines.append('Firestore의 대기·실패 건수는 조회할 수 없습니다. 로컬 횟수로 처벌하지 않습니다.')
        lines.append('연결 복구 시 저장된 사건·취소·초기화·설정과 현재 메시지를 확인합니다. 불확실한 Discord 조치는 자동 반복하지 않습니다.')
        await respond(interaction, '\n'.join(lines))

    @automatic.command(name='연결점검', description='위반 증가 없이 Firestore·로그·OCR·선택적 Storage를 점검합니다')
    async def connection_check(self, interaction: discord.Interaction) -> None:
        lines = []
        try:
            await self.store.probe()
            self.service.resume_after_reconnect()
            lines.append('Firestore 실제 서버 조회: 성공. 테스트 사건·카운터 변경 없음.')
            config = await self.store.get_config(interaction.guild_id)
            _, report = await self.permission_report(interaction.guild, config)
            lines.append(report)
        except StorageUnavailable:
            self.service.halted = True
            lines.append('Firestore 연결 실패: 새 횟수 기반 처벌을 보류합니다. ADC·IAM·프로젝트·네트워크를 확인하세요.')
        if self.runtime and self.runtime.settings.storage_bucket:
            try:
                proof = await self.runtime.check_storage()
                lines.append('선택적 Storage 비공개 점검: ' + json.dumps(proof, ensure_ascii=False))
            except FirebaseStorageError as exc:
                lines.append(f'Storage 확인 실패: {exc.code}. 원본 보관을 켜지 마세요.')
        else:
            lines.append('원본 Storage 버킷 미설정: OCR과 원본 미보관 운영에는 필요하지 않습니다.')
        lines.append('이 명령 자체는 삭제·통지·타임아웃·횟수 증가를 실행하지 않습니다.')
        await respond(interaction, '\n'.join(lines))

    @automatic.command(name='로그재시도', description='사건 로그만 복구합니다. 위반 증가·타임아웃 재실행 없음')
    async def retry_log(self, interaction: discord.Interaction, 사건: str) -> None:
        case = await self.store.get_incident_by_id(interaction.guild_id, 사건)
        if case is None:
            await respond(interaction, '이 서버의 사건을 찾을 수 없습니다.')
            return
        adapter = await self._case_adapter(case, logs_only=True)
        result = await self.service.retry_log(interaction.guild_id, 사건, adapter)
        await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'retry_log', {'incident_id': 사건})
        await respond(interaction, '로그만 처리했습니다. 삭제·누적 증가·타임아웃은 실행하지 않았습니다.\n' + safe_text(str(result), 1800))

    @automatic.command(name='모드', description='적용·관찰·끄기를 변경합니다')
    async def mode(self, interaction: discord.Interaction, 값: Literal['적용', '관찰', '끄기']) -> None:
        config = await self.store.get_config(interaction.guild_id)
        if 값 == '적용':
            if not config['setup_complete']:
                raise ValueError('먼저 /자동관리 설정 또는 로그생성을 완료하세요.')
            good, report = await self.permission_report(interaction.guild, config)
            if not good:
                await respond(interaction, '적용 모드 전환을 보류했습니다.\n' + report)
                return
        await self._change(interaction, mode={'적용': 'enforce', '관찰': 'observe', '끄기': 'off'}[값])
        await respond(interaction, f'모드: {값}. 과거 사건을 다시 처벌하지 않습니다.')

    @automatic.command(name='권한점검', description='비공개 로그·권한·OCR 준비를 다시 확인합니다')
    async def permissions(self, interaction: discord.Interaction) -> None:
        config = await self.store.get_config(interaction.guild_id)
        good, report = await self.permission_report(interaction.guild, config)
        await respond(interaction, ('필수 점검 통과\n' if good else '설정 보완 필요\n') + report)

    @automatic.command(name='처벌설정', description='경고 허용 횟수와 고정 타임아웃을 변경합니다')
    async def punishment(self, interaction: discord.Interaction,
                         경고횟수: app_commands.Range[int, 0, 100] | None = None,
                         타임아웃: str | None = None) -> None:
        changes = {}
        if 경고횟수 is not None:
            changes['warning_limit'] = 경고횟수
        if 타임아웃 is not None:
            changes['timeout_seconds'] = parse_duration(타임아웃)
        if not changes:
            raise ValueError('경고횟수 또는 타임아웃을 하나 이상 입력하세요.')
        config = await self._change(interaction, **changes)
        await respond(interaction, f'경고 허용 {config["warning_limit"]}회, 그다음부터 매번 고정 {duration_label(config["timeout_seconds"])}. 새로운 사건의 처벌 계획부터 적용됩니다.')

    @automatic.command(name='처벌확인', description='현재 누적 처벌 기준을 확인합니다')
    async def punishment_status(self, interaction: discord.Interaction) -> None:
        config = await self.store.get_config(interaction.guild_id)
        await respond(interaction, f'경고 허용: {config["warning_limit"]}회\n타임아웃 시작: {config["warning_limit"] + 1}회째\n매번 고정: {duration_label(config["timeout_seconds"])}\n자동 횟수 초기화 없음. 남은 시간에 더하지 않으며 더 긴 기존 타임아웃은 유지합니다.')

    @automatic.command(name='이미지검사', description='첨부 이미지의 로컬 OCR 검사를 켜거나 끕니다')
    async def image_setting(self, interaction: discord.Interaction, 사용: Literal['켜기', '끄기']) -> None:
        if 사용 == '켜기' and not self.ocr.ready:
            raise ValueError('OCR 모델이 준비되지 않았습니다. 설치 스크립트와 상태를 확인하세요.')
        await self._change(interaction, image_enabled=사용 == '켜기')
        await respond(interaction, f'이미지 OCR: {사용}. 텍스트 설정은 유지됩니다.')

    @automatic.command(name='공개안내', description='DM 실패 시 원문 없는 공개 안내와 자동 삭제 시간을 설정합니다')
    async def public_notice(self, interaction: discord.Interaction, 사용: Literal['켜기', '끄기'],
                            삭제초: app_commands.Range[int, 5, 300] = 15,
                            쿨다운초: app_commands.Range[int, 1, 86400] = 60) -> None:
        await self._change(interaction, public_fallback=사용 == '켜기', public_delete_after=삭제초, notice_cooldown_seconds=쿨다운초)
        await respond(interaction, f'DM 실패 시 공개 안내 {사용}, 삭제 {삭제초}초, 알림 쿨다운 {쿨다운초}초. 검사·집계에는 쿨다운을 적용하지 않습니다.')

    @automatic.command(name='보관기간', description='새 사건의 Firestore 발췌·Storage·Discord 발췌 보관 일수')
    async def retention(self, interaction: discord.Interaction, 일수: app_commands.Range[int, 1, 3650]) -> None:
        await self._change(interaction, retention_days=일수)
        await respond(interaction, f'새 사건의 증거 보관 {일수}일. 기존 사건의 확정 기한은 유지하며 Firestore 발췌·Storage 객체·Discord 로그 발췌를 각각 정리합니다. 누적 횟수·중복 방지·취소·초기화 이력은 유지합니다.')

    @automatic.command(name='증거보관', description='확정 사건 이미지 원본의 비공개 Firebase Storage 보관 설정')
    async def originals(self, interaction: discord.Interaction, 사용: Literal['켜기', '끄기']) -> None:
        if not await self.authorize(interaction, owner_admin_only=True):
            return
        if 사용 == '켜기':
            if self.runtime is None:
                raise ValueError('FIREBASE_STORAGE_BUCKET과 비공개 Storage 설정을 먼저 준비하세요.')
            try:
                await self.runtime.check_storage()
            except FirebaseStorageError as exc:
                raise ValueError(f'비공개 Storage 점검 실패({exc.code}). IAM·Uniform bucket-level access·Public Access Prevention을 확인하세요.') from exc
        await self._change(interaction, retain_originals=사용 == '켜기')
        await respond(interaction, f'새 확정 사건의 원본 보관: {사용}. 비공개 Firebase Storage 객체만 저장하고 Firestore에는 경로·해시를 연결합니다. 공개 URL이나 Discord 이미지 재게시를 만들지 않습니다. 기존 보관분은 각 만료 기한에 정리합니다.')

    @automatic.command(name='만료정리', description='이 서버의 만료 증거만 정리합니다')
    async def purge(self, interaction: discord.Interaction) -> None:
        count = await self._retention_pass(guild_id=interaction.guild_id)
        await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'purge_expired', str(count))
        await self.audit_notice(interaction.guild, interaction.user.id, '만료 증거 정리', str(count))
        await respond(interaction, f'만료 증거 {count}건 정리. 누적 위반은 유지됩니다.')

    @automatic.command(name='시간대', description='UTC 저장값을 표시할 시간대를 정합니다')
    async def timezone_setting(self, interaction: discord.Interaction, 시간대: str = 'Asia/Seoul') -> None:
        ZoneInfo(시간대)
        await self._change(interaction, display_timezone=시간대)
        await respond(interaction, f'표시: {safe_text(시간대)}. 저장은 UTC입니다.')

    @automatic.command(name='운영역할', description='소유자·Administrator가 운영 역할을 추가하거나 삭제합니다')
    async def operator_role(self, interaction: discord.Interaction, 작업: Literal['추가', '삭제'], 역할: discord.Role) -> None:
        if not await self.authorize(interaction, owner_admin_only=True):
            return
        if 역할.is_default():
            raise ValueError('@everyone은 운영 역할로 지정할 수 없습니다.')
        await self._list_change(interaction, 'operator_role_ids', 역할.id, 작업 == '추가')
        await respond(interaction, f'운영 역할 {역할.id} {작업}. 로그 채널의 해당 역할 보기 권한을 다시 점검하세요. 명령 실행 권한은 저장된 역할로 즉시 확인합니다.')

    @automatic.command(name='분류', description='분류별 적용·검토·끄기를 설정합니다')
    @app_commands.choices(분류=CATEGORY_CHOICES)
    async def category(self, interaction: discord.Interaction, 분류: app_commands.Choice[str], 설정: Literal['적용', '검토', '끄기']) -> None:
        async with self.service.policy_lock(interaction.guild_id):
            config = await self.store.get_config(interaction.guild_id)
            modes = dict(config['category_modes'])
            modes[분류.value] = {'적용': 'enforce', '검토': 'review', '끄기': 'off'}[설정]
            await self.store.update_config(interaction.guild_id, actor_id=interaction.user.id, reason='분류 설정', category_modes=modes)
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '분류 변경', f'{분류.value}: {설정}')
        await respond(interaction, f'{분류.name}: {설정}. 적용이어도 애매한 결과는 자동 처벌하지 않습니다.')

    async def _list_change(self, interaction, field: str, value: int, add: bool) -> None:
        async with self.service.policy_lock(interaction.guild_id):
            config = await self.store.get_config(interaction.guild_id)
            values = set(config[field])
            values.add(value) if add else values.discard(value)
            await self.store.update_config(interaction.guild_id, actor_id=interaction.user.id, reason=field, **{field: sorted(values)})
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, field, f'{value} {"추가" if add else "삭제"}')

    @exceptions.command(name='채널', description='부모 채널 예외는 그 아래 모든 스레드에도 적용됩니다')
    async def exempt_channel(self, interaction: discord.Interaction, 작업: Literal['추가', '삭제'],
                             채널: discord.TextChannel | discord.Thread | discord.ForumChannel) -> None:
        await self._list_change(interaction, 'exempt_channel_ids', 채널.id, 작업 == '추가')
        await respond(interaction, f'채널 {채널.id} {작업}. 부모 텍스트/포럼 채널의 예외는 하위 스레드에 상속됩니다.')

    @exceptions.command(name='역할', description='지정 역할의 메시지를 검사에서 제외합니다')
    async def exempt_role(self, interaction: discord.Interaction, 작업: Literal['추가', '삭제'], 역할: discord.Role) -> None:
        if 역할.is_default():
            raise ValueError('전체 검사를 끄려면 /자동관리 모드 값:끄기를 사용하세요.')
        await self._list_change(interaction, 'exempt_role_ids', 역할.id, 작업 == '추가')
        await respond(interaction, f'예외 역할 {역할.id} {작업}.')

    @banned.command(name='추가', description='리터럴 규칙 추가(임의 정규식 금지, 기본은 검토)')
    @app_commands.choices(분류=CATEGORY_CHOICES)
    async def add_rule(self, interaction: discord.Interaction, 표현: app_commands.Range[str, 2, 64],
                       분류: app_commands.Choice[str], 일치방식: Literal['단어', '제한우회', '문자열'],
                       판단: Literal['검토', '공격대상확인'] = '검토') -> None:
        rule = validate_custom_rule(표현, 분류.value, {'단어': 'token', '제한우회': 'bounded', '문자열': 'literal'}[일치방식])
        rule['id'] = 'custom_' + uuid.uuid4().hex[:12]
        if 판단 == '공격대상확인':
            rule['auto_policy'] = 'targeted'
        Engine.from_file(self.rules_path, custom_rules=[rule])
        async with self.service.policy_lock(interaction.guild_id):
            rule_id = await self.store.add_rule(interaction.guild_id, rule)
            await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'add_rule', rule_id)
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '규칙 추가', f'{rule_id} / {표현} / {판단}')
        await respond(interaction, f'규칙 ID {rule_id}, 판단 {판단}. /필터테스트로 정상 문장도 확인하세요.')

    async def _all_rules(self, guild_id: int):
        base = _rules_data(self.rules_path)['rules']
        state = await self.store.list_rules(guild_id)
        return [*base, *state['custom_rules']], state['disabled_ids']

    async def _toggle_rule(self, interaction, rule_id: str, disabled: bool) -> None:
        async with self.service.policy_lock(interaction.guild_id):
            rules, _ = await self._all_rules(interaction.guild_id)
            if not any(r['id'] == rule_id for r in rules):
                raise ValueError('규칙 ID를 찾을 수 없습니다.')
            await self.store.set_rule_disabled(interaction.guild_id, rule_id, disabled)
            await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'disable_rule' if disabled else 'restore_rule', rule_id)
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '규칙 변경', f'{rule_id}: 비활성 {disabled}')
        await respond(interaction, f'{safe_text(rule_id)}: {"비활성화" if disabled else "복원"}. 다른 서버와 기본 JSON은 바뀌지 않습니다.')

    @banned.command(name='삭제', description='이 서버에서 기본·추가 규칙을 비활성화합니다')
    async def disable_rule(self, interaction: discord.Interaction, 규칙id: str) -> None:
        await self._toggle_rule(interaction, 규칙id, True)

    @banned.command(name='복원', description='이 서버에서 비활성화한 규칙을 다시 사용합니다')
    async def restore_rule(self, interaction: discord.Interaction, 규칙id: str) -> None:
        await self._toggle_rule(interaction, 규칙id, False)

    @banned.command(name='목록', description='기본·추가·비활성화 규칙을 15개씩 표시합니다')
    async def list_rules(self, interaction: discord.Interaction, 페이지: app_commands.Range[int, 1, 100] = 1) -> None:
        rules, disabled = await self._all_rules(interaction.guild_id)
        lines = [f'규칙 {len(rules)}개 / {페이지}페이지']
        for rule in rules[(페이지 - 1) * 15:페이지 * 15]:
            lines.append(f'{rule["id"]} | {safe_text(rule["expression"], 80)} | {rule["category"]} | {rule["match_mode"]} | {rule["auto_policy"]} | {"꺼짐" if rule["id"] in disabled else "사용"}')
        await respond(interaction, '\n'.join(lines))

    @allowed.command(name='추가', description='해당 구간만 제외하는 허용어를 추가합니다')
    async def add_allow(self, interaction: discord.Interaction, 표현: app_commands.Range[str, 2, 80]) -> None:
        term = validate_allow_term(표현)
        async with self.service.policy_lock(interaction.guild_id):
            await self.store.add_allow(interaction.guild_id, term)
            await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'add_allow', term)
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '허용어 추가', term)
        await respond(interaction, '해당 구간만 허용했습니다. 문장의 다른 욕설은 계속 검사합니다.')

    @allowed.command(name='삭제', description='관리자가 추가한 허용어를 삭제합니다')
    async def remove_allow(self, interaction: discord.Interaction, 표현: str) -> None:
        async with self.service.policy_lock(interaction.guild_id):
            removed = await self.store.remove_allow(interaction.guild_id, 표현)
            await self.store.record_admin_audit(interaction.guild_id, interaction.user.id, 'remove_allow', 표현[:100])
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '허용어 삭제', 표현[:100])
        await respond(interaction, '추가 허용어 삭제 완료.' if removed else '추가 허용어에 없습니다. 기본 정상 문장 보호는 JSON에서 관리합니다.')

    @allowed.command(name='목록', description='현재 기본·추가 허용어를 확인합니다')
    async def list_allow(self, interaction: discord.Interaction) -> None:
        engine = await self.service.get_engine(interaction.guild_id)
        await respond(interaction, '허용어(해당 구간만 제외):\n' + '\n'.join(safe_text(t, 150) for t in engine.allow_terms))

    @app_commands.command(name='필터테스트', description='제재 없이 탐지 구간·근거·현재 정책을 확인합니다')
    @app_commands.guild_only()
    @app_commands.check(admin_check)
    async def filter_test(self, interaction: discord.Interaction, 문장: app_commands.Range[str, 1, 4000]) -> None:
        preview = await self.service.test_text(interaction.guild_id, 문장)
        lines = ['테스트: 사건·누적 횟수·삭제·타임아웃을 만들지 않습니다.', preview.recommended_action]
        for f in preview.findings:
            lines.append(f'\n{f.rule_id} / {f.category} / [{f.start}:{f.end}) {safe_text(f.matched, 300)}\n심각도 {f.severity} / 근거 등급 {f.confidence} / 권장 {f.recommendation}\n{safe_text(f.reason, 450)}')
        lines.append('근거 등급은 확률이 아닙니다. 실제 운영에서는 역할·채널·삭제·타임아웃 권한도 확인합니다.')
        await respond(interaction, '\n'.join(lines))

    @app_commands.command(name='이미지테스트', description='제재 없이 한국어·영어 OCR과 탐지 결과를 확인합니다')
    @app_commands.guild_only()
    @app_commands.check(admin_check)
    async def image_test(self, interaction: discord.Interaction, 파일: discord.Attachment) -> None:
        data = await download_attachment(self._http, 파일)
        result = await self.ocr.submit(data, filename=파일.filename, declared_mime=파일.content_type)
        async with self.service.policy_lock(interaction.guild_id):
            config = await self.store.get_config(interaction.guild_id)
            engine = await self.service.get_engine(interaction.guild_id)
            inspection = Inspection(images=[InspectedImage(str(파일.id), 파일.filename, result, data)],
                                    blocked=result.status not in {'ok', 'no_text'})
            findings = image_findings(engine, inspection, '', config['category_modes'])
        lines = ['이미지 테스트: 삭제·누적 증가·타임아웃·증거 저장 없음.',
                 f'OCR 상태: {result.status} / {result.detail}',
                 f'현재 정책: 경고 {config["warning_limit"]}회 이후 매번 {duration_label(config["timeout_seconds"])}',
                 'OCR 원문(모델 점수는 정확도 확률이 아님):']
        lines.extend(f'{line.confidence:.3f} | {safe_text(line.text, 1000)}' for line in result.lines)
        for f in findings:
            lines.append(f'{f.rule_id} / {f.category} / OCR[{f.start}:{f.end}) {safe_text(f.matched, 200)} / {f.confidence}\n{safe_text(f.reason, 450)}')
        clear = any(f.confidence == 'clear' and f.recommendation == 'enforce' for f in findings)
        lines.append('예상: 현재 적용 모드·권한·최신 메시지 확인 뒤 새 확정 위반 1회 처리 가능.' if clear and config['mode'] == 'enforce' and not inspection.blocked else '예상: 자동 처벌 없음(감지 없음·검토·관찰/끄기·검사 실패 중 해당 상태).')
        await respond(interaction, '\n'.join(lines))

    @violations.command(name='조회', description='자동 초기화하지 않는 누적 위반과 최근 사건을 확인합니다')
    async def violation_list(self, interaction: discord.Interaction, 사용자: discord.Member) -> None:
        config = await self.store.get_config(interaction.guild_id)
        count = await self.store.count_violations(interaction.guild_id, 사용자.id)
        rows = await self.store.list_violations(interaction.guild_id, 사용자.id, limit=20)
        await respond(interaction, f'사용자 ID {사용자.id} / 현재 유효 누적 {count}회\n' + safe_text(json.dumps(rows, ensure_ascii=False, indent=2), 80000)
                      + f'\n표시 시간대: {config["display_timezone"]}; 상세 원본 시각은 UTC ISO입니다.')

    @violations.command(name='사건', description='사건 발췌·판정·실제 조치 결과를 확인합니다')
    async def incident(self, interaction: discord.Interaction, 사건: str) -> None:
        row = await self.store.get_incident_by_id(interaction.guild_id, 사건)
        if row is None:
            await respond(interaction, '이 서버의 사건을 찾을 수 없습니다.')
            return
        actions = await self.store.get_actions(사건)
        audit = await self.store.get_warning_audit(interaction.guild_id, 사건)
        await respond(interaction, safe_text(json.dumps({'incident': row, 'actions': actions, 'audit': audit}, ensure_ascii=False, indent=2), 100000))

    @violations.command(name='취소', description='오탐을 누적에서 제외하고 취소 이력을 보존합니다')
    async def cancel_violation(self, interaction: discord.Interaction, 사건: str, 사유: app_commands.Range[str, 1, 300]) -> None:
        incident = await self.store.get_incident_by_id(interaction.guild_id, 사건)
        if incident is None:
            await respond(interaction, '이 서버의 사건을 찾을 수 없습니다.')
            return
        async with self.service.user_lock(interaction.guild_id, incident['user_id']):
            canceled = await self.store.cancel_violation(interaction.guild_id, 사건, interaction.user.id, 사유)
        await self.audit_notice(interaction.guild, interaction.user.id, '위반 취소', f'{사건} / {사유} / 변경 {canceled}')
        await respond(interaction, '취소 완료. 누적에서 제외했습니다. 삭제된 메시지를 복원하거나 기존 타임아웃을 해제하지 않습니다.' if canceled else '이미 취소됐거나 취소할 확정 위반이 없습니다.')

    @violations.command(name='초기화', description='사용자 누적을 초기화하고 이전 지연 OCR 결과를 무효화합니다')
    async def reset_violation(self, interaction: discord.Interaction, 사용자: discord.Member, 사유: app_commands.Range[str, 1, 300]) -> None:
        async with self.service.user_lock(interaction.guild_id, 사용자.id):
            generation = await self.store.reset_violations(interaction.guild_id, 사용자.id, interaction.user.id, 사유)
            self.service.invalidate(interaction.guild_id)
        await self.audit_notice(interaction.guild, interaction.user.id, '누적 초기화', f'사용자 ID {사용자.id} / {사유} / 세대 {generation}')
        await respond(interaction, '누적 초기화 완료. 이전 사건 이력과 중복 방지는 유지하며 초기화 이전에 시작된 지연 OCR은 집계하지 않습니다. 기존 타임아웃은 해제하지 않습니다.')


async def setup(bot: commands.Bot) -> None:
    # Check before cog_load starts OCR/background tasks; never replace another
    # extension's Cog or slash command merely to make this extension load.
    if bot.get_cog("AutoMod") is not None:
        raise RuntimeError("AutoMod Cog가 이미 등록되어 있습니다. 중복 로딩을 확인하세요.")
    reserved = ("자동관리", "금칙어", "허용어", "예외", "위반", "필터테스트", "이미지테스트")
    conflicts = [name for name in reserved if bot.tree.get_command(name) is not None]
    if conflicts:
        raise RuntimeError("기존 슬래시 명령과 이름이 겹칩니다: " + ", ".join(conflicts)
                           + ". 기존 명령을 덮어쓰지 않았습니다. 명령 이름을 조정하세요.")
    await bot.add_cog(AutoMod(bot))

