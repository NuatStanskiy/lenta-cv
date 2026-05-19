# Qwen SGLang Server

Это не кастомная сборка. Используется официальный образ `lmsysorg/sglang:latest`.
Контейнер поднимает OpenAI-compatible API на порту `30000`.

## PowerShell запуск

```powershell
docker network create price-net

$env:HF_TOKEN="hf_xxxxxxxxxxxxxxxxx"

mkdir $HOME\.cache\huggingface -Force

docker run --rm `
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
    --mem-fraction-static 0.85 `
    --mm-max-concurrent-calls 1
```

Если контейнер ругается на память, уменьши:

```text
--mem-fraction-static 0.75
```

или добавь лимит пикселей:

```text
--mm-process-config '{"image":{"max_pixels":524288}}'
```

## Проверка readiness

```powershell
curl http://localhost:30000/health
```

## Прямой тест vision API

В PowerShell проще выполнить тест Python-скриптом из этого архива:

```powershell
python .\test_sglang_image.py C:\path\to\crop.jpg
```

## Что указывать в pipeline

Когда pipeline запущен в той же Docker-сети `price-net`, используй:

```text
LLM_API_URL=http://qwen-sglang:30000
LLM_ENDPOINT=/v1/chat/completions
LLM_MODEL=Vishva007/Qwen3-VL-8B-Instruct-W4A16-AutoRound-AWQ
```

Если pipeline запускается не в Docker-сети, а с хоста, используй:

```text
LLM_API_URL=http://host.docker.internal:30000
```

Если pipeline будет на HF Spaces, а SGLang на твоём компьютере/сервере с белым IP:

```text
LLM_API_URL=http://<PUBLIC_IP>:30000
```

В этом случае лучше закрыть порт reverse proxy/API key/firewall allowlist, потому что SGLang сам по себе обычно не требует авторизации.
