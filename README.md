# US Market Monitor

미국 증시 알람 서비스를 위한 공개 저장소입니다. 현재는 프로젝트 초기 구성 단계입니다.

## 개발 준비

Python 3.11 이상을 사용합니다. PowerShell에서 다음 명령을 실행하세요.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
```

`.env`에 개인 설정을 입력하세요. 알람 실행 기능과 환경변수 로딩은 추후 구현합니다.

## 공개 저장소 설정

- `.env`와 `.env.*`, 인증서, 로컬 데이터 및 로그는 Git에서 제외합니다.
- `.env.example`에는 비밀값 없이 설정 항목만 유지합니다.
- API 키, 웹훅 주소, 토큰은 코드나 문서에 직접 입력하지 않습니다.
- GitHub Actions를 추가할 때 비밀값은 GitHub Secrets에 저장합니다.
- `.gitignore`는 이미 커밋한 비밀정보를 제거하지 않습니다. 노출된 키는 폐기하고 재발급하세요.

## 구현 예정

- 미국 증시 데이터 수집 및 장 운영 시간 처리
- 알람 조건과 중복 알림 방지
- 알림 채널 연결
- 실행 스케줄 및 배포 구성

데이터 제공자, 알림 채널과 배포 환경은 아직 정하지 않았습니다.
