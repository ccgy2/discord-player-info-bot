# 기존 bot.py + cogs 확장 모듈 — v5.4.2

기존 **bot.py를 메인 파일**로 실행하고 `cogs.records_roster`를 확장 모듈로 불러오는 구성입니다. 지정 채널의 사용자 멘션을 웹 승인 대기함으로 가져오고, 웹에서 승인한 군을 같은 봇이 Discord 역할에 반영합니다.

## 고정한 설정

| 구분 | ID |
| --- | --- |
| 등말소 채널 | `1551385834995978301` |
| 1군 역할 | `1520484992017367120` |
| 2군 역할 | `1520485077182582977` |

역할 이름 대신 **위 역할 ID만** 사용합니다. 역할 이름을 바꾸거나 같은 이름의 역할이 더 있어도 위 두 역할을 대상으로 처리합니다. 해당 ID의 역할이 삭제되거나 다른 서버의 역할이면 보류하며 이름이 같은 다른 역할로 바꾸지 않습니다.

## 1. cogs 폴더 복사

기능만 추가하는 ZIP의 `cogs` 폴더 내용을 현재 `bot.py` 옆의 `cogs` 폴더에 합칩니다. 기존의 다른 Cog 파일은 그대로 둡니다.

| 파일 | 역할 |
| --- | --- |
| `bot.py` | 기존 봇 메인. 확장 모듈을 불러오는 코드만 연결 |
| `cogs/records_roster.py` | 불러올 Cog의 진입점 |
| `cogs/_records_roster/__init__.py` | 내부 모듈 묶음 |
| `cogs/_records_roster/bridge.py` | 멘션 수집·원문 재확인·대기함 연결 |
| `cogs/_records_roster/roles.py` | 승인 후 정확한 역할 ID 부여·제거 |

**`records_roster.py`만 복사하지 말고 `_records_roster` 폴더도 함께 넣으세요.** 확장 모듈 로더에는 `cogs.records_roster` 하나만 등록합니다. `_records_roster` 내부 파일은 독립 Cog가 아니므로 각각 `load_extension`으로 불러오지 않습니다. 최상위 `cogs/*.py`를 이미 자동으로 불러오는 봇이라면 중복 등록하지 마세요.

## 2. 기존 bot.py에 연결

### 이미 setup_hook 또는 Cog 로더가 있는 경우

Firebase 클라이언트 `db`와 봇 인스턴스가 만들어진 다음, 로그인 전에 다음을 한 번 지정합니다. `db`는 기존 Firebase Admin Firestore 클라이언트입니다. 기존 코드의 변수명이 다르면 실제 변수명을 사용합니다.

```python
bot.records_firestore = db
```

기존 비동기 `setup_hook` 안에서 아래 모듈을 불러옵니다. `self`로 봇을 다루는 클래스라면 `bot` 대신 `self`를 사용합니다.

```python
if "cogs.records_roster" not in bot.extensions:
    await bot.load_extension("cogs.records_roster")
```

기존 다른 확장 모듈을 불러오는 줄은 유지합니다. `load_extension`은 비동기 함수이므로 반드시 `await`를 붙입니다. `setup_hook` 안에서 직접 `wait_until_ready()`를 기다리지 않습니다. 이 Cog가 별도의 시작 작업에서 연결 준비를 기다립니다.

### 이전에 받은 일반 bot.py를 사용하는 경우

같이 제공한 전체 봇 ZIP의 `bot.py`에는 연결이 적용되어 있습니다. 직접 합치려면 기존 `bot = commands.Bot(...)` 부분을 다음처럼 바꿉니다. 기존 `PREFIX`, `intents`, `db`는 그대로 사용합니다.

```python
class KMBBot(commands.Bot):
    async def setup_hook(self):
        await super().setup_hook()
        if "cogs.records_roster" not in self.extensions:
            await self.load_extension("cogs.records_roster")


bot = KMBBot(command_prefix=PREFIX, intents=intents, help_command=None)
bot.records_firestore = db
```

이미 자체 Bot 클래스가 있다면 그 클래스의 `setup_hook`에 모듈을 불러오는 부분만 합치세요. 새 봇 인스턴스를 추가로 만들지 않습니다. 기존 `on_ready`, `on_message`, 명령어, `bot.run(...)`은 계속 사용합니다.

### v5.4.0 / v5.4.1의 직접 연결을 적용했던 경우

**아래 옛 시작 코드와 새 Cog를 함께 실행하지 않습니다.** 이전 `bot.py`에서 다음 세 부분만 제거하고 위 Cog 연결을 사용하세요.

1. `from records_roster_bridge import start_records_roster_bridge`
2. `RECORDS_ROSTER_BRIDGE = None`과 `on_ready`의 해당 전역 변수 선언
3. `on_ready` 안에서 `start_records_roster_bridge(...)`를 호출하던 `try/except` 묶음

`on_ready`의 슬래시 명령 동기화, 기존 선수 동기화, Firestore 감시, 처벌 동기화, Minecraft 시작 코드는 유지합니다. 옛 루트의 `records_roster_bridge.py`, `records_roster_roles.py`는 새 Cog에서 사용하지 않습니다. 구동 중인 옛 프로세스를 종료한 뒤 기존 봇을 **한 인스턴스만 다시 시작**하세요.

## 3. 기록실 연결 환경변수

기존 토큰과 Firebase 비밀 변수는 유지하고 아래 값을 현재 봇 실행 환경에 추가합니다. 기존에 이미 설정했다면 그대로 사용합니다.

