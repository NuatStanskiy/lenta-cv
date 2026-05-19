# Price-tag pipeline (Лента) — локальный запуск

Два Docker-контейнера в общей сети `price-net`:

```
clients ─┐
         ├─> price-pipeline :7860   ─POST /process─┐
         │     (FastAPI + YOLO detector            │
         │      + крам/трекинг по видео)           │
         │                                          ▼
         └─────────────────────────────────  qwen-sglang :30000
                                              (OpenAI-compatible vision API,
                                               Qwen3-VL-8B AWQ)
```

`/process` принимает image/video → `rotate → fisheye(опц) → YOLO price-tag-detection → crop с padding → BoT-SORT (только видео) → Qwen3-VL по каждому кропу → CSV`.

---

## 1. Что нужно на машине

- **Windows 11** + **WSL2** + **Docker Desktop** (Linux containers, WSL2 backend)
- **NVIDIA GPU** ≥ 12 GB VRAM (тестировалось на RTX 4060 Ti 16 GB) + актуальные драйверы
- **NVIDIA Container Toolkit** включён в Docker Desktop (Settings → Resources → WSL Integration + GPU)
- **HuggingFace token** (read scope достаточно) — без него скачивание весов идёт со скоростью rate-limit'а

Опционально (для дев-скриптов и visualize): Python 3.12 + venv с `opencv-python`, `pillow`.

### Настройка ресурсов WSL2

Создать `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=20GB
processors=12
swap=8GB
```

Применить: правый клик по иконке Docker Desktop в трее → Quit → запустить заново. Без этого WSL по умолчанию даёт Docker ~50% RAM хоста (~16 GB на 32-гиговой машине), чего может не хватить при больших видео.

---

## 2. Структура репозитория

```
lenta/
├── qwen_sglang_server/                   # запуск SGLang-сервера
│   ├── run_sglang.ps1                    # PowerShell-стартер
│   ├── docker-compose.sglang.yml         # альтернатива через compose
│   └── README.md
├── price_pipeline_api_sglang/            # пайплайн (FastAPI)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                       # /health, /process, /process_json
│   │   ├── detector.py                   # YOLO + tracking
│   │   ├── llm_client.py                 # OpenAI-compatible vision-клиент + JSON schema
│   │   ├── prompt.py                     # промпт для Qwen3-VL
│   │   ├── postprocess.py                # нормализация полей, замена пустых на "Нет"
│   │   ├── csv_writer.py                 # маппинг в целевой CSV (29 колонок)
│   │   ├── fisheye.py                    # rotate + fisheye undistort
│   │   ├── image_ops.py                  # crop/encode helpers
│   │   ├── media.py                      # upload/декодирование
│   │   ├── schemas.py
│   │   ├── security.py                   # опциональный X-API-Key
│   │   └── config.py                     # ENV → Settings
│   └── README.md
├── models/                               # YOLO-веса
│   └── price-tag-detection.pt            # ← смонтировать в pipeline
├── data/                                 # тестовые наборы
├── dataset/, dataset-2/                  # кадры
├── visualize_result.py                   # дев-скрипт: viz для одной картинки
├── visualize_video.py                    # дев-скрипт: viz для видео
└── README.md                             # этот файл
```

---

## 3. Поднять оба контейнера

### 3.1. Общая Docker-сеть

```powershell
docker network create price-net
```

(Если уже существует — `docker network ls` покажет, ошибку можно игнорировать.)

### 3.2. SGLang-сервер (Qwen3-VL-8B AWQ)

```powershell
$env:HF_TOKEN = "hf_..."                  # из https://huggingface.co/settings/tokens

mkdir $HOME\.cache\huggingface -Force | Out-Null

docker run -d `
  --name qwen-sglang `
  --network price-net `
  --gpus all `
  --shm-size 32g `
  --ipc=host `
  -p 30000:30000 `
  -v ${HOME}\.cache\huggingface:/root/.cache/huggingface `
  -e HF_TOKEN=$env:HF_TOKEN `
  lmsysorg/sglang:latest `
  python3 -m sglang.launch_server `
    --model-path Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ `
    --host 0.0.0.0 `
    --port 30000 `
    --trust-remote-code `
    --grammar-backend xgrammar `
    --context-length 4096 `
    --mem-fraction-static 0.80
