# AutoMod 등록 비교 수정 + OCR 시작 준비 + Firestore 오류 진단

버전: 2026-10-27.registration-2 / Cog 2.1.2-singlefile-diagnostics
기준: 이 대화에서 직전에 제공한 등록 수정 bot.py 및 Firebase 인증 재사용 automod.py.

## 화면에서 확인된 내용과 아직 모르는 내용

화면에는 `Discord 재조회 스키마 불일치: 예외`, OCR `FileNotFoundError`,
일반화된 Firestore 오류가 함께 있습니다. 세 가지를 같은 장애로 취급하면 안 됩니다.

이전 bot.py의 명령 비교 함수는 channel_types 배열의 순서를 그대로 비교했습니다.
허용하는 채널 유형이 같고 순서만 달라도 실패로 표시하는 코드 결함입니다.
이번에는 송신과 비교 모두 유형을 정수 집합으로 정규화합니다.
반면 실제 파라미터/선택지 순서, 필수 여부, 유형, 권한, 값 제약 차이는 계속 검출합니다.
원격 Discord 응답 전체는 받지 않았으므로, 운영 서버의 차이가 이 순서 차이뿐이라고
확정한 것은 아닙니다. 실제 차이가 남으면 [AutoMod 명령 차이]에 구체적인 필드가 나옵니다.

Firestore 원래 예외는 이전 파일에서 숨겨졌으므로 현재 사진만으로 인덱스 누락,
IAM, 인증, 데이터베이스, 네트워크 중 하나를 확정할 수 없습니다.
이번 수정은 예외 종류와 실패 단계를 보존하고, 단일 문서 읽기와 복합 쿼리 실패를
구분합니다. 보안 규칙/IAM을 변경하거나 로컬 카운터로 우회하지 않습니다.

## 적용 파일

프로젝트 루트에 bot.py와 start_automod.py를, cogs/에 automod.py를 배치합니다.
기존 파일은 별도 위치에 백업하세요. cogs 폴더에 백업 .py를 남기면 자동 로더가
그 파일도 로딩할 수 있으므로 백업 파일은 cogs 밖으로 옮기세요.

```
프로젝트/
  bot.py                     교체
  start_automod.py            추가 (모델 준비 후 기존 bot.py 실행)
  cogs/
    automod.py               교체 (여전히 한 개의 Cog 파일)
    기존 다른 Cog들          유지
```

requirements.txt, Dockerfile, Procfile, runtime.txt, 기존 Firebase 인증은 자동으로 바꾸지 않습니다.
현재 환경이 Python 3.11 이상이고 discord.py 및 기존 OCR/Firebase 의존성이 설치되어 있어야 합니다.
다른 Discord 라이브러리를 함께 설치하지 마세요. 이 패치는 새 패키지를 설치하는 도구가 아닙니다.
모델 설치 중 ModuleNotFoundError가 발생하면 기존 OCR 의존성 설치부터 완료해야 합니다.

## Railway 적용 순서

1. 위 세 파일을 현재 연결된 저장소에 반영합니다.
2. 해당 서비스의 Settings → Deploy → Start Command를 다음으로 지정합니다.

```
python start_automod.py
```

3. 환경변수는 기존 것을 유지합니다.

```
FIREBASE_PROJECT_ID=discord-player-info-bot
```

FIREBASE_KEY는 기존 Secret을 그대로 사용하며 코드/문서에 복사하지 않습니다.
잘못된 GOOGLE_APPLICATION_CREDENTIALS 값을 새로 넣을 필요도 없습니다.
4. 새 배포를 실행하고 다음 표식을 확인합니다.

```
[AutoMod 시작] ...
[AutoMod 패치] 2026-10-27.registration-2
[AutoMod 등록확인] ... 자동관리 7/7 ...
```

표식은 예상 출력입니다. 실제 운영 배포를 여기서 실행한 것은 아닙니다.
커스텀 Docker ENTRYPOINT가 있다면 Start Command가 실제 적용되는지 시작 로그로 확인하세요.
기존 Dockerfile이 특정 파일만 COPY한다면 start_automod.py도 컨테이너에 포함해야 합니다.

## OCR 모델 준비

start_automod.py를 시작 명령으로 선택하는 것은 누락된 공개 OCR 모델 다운로드를 허용하는 것입니다.
이 스크립트는 자동 처벌·DB 쓰기·키 출력·메시지 이미지 업로드를 하지 않습니다.

