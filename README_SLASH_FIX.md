# AutoMod 슬래시 명령 등록·동기화 보완

패치 식별자: `2026-10-27.registration-1`

## 적용

1. 현재 `bot.py`를 프로젝트 밖에 백업합니다.
2. 이 패키지의 `bot.py`로 프로젝트 루트의 `bot.py`를 교체합니다.
3. 기존 `cogs/automod.py`, 다른 Cog, requirements, 토큰, FIREBASE_KEY, FIREBASE_PROJECT_ID는 유지합니다.
4. 기존 호스팅 방식으로 봇 프로세스를 재시작/재배포합니다. 별도 Cog 로더나 `setup_hook()`을 추가하지 않습니다.
5. 로그에서 `[AutoMod 패치] 2026-10-27.registration-1`을 확인합니다. 이 줄이 없다면 다른 파일이나 이전 배포가 실행 중일 수 있습니다.
6. `[AutoMod 등록확인] guild=... app=... 자동관리 7/7`을 확인합니다. 이는 Discord API에서 명령 정의·하위 명령을 다시 읽어 일치함을 확인했다는 뜻입니다. OCR/Firestore/자동 처벌 성공을 뜻하지 않습니다.
7. Discord 입력창에서 `/자동관리`를 입력하고 목록의 `연결점검`을 선택합니다.

이 패치는 등록/동기화를 담당합니다. OCR 모델 없음, Firestore 읽기 실패, 잘못된 서비스 계정, 인덱스 부족을 해결했다고 주장하지 않습니다.

## 슬래시 목록이 없어도 실행하는 복구 명령

서버 소유자 또는 Administrator가 서버 채팅에서 실행합니다.

```text
!자동관리등록
!자동관리점검
```

`BOT_PREFIX`를 변경한 서버는 실제 접두사를 사용합니다. `!자동관리등록`은 현재 서버의 자동관리 명령 7개만 개별 생성/갱신한 후 재조회합니다. `!자동관리점검`은 로컬 등록, 앱 ID, 서버 ID, Discord 전역·서버별 명령 ID와 하위 명령을 조회합니다. 두 명령은 Firestore·OCR에 접근하지 않고, 횟수 증가나 삭제·타임아웃을 하지 않습니다. 권한은 실행 시 서버 소유자 또는 최신 역할의 Administrator 여부로 검사하며 운영 역할만으로는 복구 명령을 실행할 수 없습니다.

## 등록했는데 목록이 보이지 않는 경우

등록 API 성공과 사용자의 Discord 클라이언트 목록 표시는 구별됩니다.

- 서버 설정의 연동/앱 관리에서 **같은 앱 ID**의 봇을 확인하고 명령의 사용자·역할·채널 허용 여부를 확인합니다.
- 실행 사용자의 현재 채널 `애플리케이션 명령어 사용(Use Application Commands)` 권한을 확인합니다. 이는 봇의 타임아웃 권한과 다른 항목입니다.
- 소유자/Administrator 계정에서도 안 보이면 Discord 앱을 완전히 다시 열거나 웹 클라이언트에서도 확인합니다. 클라이언트 목록 갱신을 코드로 보장하지 않습니다.
- HTTP 403 / Missing Access가 기록되면 해당 봇 애플리케이션의 서버 설치 상태를 확인합니다. Developer Portal의 동일 앱에서 Guild Install 및 `bot`, `applications.commands` 범위로 서버에 재승인합니다. 봇을 먼저 추방하거나 Administrator 권한을 부여할 필요는 없습니다. `applications.commands`는 현재 Discord 문서상 `bot` 범위에 자동 포함되므로, 단순히 예전 초대 링크라는 이유만으로 범위 누락을 단정하지 않습니다.
- 봇 인스턴스 여러 개나 다른 코드가 같은 토큰으로 명령을 덮어쓰면 수정본의 등록 결과가 이후 달라질 수 있습니다. 같은 애플리케이션의 명령 배포 담당을 하나로 정합니다.

## 무엇을 변경했는가

