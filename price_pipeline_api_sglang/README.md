---
title: Price Pipeline API SGLang
sdk: docker
app_port: 7860
---

# Price Pipeline API + SGLang

Первый контейнер. Его можно поднять на Hugging Face Spaces или на обычном сервере.

Что делает `/process`:

1. принимает `image/*` или `video/*` как multipart `file`;
2. поворачивает кадр/кадры, по умолчанию `ROTATE=ccw`, как в ноутбуках;
3. опционально прогоняет через fisheye undistort;
4. запускает YOLO `price-tag-detection`;
5. режет найденные bbox ценников с padding;
6. для видео использует BoT-SORT/ByteTrack и выбирает лучший кроп трека по Laplacian sharpness;
7. отправляет кропы в SGLang OpenAI-compatible API `/v1/chat/completions`;
8. возвращает CSV.

Corner-detection/поиск углов не используется.

## Эндпоинты

```text
GET  /health
POST /process       multipart file=image_or_video -> text/csv
POST /process_json  multipart file=image_or_video -> json rows, удобно для дебага
```

## Локальный запуск вместе с SGLang

Сначала подними SGLang-контейнер, например с именем `qwen-sglang` и портом `30000`.
Потом собери этот pipeline-контейнер:

```bash
docker build -t price-pipeline-api-sglang .
```

Запуск в той же Docker-сети:

```bash
docker run --rm \
  --name price-pipeline \
  --network price-net \
  -p 7860:7860 \
  -e MODEL_PATH=/models/price-tag-detection.pt \
  -e DEVICE=cpu \
  -e LLM_BACKEND=openai_vision \
  -e LLM_API_URL=http://qwen-sglang:30000 \
  -e LLM_ENDPOINT=/v1/chat/completions \
  -e LLM_MODEL=Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ \
  -e ROTATE=none \
  -e SAVE_DEBUG_CROPS=true \
  -v /absolute/path/to/best.pt:/models/price-tag-detection.pt:ro \
  -v "$PWD/debug_crops:/tmp/price_pipeline_debug" \
  price-pipeline-api-sglang
```

Проверка без LLM:

```bash
curl -s -F "file=@test.jpg" "http://localhost:7860/process_json?skip_llm=true"
```

Проверка всего пайплайна:

```bash
curl -o result.csv -F "file=@test.jpg" http://localhost:7860/process
curl -o result.csv -F "file=@test.mp4" http://localhost:7860/process
```

## Переменные окружения

### Базовые

| ENV | default | смысл |
|---|---:|---|
| `MODEL_PATH` | `/models/price-tag-detection.pt` | путь к `.pt` весам детектора |
| `DEVICE` | `cpu` | `cpu`, `cuda`, `cuda:0` |
| `API_KEY` | пусто | если задан, первый контейнер требует `X-API-Key` на входе |

### LLM / SGLang

| ENV | default | смысл |
|---|---:|---|
| `LLM_BACKEND` | `openai_vision` | `openai_vision` для SGLang/vLLM/OpenAI-compatible API или `legacy_extract` для старого `/extract` |
| `LLM_API_URL` | `http://127.0.0.1:30000` | URL SGLang без trailing slash |
| `LLM_ENDPOINT` | `/v1/chat/completions` | OpenAI-compatible chat endpoint |
| `LLM_MODEL` | `Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ` | имя модели в OpenAI-запросе |
| `LLM_API_KEY` | пусто | Bearer/X-API-Key, если SGLang закрыт прокси |
| `LLM_TIMEOUT_SEC` | `180` | timeout одного crop OCR запроса |
| `LLM_MAX_TOKENS` | `768` | max tokens для JSON ответа |
| `LLM_TEMPERATURE` | `0.0` | температура генерации |
| `LLM_TOP_P` | `0.95` | top_p |
| `LLM_USE_JSON_SCHEMA` | `true` | передавать `response_format=json_schema` в SGLang |
| `LLM_ENABLE_THINKING` | `false` | передавать `chat_template_kwargs.enable_thinking` |

### Детекция/видео

| ENV | default | смысл |
|---|---:|---|
| `CONF_THRESHOLD` | `0.30` | confidence YOLO |
| `IOU_THRESHOLD` | `0.50` | NMS IoU |
| `CROP_PADDING` | `0.10` | расширение bbox перед кропом |
| `EDGE_MARGIN` | `15` | отбрасывать боксы, прилипшие к краям |
| `FRAME_INTERVAL_SEC` | `0.20` | шаг кадров для видео |
| `ENABLE_TRACKING` | `true` | включить `model.track` для видео |
| `TRACKER_TYPE` | `botsort` | `botsort` или `bytetrack` |
| `TOP_K_PER_TRACK` | `1` | сколько лучших кропов брать с одного трека |

### Orientation / fisheye

| ENV | default | смысл |
|---|---:|---|
| `ROTATE` | `ccw` | `none`, `cw`, `ccw`, `180` |
| `FISHEYE_ENABLED` | `false` | включить OpenCV fisheye undistort |
| `FISHEYE_K` | пусто | JSON 3x3 camera matrix |
| `FISHEYE_D` | пусто | JSON distortion vector из 4 чисел |
| `FISHEYE_DIM` | пусто | JSON `[width,height]` размера калибровки |
| `FISHEYE_BALANCE` | `0.0` | balance для `estimateNewCameraMatrixForUndistortRectify` |
| `FISHEYE_FOV_SCALE` | `1.0` | fov scale |

Пример fisheye env:

```bash
-e FISHEYE_ENABLED=true \
-e 'FISHEYE_K=[[1000,0,960],[0,1000,540],[0,0,1]]' \
-e 'FISHEYE_D=[-0.05,0.001,0.0,0.0]' \
-e 'FISHEYE_DIM=[1920,1080]'
```

Если матрицы нет, fisheye-этап становится no-op, но поворот всё равно применяется.

## HF Spaces

Для Docker Space достаточно положить этот архив в репозиторий Space. В Settings → Variables/Secrets нужно задать минимум:

```text
MODEL_PATH=/models/price-tag-detection.pt
DEVICE=cpu
LLM_BACKEND=openai_vision
LLM_API_URL=http://<public-ip-your-local-sglang>:30000
LLM_ENDPOINT=/v1/chat/completions
LLM_MODEL=Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ
```

Вес `price-tag-detection.pt` лучше примонтировать/добавить отдельно. В архив он намеренно не включён.