- 필요한 파일: craft_mlt_25k.pth, korean_g2.pth
- 기본 위치: 프로젝트 루트/data/ocr_models
- AUTOMOD_OCR_MODEL_DIR가 있으면 Cog와 같은 경로 규칙으로 해석합니다.
- 둘 다 존재하고 0바이트가 아니면 설치 과정을 건너뜁니다. 파일 존재는 OCR 정확성/정상 로딩의 증명이 아닙니다.
- 누락되면 현재 Python 환경에서 실제 `automod.py --install-ocr-models`를 실행합니다.
- 설치 제한시간은 600초이며 무제한 설치 재시도는 하지 않습니다.
- 설치 실패를 성공으로 표시하지 않습니다. 기존 봇은 시작하지만 OCR 미준비 상태를 명확히 알립니다.
- 모델의 실제 초기화와 안전 검사는 기존 OCR 작업자가 수행합니다.
- 파손된 비어 있지 않은 모델은 아래 명령으로 점검/재설치하세요.

```
python cogs/automod.py --check-ocr-models
python cogs/automod.py --install-ocr-models
```

모델 경로를 Railway Volume에 두면 재배포마다 다시 내려받는 일을 줄일 수 있습니다.
예를 들어 실제 Volume을 `/automod-data`에 연결한 다음:

```
AUTOMOD_OCR_MODEL_DIR=/automod-data/ocr_models
```

환경변수만 설정한다고 영구 저장소가 생기지 않습니다. 실제 Volume 연결이 필요합니다.
다른 경로에 설치된 모델을 가리는 볼륨 마운트에도 주의하세요.

## Discord에서 확인

서버 소유자 또는 Administrator가 실행합니다. 접두사는 기존 BOT_PREFIX를 따릅니다.
기본 접두사는 ! 입니다.

```
!자동관리등록
!자동관리점검
```

첫 명령은 현재 서버의 7개 자동관리 루트를 개별 등록·재조회합니다.
두 번째는 등록 ID와 하위 명령을 읽기 전용으로 확인합니다.
명령별 갱신을 사용하며 전체 명령 삭제/일괄 덮어쓰기를 하지 않습니다.
권한 차이는 무시하지 않습니다. 명령 목록에 보이는지와 실제 실행 권한은 별개입니다.

등록 7/7이 확인되면 입력창에서 /자동관리를 입력하여 자동 완성 목록의 연결점검을 선택합니다.
그냥 일반 메시지로 `/자동관리 연결점검`이라는 글자를 보내는 것과는 다릅니다.

Firestore 상세 점검을 슬래시 없이 하려면:

```
!자동관리저장점검
```

이 명령의 전체 결과는 요청자의 DM으로만 보냅니다. DM 차단 시 공개 채널에 상세 보고서를
대신 보내지 않습니다. 슬래시 연결점검 응답은 기존처럼 실행자에게만 표시됩니다.
이 진단은 위반 횟수, 사건, 삭제, 처벌, 원본 보관을 만들지 않습니다.
정상 점검 후 기존 복구 루프가 이전 미완료 사건을 처리하는 동작은 여전히 별개로 존재합니다.

## Firestore: 오류 종류별 조치

로그 예시(발생할 때만):

```
[AutoMod Firestore 오류] operation=query category=missing_index exception=FailedPrecondition ...
[AutoMod 인덱스 생성 링크] https://console.firebase.google.com/...
```

- missing_index: 표시된 Firebase Console 링크에서 인덱스를 생성하고 사용 가능 상태인지 확인합니다.
- permission_denied: 서비스 계정의 IAM, API 활성화, 대상 프로젝트를 확인합니다. Rules를 공개로 바꾸지 않습니다.
- authentication: 기존 FIREBASE_KEY/ADC와 서비스 계정 유효성을 확인합니다. 키를 공유하지 않습니다.
- not_found: 프로젝트와 (default) Firestore 데이터베이스를 확인합니다.
- network_or_timeout: 네트워크/gRPC/시간 초과 상태를 확인합니다.
- quota: 요청량과 할당량을 확인합니다.
- failed_precondition/unknown: 인덱스 오류라고 추정하지 않고 예외 종류와 실패 단계를 확인합니다.

오류 원문 전체는 로그로 내보내지 않습니다. 인덱스 생성 URL도 Firebase Console의
허용된 경로와 create_composite 파라미터만 통과시킵니다.

연결점검은 단일 문서 조회뿐 아니라 아래 네 쿼리를 각각 limit(1)로 검사합니다.
한 쿼리 실패로 뒤의 인덱스 검사가 사라지지 않으며 문서 내용을 사용자에게 반환하지 않습니다.
읽기 성공은 쓰기 IAM과 실제 Discord 제재 성공까지 증명하는 것이 아닙니다.