```

Или одной командой через PowerShell-обёртку:

```powershell
.\qwen_sglang_server\run_sglang.ps1
```

**Что происходит при первом запуске:**
- ~7.25 GB safetensors качаются с HF в `~/.cache/huggingface` (с токеном — минуты, без токена — часы)
- Веса распаковываются и грузятся в VRAM (~5 минут)
- KV cache + CUDA graph + warmup (~30 сек)

**Проверка готовности:**

```powershell
docker logs -f qwen-sglang                # стримить лог
curl http://localhost:30000/health        # пусто = ОК
curl http://localhost:30000/v1/models     # должен вернуть JSON с моделью
```

VRAM-расход на 4060 Ti 16 GB после полной загрузки:
- веса: ~6.9 GB
- KV cache: ~5 GB (35987 токенов на runtime, поделено K/V)
- остаток на активации/CUDA graph

**Важные нюансы:**

- `--rm` НЕ ставить пока не уверены — если контейнер падает, при `--rm` логи стираются мгновенно. Используйте `-d` + `docker logs`.
- Известные несовместимые флаги в текущей `lmsysorg/sglang:latest`:
  - `--mm-max-concurrent-calls` — флага не существует, контейнер падает на старте.
  - `--limit-mm-data-per-request N` — argparse принимает int, но код внутри ждёт dict; warmup-запрос валится с `AttributeError: 'int' object has no attribute 'items'`. Если правда нужно ограничить — `--limit-mm-data-per-request '{"image":1}'`.

### 3.3. Сборка pipeline-контейнера

```powershell
docker build -t price-pipeline-api-sglang .\price_pipeline_api_sglang
```

### 3.4. Запуск pipeline-контейнера

```powershell
# обязательно $env:MSYS_NO_PATHCONV или PowerShell — в Git Bash пути типа
# /models/... мангаются в C:/Program Files/Git/models/...

docker run -d `
  --name price-pipeline `
  --network price-net `
  -p 7860:7860 `
  -e MODEL_PATH=/models/price-tag-detection.pt `
  -e DEVICE=cpu `
  -e LLM_BACKEND=openai_vision `
  -e LLM_API_URL=http://qwen-sglang:30000 `
  -e LLM_ENDPOINT=/v1/chat/completions `
  -e LLM_MODEL=Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ `
  -e LLM_CONCURRENCY=4 `
  -e ROTATE=ccw `
  -v "${PWD}\models\price-tag-detection.pt:/models/price-tag-detection.pt:ro" `
  price-pipeline-api-sglang
```

`ROTATE=ccw` соответствует `cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)` — нужно для видео `data/43_15/43_15.mp4` и подобных. Для изображений, которые уже в правильной ориентации, ставьте `ROTATE=none`.

**Проверка:**

```powershell
curl http://localhost:7860/health
# {"ok":true, "detector_ready":true, "llm_api_url":"http://qwen-sglang:30000", ...}
```

---

## 4. Использование API

### `GET /health`
Здоровье + текущая конфигурация. Не падает, даже если LLM мёртв — `detector_ready` важнее.

