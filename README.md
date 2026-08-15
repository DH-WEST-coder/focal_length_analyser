# Focal AI Export

사진을 수정하지 않고 EXIF 초점거리 데이터를 AI 분석용 JSONL·CSV로 내보내는 데스크톱 앱입니다.

## 다운로드 및 실행

GitHub의 [Releases](../../releases)에서 사용 중인 운영체제용 파일을 다운로드하세요. 사진은 읽기 전용으로 스캔되며, 원본 사진과 EXIF는 수정하지 않습니다.

### macOS

1. `FocalAIExport-macos.zip` 압축을 풉니다.
2. `FocalAIExport.app`을 `Applications` 폴더로 옮긴 뒤 실행합니다.
3. macOS가 개발자를 확인할 수 없다고 표시하면 앱을 Control-클릭하고 **열기**를 한 번 선택합니다.
4. 앱에서 **사진 폴더**와 **결과 저장 위치**를 선택하고 **AI 데이터 내보내기**를 누릅니다.

### Windows

1. `FocalAIExport.exe`를 다운로드합니다.
2. 파일을 원하는 폴더에 보관한 뒤 더블클릭해 실행합니다.
3. Windows 보호 경고가 표시되면 다운로드 파일이 공식 Releases에서 왔는지 확인한 뒤 **추가 정보 → 실행**을 선택합니다.
4. 앱에서 **사진 폴더**와 **결과 저장 위치**를 선택하고 **AI 데이터 내보내기**를 누릅니다.

Windows 실행 파일은 단일 파일 패키지이며, 두 운영체제 모두 별도 Python 설치가 필요하지 않습니다.

## 출력

- `photos.jsonl`: AI 입력에 적합한 사진별 레코드
- `photos.csv`: 스프레드시트·분석 도구용 동일 데이터
- `dataset.json`: 스키마, EXIF 품질, 카메라별 내부 crop factor 근거
- `yearly_summary.csv`: 연도별 산술·기하평균, 중앙값, 사분위수 (세션/burst/촬영일/월/사진 가중치 × 전체/단렌즈/줌렌즈)
- `exact_focal_usage.csv`: 정확한 35mm 환산 초점거리별 사용량과 순위
- `log_focal_cluster_usage.csv`: 1/6-stop(±약 5.9%) 로그 초점거리군별 사용량
- `rolling_12m_summary.csv`: 매월 종료 시점의 최근 12개월 통계
- `monthly_data_quality.csv`: 월별 EXIF 품질, 촬영일·세션 수, active month 여부
- `zoom_position_usage.csv`: 전체 줌 및 렌즈 모델별 5구간 줌 위치 분포
- `session_gap_sensitivity.csv`: 세션 간격 60/90/120분 및 선택값 민감도
- `lens_observed_coverage.csv`: 렌즈별 데이터 최초·최종 관측 범위
- `preference_score.csv`: photo 50% + shooting day 30% + month 20% 정규화 점수와 photo/day 동시 강도
- `AI_HANDOFF.md`: 다른 AI에 전달할 권장 요청문과 필드 설명

절대 파일 경로, GPS, 카메라 일련번호, 이미지 픽셀은 출력하지 않습니다. 세션·촬영일 검증을 위해 상대 파일명과 EXIF 촬영 시각은 포함됩니다.

## 데이터 규칙

- `DateTimeOriginal` 우선, 없으면 폴더의 연·월을 대체값으로 사용
- EXIF 35mm 환산값 우선
- 동일 카메라의 검증된 실초점거리/환산값이 3장 이상일 때만 중앙 crop factor로 보완
- 근거가 없으면 `focal_35mm_source=missing`으로 유지
- 단/줌은 `LensSpecification`, 없으면 `LensModel`로 판정
- 줌 위치는 로그 기준 0(광각단)~1(망원단)으로 저장
- 보정본 한 장을 최종 선택 결과로 보고 `photo_weight`를 대표 지표로 사용
- `shooting_day_weight`는 반복성, `month_weight`는 장기 지속성 보조 지표
- 50/30/20 종합점수는 각 가중치별 초점거리 사용률을 먼저 정규화한 뒤 계산
- 기본 90분 세션과 5초 burst는 촬영 행동용 고급 진단값으로만 제공
- 평균 초점거리는 산술평균과 로그 기반 기하평균을 함께 제공
- active month는 촬영일 3일 이상 또는 사진 50장 이상으로 표시
- 렌즈 관측 범위는 구매·판매·보유기간으로 추정하지 않음

## 개발·로컬 빌드 (macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
PYTHONPATH=src python -m pytest -q
FOCAL_PYI_CONFIG="$PWD/.pyinstaller_cache" PYINSTALLER_CONFIG_DIR="$FOCAL_PYI_CONFIG" \
  python -m PyInstaller --noconfirm --clean --windowed --name FocalAIExport \
  --paths ./src --workpath ./build/FocalAIExport --distpath ./dist --specpath . src/main.py
```

완성 앱: `dist/FocalAIExport.app`

Windows `.exe`는 macOS에서 교차 빌드하지 않습니다. GitHub Actions의 Windows 러너가 릴리즈 태그마다 `dist/FocalAIExport.exe`를 생성합니다.

## GitHub 릴리즈 만들기

`v1.0.0`처럼 `v`로 시작하는 태그를 GitHub에 푸시하면 `.github/workflows/release.yml`이 macOS와 Windows 패키지를 병렬로 빌드하고 해당 태그의 GitHub Release에 첨부합니다.

```bash
git tag v1.0.0
git push origin v1.0.0
```

워크플로 진행 상황은 GitHub 저장소의 **Actions** 탭에서 확인합니다. 빌드가 끝나면 **Releases** 탭에서 두 파일을 다운로드할 수 있습니다.

## 명령줄 검증용 실행

```bash
PYTHONPATH=src .venv/bin/python src/export_cli.py "/사진/폴더" \
  --output-dir "/결과를/저장할/기존/상위폴더" \
  --session-gap-minutes 90 --burst-gap-seconds 5
```
