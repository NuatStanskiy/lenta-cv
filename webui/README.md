# Price-tag pipeline — Web UI

Самостоятельная папка `webui/`, которую можно перенести на сервер и поднять отдельно. Внутри — крохотный FastAPI-прокси, который раздаёт статический UI и форвардит запросы в `price-pipeline` (`POST /process`, `POST /process_json`, `GET /health`).

```
Browser
  │
  ▼
webui :8080  (твой сервер)
  │  GET /         → static/index.html
  │  POST /api/process    ─┐
  │  POST /api/process_csv ─┤
  │  GET  /api/health      ─┤
  └─────────────────────────┘
              │
              ▼ HTTP forward
       PIPELINE_API_URL  (например http://host.docker.internal:7860)
              │
              ▼ через SSH reverse-tunnel
        price-pipeline :7860 (твоя локальная машина)
```

## Что есть в UI

- drag-and-drop загрузка `image/*` или `video/*`
- кнопка «Обработать» → шлёт `/api/process` → рендерит:
  - превью с bbox-ами (canvas, повёрнутый CCW в тон pipeline ROTATE=ccw)
  - таблицу извлечённых полей (12 LLM-полей)
  - подсветка строки таблицы → выделение bbox жёлтым
- кнопка «Скачать CSV» → `/api/process_csv` → файл в формате `data/25_2-10/25_2-10.csv` (29 колонок)
- индикатор состояния пайплайна сверху (зелёный/красный), пингуется раз в 15 с

## Локальный запуск (рядом с пайплайном)

```powershell
cd webui

# 1. через Docker (рекомендую)
docker build -t price-pipeline-webui .
docker run --rm -p 8080:8080 `
  -e PIPELINE_API_URL=http://host.docker.internal:7860 `
  --add-host=host.docker.internal:host-gateway `
  price-pipeline-webui

# или через docker-compose
docker compose up --build
```

Если webui крутится в той же docker-сети, что pipeline:

```powershell
docker run --rm -p 8080:8080 `
  --network price-net `
  -e PIPELINE_API_URL=http://price-pipeline:7860 `
  price-pipeline-webui
```

Без Docker:

```powershell
cd webui
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PIPELINE_API_URL = "http://localhost:7860"
uvicorn server:app --host 0.0.0.0 --port 8080 --reload
```

Открыть: <http://localhost:8080/>

## Деплой на сервер + SSH-туннель к локальному пайплайну

Кейс: pipeline крутится у тебя на локальном Windows/WSL (где RTX 4060 Ti), а UI хочется на сервере с публичным IP.

### Вариант A — pipeline на хосте сервера через reverse-SSH (рекомендую)

С локальной машины пробрасываем порт пайплайна на сервер:

```powershell
# с твоего ноутбука (где запущен price-pipeline :7860)
ssh -N -R 7860:localhost:7860 deploy@your-server.example.com
```

На сервере теперь `localhost:7860` = твой локальный pipeline. Поднимаем webui:

```bash
# на сервере
git pull   # либо scp -r webui deploy@your-server:/srv/webui
cd /srv/webui
docker build -t price-pipeline-webui .
docker run -d --name webui \
  -p 8080:8080 \
  -e PIPELINE_API_URL=http://host.docker.internal:7860 \
  --add-host=host.docker.internal:host-gateway \
  --restart unless-stopped \
  price-pipeline-webui
```

Открыть: `http://your-server.example.com:8080/` (или через nginx с TLS).

Чтобы reverse-tunnel держался: `autossh -M 0 -N -R 7860:localhost:7860 deploy@your-server.example.com` со скриптом-обёрткой/`pm2`/`nssm`, либо настроить `~/.ssh/config`:

```
Host pipeline-tunnel
  HostName your-server.example.com
  User deploy
  RemoteForward 7860 localhost:7860
  ServerAliveInterval 30
  ServerAliveCountMax 3
  ExitOnForwardFailure yes
```

В `/etc/ssh/sshd_config` на сервере нужно `GatewayPorts no` (значение по умолчанию) — туннель будет слушать только `127.0.0.1` сервера, что и требуется (webui ходит в localhost через `host.docker.internal`).

### Вариант B — webui и pipeline в одной docker-сети

Если pipeline тоже задеплоен на этот сервер (например, на отдельный GPU-инстанс рядом):

```bash
docker network create price-net   # если ещё нет
# pipeline уже запущен в сети price-net под именем price-pipeline
docker run -d --name webui \
  --network price-net \
  -p 8080:8080 \
  -e PIPELINE_API_URL=http://price-pipeline:7860 \
  --restart unless-stopped \
  price-pipeline-webui
```

### Вариант C — pipeline доступен по публичному URL

```bash
docker run -d --name webui \
  -p 8080:8080 \
  -e PIPELINE_API_URL=https://pipeline.internal.example.com \
  -e PIPELINE_API_KEY=secret-key-if-protected \
  price-pipeline-webui
```

Если пайплайн закрыт `API_KEY` — webui добавит `X-API-Key` в каждый проксированный запрос.

## ENV прокси

| ENV | default | смысл |
|---|---|---|
| `PIPELINE_API_URL` | `http://localhost:7860` | базовый URL пайплайна |
| `PIPELINE_API_KEY` | пусто | `X-API-Key` для пайплайна с `API_KEY` |
| `REQUEST_TIMEOUT` | `1800` | таймаут одного `/process` в секундах |

## Поставить за nginx + TLS

Минимальный конфиг (для большого видео важно поднять `client_max_body_size` и таймауты):

```nginx
server {
  listen 443 ssl http2;
  server_name pipeline-ui.example.com;
  ssl_certificate /etc/letsencrypt/live/.../fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/.../privkey.pem;

  client_max_body_size 200m;
  proxy_read_timeout 1800;
  proxy_send_timeout 1800;

  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
  }
}
```

## Структура

```
webui/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── server.py             # FastAPI proxy + static mount
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js            # drag-drop + canvas с bbox + table
└── README.md
```

## Траблшутинг

| симптом | причина | фикс |
|---|---|---|
| статус-плашка «pipeline: unreachable» | proxy не достучался до `PIPELINE_API_URL` | `docker logs webui` покажет точную ошибку; проверить туннель `curl http://localhost:7860/health` прямо на сервере |
| картинка повёрнута на 90° от ожидания | UI рисует canvas повёрнутым CCW (под `ROTATE=ccw`). Если pipeline запущен с `ROTATE=none/cw/180`, превью разойдётся с bbox-ами | подровнять `ROTATE` на pipeline или поправить `renderPreview()` в `app.js` |
| Cyrillic в скачанном CSV крокозябрит в Excel | UTF-8 без BOM | webui сохраняет с BOM (`utf-8-sig`), Excel должен открыть корректно. Если нет — переоткрыть «Data → From Text/CSV → UTF-8» |
| upload падает с 413 (nginx) | `client_max_body_size` мал | поднять до 200m+ |
| upload зависает на больших видео | nginx закрывает соединение | `proxy_read_timeout 1800` на nginx и `REQUEST_TIMEOUT=1800` на webui |
| reverse-SSH туннель отваливается | сеть/сон ноутбука | использовать `autossh` + `ServerAliveInterval` |