### `POST /process` — основной эндпоинт
multipart-форма с полем `file` = image/* | video/*. Возвращает `text/csv` (29 колонок) с заголовком и BOM (UTF-8-sig для Excel).

```powershell
# изображение
curl -F "file=@dataset\images\val\frame_000250.jpg" `
     -o result.csv `
     http://localhost:7860/process

# видео (15 сек 4K — ~3-5 минут на 4060 Ti)
curl -F "file=@data\43_15\43_15.mp4" `
     -o result_43_15.csv `
     --max-time 1800 `
     http://localhost:7860/process
```

### `POST /process_json` — то же, но JSON
Удобно для дебага: каждая строка содержит ещё и `_llm_ok`, `_llm_error`, `_det_confidence`, `_track_id`, `_frame_index` (поля с подчёркиванием не уходят в CSV).

```powershell
curl -F "file=@dataset\images\val\frame_000250.jpg" `
     -o result.json `
     "http://localhost:7860/process_json"
```

Доп. параметр `?skip_llm=true` — пропустить LLM, прогнать только детектор. Удобно проверить, что YOLO находит ценники.

---

## 5. CSV-схема

29 колонок в строгом порядке (формат `data/25_2-10/25_2-10.csv`):

```
filename, product_name, price_default, price_card, price_discount, barcode,
discount_amount, id_sku, print_datetime, code, additional_info, color,
special_symbols, frame_timestamp, x_min, y_min, x_max, y_max,
qr_code_barcode, price1_qr, price2_qr, price3_qr, price4_qr,
wholesale_level_1_count, wholesale_level_1_price,
wholesale_level_2_count, wholesale_level_2_price,
action_price_qr, action_code_qr
```

Источник заполнения:

| источник | поля |
|---|---|
| **LLM (Qwen3-VL)** | `product_name, price_default, price_card, price_discount, barcode, discount_amount, id_sku, print_datetime, code, additional_info, color, special_symbols` |
| **YOLO детектор** | `x_min, y_min, x_max, y_max, frame_timestamp` (ms для видео, 0 для image) |
| **Имя файла** | `filename` (без расширения) |
| **Заглушка `"Нет"`** | все `*_qr`, `wholesale_*`, `action_*` (требуется QR-парсинг — пока не реализован) |

Пустые/нечитаемые поля LLM заполняет строкой `"Нет"` (не `null` и не пустота).

---

## 6. ENV пайплайна — полный список

### Детектор / видео
| ENV | default | смысл |
|---|---:|---|
| `MODEL_PATH` | `/models/price-tag-detection.pt` | YOLO веса |
| `DEVICE` | `cpu` | `cpu`, `cuda`, `cuda:0` (если GPU свободна) |
| `CONF_THRESHOLD` | `0.30` | confidence |
| `IOU_THRESHOLD` | `0.50` | NMS IoU |
| `CROP_PADDING` | `0.10` | расширение bbox |
| `EDGE_MARGIN` | `15` | отбрасывать боксы у краёв |
| `FRAME_INTERVAL_SEC` | `0.20` | шаг кадров для видео |
| `ENABLE_TRACKING` | `true` | model.track для видео |
| `TRACKER_TYPE` | `botsort` | `botsort` / `bytetrack` |
| `TOP_K_PER_TRACK` | `1` | лучших кропов на трек |

### LLM
| ENV | default | смысл |
|---|---:|---|
| `LLM_BACKEND` | `openai_vision` | оставить как есть |
| `LLM_API_URL` | `http://127.0.0.1:30000` | базовый URL SGLang |
| `LLM_ENDPOINT` | `/v1/chat/completions` | |
| `LLM_MODEL` | `Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ` | |
| `LLM_CONCURRENCY` | `4` | сколько кропов параллельно в SGLang |
| `LLM_TIMEOUT_SEC` | `180` | per-crop |
| `LLM_MAX_TOKENS` | `768` | |
| `LLM_TEMPERATURE` | `0.0` | |
| `LLM_USE_JSON_SCHEMA` | `true` | `false` снимет xgrammar (быстрее, но JSON не гарантирован) |

### Ориентация / fisheye
| ENV | default | смысл |
|---|---:|---|
| `ROTATE` | `ccw` | `none, cw, ccw, 180` |
| `FISHEYE_ENABLED` | `false` | |
| `FISHEYE_K, FISHEYE_D, FISHEYE_DIM` | пусто | JSON камеры |

### Безопасность
| ENV | default | смысл |
|---|---:|---|
| `API_KEY` | пусто | если задан — `/process*` требует `X-API-Key` |

---

## 7. Дев-скрипты визуализации

Требуют локальный Python с `opencv-python` и `pillow` (в репо есть `.venv_qwen` под это). Шрифт `arial.ttf` берётся из `C:\Windows\Fonts`.

```powershell
# для одной картинки → viz_out/
#   viz_out/annotated_frame.jpg   — кадр с bbox + price_card сверху
#   viz_out/crops_with_text.jpg   — вертикальная сетка кроп + все 11 LLM-полей
#   viz_out/crop_NN.jpg           — отдельные кропы в полном разрешении
.venv_qwen\Scripts\python.exe visualize_result.py

# для видео → viz_video/
#   viz_video/annotated_frame.jpg — репрезентативный кадр с bbox
#   viz_video/crops_with_text.jpg — сетка с лучшим кропом на каждый track_id
.venv_qwen\Scripts\python.exe visualize_video.py
```

Скрипты захардкожены под `frame_000250.jpg` и `43_15.mp4`; правьте константы в начале файла под свой кейс.

---

## 8. Производительность (RTX 4060 Ti, 12 CPU, 32 GB RAM)

| вход | ценников | вариант | время | ~per crop |
|---|---:|---|---:|---:|
| `frame_000250.jpg` 4K | 15 | sequential (1) | 425 s | 28 s |
| `frame_000250.jpg` 4K | 15 | `LLM_CONCURRENCY=4` | **~90–110 s** | **~6–7 s** |

Куда уходит время:
- YOLO на CPU — линейно по кадрам, ~10–15 fps на 4K
- Qwen3-VL AWQ — ~25–30 tok/s output (warning `awq quantization is not fully optimized yet`)
- xgrammar JSON-schema constraint — добавляет CPU latency
- препроцессинг изображений в Qwen3-VL processor — съедает почти все CPU-ядра контейнера

Дальнейшие рычаги (не применены, но протестированы как вектор):
1. `LLM_CONCURRENCY=8` — рискует упереться в KV cache (`max_running_requests` SGLang)
2. `LLM_USE_JSON_SCHEMA=false` — снимает xgrammar; JSON парсится регулярками в `postprocess.extract_json_object`, потери редки
3. Resize кропов до ~512 px по короткой стороне перед отправкой в LLM — урежет vision-токены и CPU препроцессинг (правка `llm_client._extract_openai_vision`)

---

## 9. Тушить/перезапускать

```powershell
# полностью остановить
docker rm -f price-pipeline qwen-sglang

# рестарт только pipeline (после правок кода)
docker rm -f price-pipeline
docker build -t price-pipeline-api-sglang .\price_pipeline_api_sglang
# затем запустить как в разделе 3.4

# рестарт SGLang (веса кэшированы в ~/.cache/huggingface — повторно не качаются)
docker rm -f qwen-sglang
.\qwen_sglang_server\run_sglang.ps1
```

---

## 10. Известные проблемы и обходы

| симптом | причина | фикс |
|---|---|---|
| `docker run --rm ... sglang` → молча `Exited (0)`, логов нет | --rm удаляет контейнер при крэше | использовать `-d` без `--rm`; смотреть `docker logs` |
| SGLang падает `AttributeError: 'int' object has no attribute 'items'` | флаг `--limit-mm-data-per-request 1` ловит несоответствие тип int vs dict | убрать флаг или передать `'{"image":1}'` |
| pipeline стартует и сразу `FileNotFoundError: YOLO weights not found: C:/Program Files/Git/models/...` | Git Bash мангает Linux-пути в env-переменных | `MSYS_NO_PATHCONV=1 docker run ...` или вызывать из PowerShell |
| HF качает 7 GB часами | rate-limit для анонимных запросов | задать `$env:HF_TOKEN` и перезапустить SGLang |
| `nvidia-smi` в контейнере не видит GPU | NVIDIA Container Toolkit не настроен | Docker Desktop → Settings → Resources → Enable GPU |
| Out of memory при инференсе | KV cache (`--mem-fraction-static`) слишком жадный | снизить до `0.70` |
