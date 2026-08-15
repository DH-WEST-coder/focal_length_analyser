# Focal AI Export

사진을 수정하지 않고 EXIF 초점거리 데이터를 AI 분석용 JSONL·CSV로 내보내는 macOS 앱입니다.

## 출력

- `photos.jsonl`: AI 입력에 적합한 사진별 레코드
- `photos.csv`: 스프레드시트·분석 도구용 동일 데이터
- `dataset.json`: 스키마, EXIF 품질, 카메라별 내부 crop factor 근거
- `yearly_summary.csv`: 연도별 평균·중앙값·사분위수 (사진/촬영일/월 가중치 × 전체/단렌즈/줌렌즈)
- `exact_focal_usage.csv`: 정확한 35mm 환산 초점거리별 가중 사용량
- `AI_HANDOFF.md`: 다른 AI에 전달할 권장 요청문과 필드 설명

절대 파일 경로, GPS, 카메라 일련번호, 이미지 픽셀은 출력하지 않습니다. `source_file`은 선택 폴더 기준 상대 경로입니다.

## 데이터 규칙

- `DateTimeOriginal` 우선, 없으면 폴더의 연·월을 대체값으로 사용
- EXIF 35mm 환산값 우선
- 동일 카메라의 검증된 실초점거리/환산값이 3장 이상일 때만 중앙 crop factor로 보완
- 근거가 없으면 `focal_35mm_source=missing`으로 유지
- 단/줌은 `LensSpecification`, 없으면 `LensModel`로 판정
- 줌 위치는 로그 기준 0(광각단)~1(망원단)으로 저장

## 개발·빌드

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

## 명령줄 검증용 실행

```bash
PYTHONPATH=src .venv/bin/python src/export_cli.py "/사진/폴더" --output-dir "/결과를/저장할/기존/상위폴더"
```