```dotenv
RECORDS_BRIDGE_ENABLED=true
RECORDS_FIREBASE_PROJECT_ID=기록실의_기존_projectId
RECORDS_SITE_ID=kmb-league
```

SST 기록실을 연결한다면 마지막 값은 `sst-dbo`입니다. 한 봇 실행은 명시한 기록실 하나에 연결합니다. 기본 채널과 역할 ID는 위 표의 값이므로 역할 ID 환경변수를 추가로 만들 필요가 없습니다.

봇에 전달한 기존 `db.project`가 지정한 기록실 프로젝트와 같을 때만 그 연결을 재사용합니다. 서로 다른 프로젝트라면 기록실 서비스 계정의 `RECORDS_FIREBASE_CLIENT_EMAIL`, `RECORDS_FIREBASE_PRIVATE_KEY`를 추가합니다. 키와 토큰은 웹 파일에 넣지 않습니다.

기록실 연동이 기본 꺼짐인 것은 잘못된 프로젝트로 쓰지 않도록 명시적으로 대상을 정하기 위해서입니다. 활성화했는데 설정이 틀리면 기존 명령어는 유지하고 기록실 연결 보류를 로그에 표시합니다. 설정을 수정한 뒤 봇을 재시작하세요.

`RECORDS_ROLE_SYNC_ENABLED=false`는 수집을 유지하면서 역할 반영만 중지합니다. `RECORDS_BACKFILL_LIMIT=0`은 첫 연결 이후 새 글부터 수집하며, 이미 저장된 수집 커서가 있으면 누락분을 이어 읽습니다.

**웹 승인·역할 데이터 계약은 v5.4.1과 호환되며, 이번 v5.4.2 웹 수정본과도 연결됩니다.** Cog 전환 때문에 기존 승인·기록을 옮길 필요는 없습니다. 경기 등록 오류를 겪은 기록실에는 함께 제공하는 v5.4.2 웹 파일과 Firestore 규칙을 적용하세요. 웹이 아직 승인/역할 기능이 없는 구버전이면 해당 웹 업데이트가 먼저 필요합니다.

## 4. Discord 권한과 사용

1. 위 역할 ID가 등말소 채널과 같은 서버에 있는지 확인합니다.
2. 봇에 **역할 관리** 권한을 주고 봇의 가장 높은 역할을 1군·2군 역할보다 위로 옮깁니다.
3. 지정 채널의 **채널 보기 / 메시지 기록 보기** 권한을 허용합니다.
4. 코드의 `intents.message_content`와 Developer Portal의 **MESSAGE CONTENT INTENT**를 켭니다. 기존 멤버십 기능의 SERVER MEMBERS INTENT도 유지합니다.
5. 기존 실행 명령인 `python bot.py`로 실행합니다. `cogs/records_roster.py`를 따로 실행하지 않습니다.

```text
1군 등록: @선수A @선수B
1군 말소: @선수C
```

실제 Discord 사용자를 선택해서 멘션합니다. 웹 등말소 화면에서 원문 재확인 → 최초 사용자/선수 연결 확인 → 승인을 진행합니다. 승인 전과 반려 시에는 역할을 바꾸지 않습니다.

| 승인 | 역할 처리 |
| --- | --- |
| 1군 등록 | `1520484992017367120` 추가·확인 → `1520485077182582977` 제거 |
| 2군 등록 / 1군 말소 | `1520485077182582977` 추가·확인 → `1520484992017367120` 제거 |
| 최근 변경 정정으로 미분류 복원 | 위 두 역할만 제거 |

다른 역할은 유지합니다. 웹 승인과 Discord 역할 반영 상태는 따로 표시합니다. 권한이나 ID 문제로 보류되면 해당 문제를 해결하고 웹의 **역할 재시도**를 누릅니다. 같은 역할 이름을 새로 만드는 것으로 삭제된 역할 ID를 대체할 수는 없습니다.

## 5. 다시 불러오기·중지

재접속 시 같은 수집기와 역할 작업을 다시 만들지 않습니다. `unload_extension`은 수집 리스너와 시작·수집·역할 작업을 정리하고, `reload_extension`은 새 인스턴스로 다시 연결합니다. 이미 Discord에 도달했을 수 있는 역할 변경은 처리 잠금이 끝난 뒤 실제 역할을 다시 확인하도록 남깁니다. 중지 과정에서 임의로 완료나 실패로 바꾸지 않습니다.

**파일 업데이트 뒤에는 봇을 완전히 재시작하세요.** 일반 확장 모듈 다시 불러오기는 내부 모듈의 새 파일까지 모두 읽는 코드 업데이트 수단으로 보장하지 않습니다. 같은 토큰의 옛 프로세스가 함께 남지 않도록 기존 실행 환경에서 한 번만 재시작합니다.

이번 제공 과정에서는 실제 Discord 로그인·메시지 전송·역할 변경·운영 Firebase 쓰기·배포를 실행하지 않았습니다. 오프라인 검증 범위는 `TEST_REPORT-v5.4.2.md`에 있습니다.

공식 문서: [discord.py 확장 모듈](https://discordpy.readthedocs.io/en/v2.4.0/ext/commands/extensions.html), [Cog 생명주기](https://discordpy.readthedocs.io/en/v2.4.0/ext/commands/api.html), [Discord 역할 권한](https://docs.discord.com/developers/topics/permissions).