- `setup_hook()` 하나와 `cogs` 자동 로더 하나 유지. 기존 선수 관리 함수 53개는 AST 비교에서 변경 없음.
- 실행 디렉터리 대신 `bot.py` 기준으로 `cogs/` 탐색. `_`로 시작하는 보조 파일 제외, 이미 로드한 확장 재호출 방지, AutoMod는 다른 Cog 뒤에 로딩.
- 기본 Firebase 앱 존재 여부를 `get_app()`으로 확인. 기존 FIREBASE_KEY 인증 방식 유지. 전역 `db`가 없을 때 setup_hook에서 결과를 다시 할당.
- Gateway 준비 후 실행하는 작업을 setup_hook에서 한 번 생성. `on_ready` 재접속마다 확장 로딩/동기화를 실행하지 않음. setup_hook 안에서 `wait_until_ready()`를 직접 기다리지 않음.
- 전역 범위에는 현재 로드된 명령을 개별 갱신. 서버 범위에는 AutoMod 7개 최상위 명령만 개별 갱신.
- 자동관리 전역 명령은 서버 설치/서버 실행 문맥을 명시. 서버 명령 등록에서는 전역 전용 필드를 제외.
- Discord 응답의 명령 ID·이름·옵션·권한 기본값을 재검사. 하위 `연결점검`, `저장상태`, `상태` 누락은 성공 처리하지 않음.
- 로컬 트리와 AutoMod Cog의 명령 소유권이 다르면 덮어쓰지 않음. 로컬 트리에만 누락됐고 Cog에 정의가 있으면 해당 항목만 복원.
- 전체 목록을 비우는 `clear_commands()` / 일괄 PUT / DELETE 사용하지 않음. 비어 있거나 일부 Cog가 실패한 상태의 일괄 동기화로 기존 원격 명령을 삭제하는 문제 방지.
- 동일 정의는 다시 POST하지 않음. 현재 스키마가 다른 AutoMod 항목만 갱신.
- 수동 복구/점검에 서버 관리자 검사와 쿨다운. 원문 메시지/토큰/인증 JSON을 진단에 넣지 않음.
- 종료 시 등록 작업과 기존 HTTP 세션 정리. 기존 `on_message` 처리나 `process_commands()` 추가 없음.

## 구현상 주의

Discord의 공식 `POST /applications/{id}/guilds/{guild_id}/commands` 개별 upsert를 사용합니다. 이 요청에는 기존 discord.py HTTP 전송 계층(`bot.http.request`, `discord.http.Route`)과 명령 직렬화(`to_dict`, 번역기 설정 시 `get_translated_payload`)를 재사용하므로 새 토큰을 코드에 넣거나 다른 Discord 라이브러리를 설치하지 않습니다. 이 중 전송 계층/직렬화 메서드는 discord.py 내부 구현에 의존합니다. 큰 버전 업그레이드 시 재검증해야 합니다.

서버별 AutoMod 명령의 실행은 기본 `CommandTree.fallback_to_global=True`로 실제 Cog 콜백에 연결됩니다. 서버별 동일 이름의 다른 로컬 명령이 있거나 이 옵션을 껐다면 안전하게 등록을 중단합니다. Cog 해제 후 남는 별도 로컬 별칭을 만들지 않습니다.

이미 존재하는 전역 AutoMod 명령을 삭제하지 않습니다. 서버별 명령을 추가로 보완하며, 기존 Discord 역할·사용자·채널별 명령 제한도 임의로 변경하지 않습니다. 원격에서 다른 이름으로 남은 오래된 명령은 자동 삭제하지 않습니다.

## 실제 검증

```bash
python -m py_compile bot.py
python -m unittest discover -s tests -v
```

이 패키지 작성 환경: Python 3.13.5. Python 3.11 문법 파싱도 확인했습니다.

- 등록/갱신/검증/중복/동시성/서버 분리/권한/비파괴 동작: **35개 오프라인 테스트 통과**.
- 변경 전 bot_fixed.py와 함수 AST 비교: 기존 함수 **53개 변경 없음**.
- 실제 제공된 automod.py 정적 검사: 최상위 명령 7개, 자동관리 하위 명령 18개, 필수 진단 3개 존재.
- `TEST_OUTPUT.txt`는 실제 실행 출력입니다.

테스트는 표준 라이브러리 `unittest`와 Discord·HTTP 대역을 사용합니다. bot.py의 실제 등록 코드를 AST로 추출해 테스트하며 Firebase 초기화나 기존 운영 명령을 실행하지 않습니다. 실제 discord.py 패키지 설치를 시도했으나 이 실행 환경의 패키지 서버 DNS 연결 제한으로 실패했습니다. 따라서 **실제 discord.py 가져오기/네트워크 통합, Discord 서버에 대한 등록 요청, 클라이언트 명령 목록 표시, Firebase 및 OCR은 이번 패치에서 실행 검증하지 않았습니다.** 과거 답변의 수백 개 테스트 성공 주장을 이 패치의 검증으로 사용하지 않습니다.

## 공식 근거

- Discord application commands: https://docs.discord.com/developers/interactions/application-commands
  - 전역/서버 등록 범위, 개별 POST의 upsert 의미, 서버별 명령 즉시 갱신, 명령별 권한.
- discord.py CommandTree: https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.app_commands.CommandTree
  - 로컬 등록과 원격 등록 구분, fallback_to_global, 전역/서버 조회 범위.

이 패키지에는 bot.py, 문서, 테스트와 결과만 포함하며 서비스 계정 JSON, 운영 DB, OCR 모델, 토큰은 포함하지 않습니다.