### 필요한 복합 인덱스

컬렉션 ID `incidents`, 쿼리 범위 `Collection`입니다.
기존 인덱스를 삭제하거나 초기화하지 않습니다.

| 쿼리 | 필드 순서 |
|---|---|
| 미전송 로그 | log_status 오름차순 → log_attempt_at 오름차순 |
| 증거 만료 정리 | retention_state 오름차순 → retention_next_attempt_at 오름차순 |
| 사용자 전체 위반 | user_id 오름차순 → counted_ever 오름차순 → created_at 내림차순 |
| 사용자 현재 세대 위반 | user_id 오름차순 → counted_ever 오름차순 → generation 오름차순 → created_at 내림차순 |

`firestore.indexes.automod.json`은 이 네 항목만 담은 병합용 자료입니다.
기존 프로젝트의 firestore.indexes.json이나 firebase.json을 통째로 교체하지 마세요.
기존 indexes에 없는 항목만 합치고 기존 fieldOverrides도 유지해야 합니다.
Console의 오류 링크를 이용하면 필요한 쿼리에 맞는 인덱스가 자동 입력됩니다.
인덱스 생성은 콘솔 관리자 권한으로 수행하며 봇 서비스 계정에 소유자 권한을 주지 않습니다.

Admin SDK는 클라이언트 Security Rules를 우회하고 IAM을 사용합니다.
공개 Rules로 바꾸는 것은 이 문제의 해결 방법이 아니며 이 패치는 Rules를 수정하지 않습니다.

## 변경하지 않은 부분

- 첫 확정 위반 경고, 두 번째부터 기본 고정 1시간, 관리자 처벌 설정.
- Firestore 전용 경로, 사용자 누적·사건 ID·취소/초기화 세대, 중복 방지.
- 텍스트 감지 엔진과 OCR 인식 방식, 예외/허용어, 비공개 로그 검사.
- 타임아웃 재시도 시 종료 시각 고정, 긴 기존 타임아웃 보존.
- 기존 선수 관리 함수, 다른 Cog의 코드, 토큰/인증 Secret.

Firestore 오류일 때 보호 상태를 거짓으로 정상 처리하거나 메모리 카운터로 제재하지 않습니다.

## 실제 수행한 검증

- Python 3.13.5에서 수정한 세 Python 파일 compile 성공.
- Python 3.11 문법 AST 파싱 성공(3.11 인터프리터 실제 실행이 아님).
- 표준 unittest 모의 테스트 69개 통과: 등록 45, 진단 15, 시작 스크립트 9.
- 실제 CLI `python cogs/automod.py --help` 실행 성공, Firebase/Discord 초기화 없음.
- 기존 bot.py의 최상위 함수 66개는 AST 비교상 동일. 등록 도우미/클래스 메서드와 진단부는 변경됨.

테스트 재현(프로젝트 루트에서):

```
python -m unittest discover -s tests -v
```

실제 Discord/Firebase SDK는 이 환경에 설치되어 있지 않으며 패키지 설치 시도는 DNS 제한으로
실패했습니다. 테스트는 실제 함수/메서드 코드를 AST로 추출해 모의 네트워크 객체에 실행했습니다.
실제 SDK 통합, Discord API 등록·메뉴 표시·권한, 운영 Firestore/IAM/인덱스 생성,
Railway 재배포, OCR 모델 다운로드·실제 인식은 이번 차례에 실행하지 않았습니다.

TEST_OUTPUT.txt에 모의 설치 실패/timeout 출력이 포함됩니다. 이는 실패 처리 테스트의
예상 출력이며 사용자 서버에서 얻은 로그가 아닙니다.

## 확인에 사용한 공식 문서

- Discord Application Commands: https://docs.discord.com/developers/interactions/application-commands
- discord.py channel transformers: https://github.com/Rapptz/discord.py/blob/master/discord/app_commands/transformers.py
- Firestore indexes: https://firebase.google.com/docs/firestore/query-data/indexing
- Firebase Admin setup: https://firebase.google.com/docs/admin/setup
- Railway Start Command: https://docs.railway.com/deployments/start-command
- Railway Volumes: https://docs.railway.com/volumes
- EasyOCR Reader settings: https://www.jaided.ai/easyocr/documentation/

운영 환경에 실제 적용되었다는 의미가 아니라 코드 수정의 참고 자료입니다.
