# llama-server API Quick Reference

## Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/completion` | Text completion from a prompt |
| POST | `/tokenize` | Tokenize text |
| POST | `/detokenize` | Convert tokens to text |
| POST | `/apply-template` | Apply chat template to messages (returns formatted prompt string) |
| POST | `/embedding` | Generate embeddings (non-OAI) |
| POST | `/reranking` | Rerank documents by query |
| POST | `/infill` | Code infilling |
| GET | `/props` | Get server global properties |
| POST | `/props` | Change server global properties |
| GET | `/slots` | Current slot processing state |
| GET | `/metrics` | Prometheus-compatible metrics |
| POST | `/slots/{id}?action=save` | Save slot prompt cache to file |
| POST | `/slots/{id}?action=restore` | Restore slot prompt cache from file |
| POST | `/slots/{id}?action=erase` | Erase slot prompt cache |
| GET | `/lora-adapters` | List LoRA adapters |
| POST | `/lora-adapters` | Set LoRA adapters |

## OpenAI-Compatible Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/models` | Model info |
| POST | `/v1/completions` | Completions |
| POST | `/v1/chat/completions` | Chat completions |
| POST | `/v1/responses` | Responses API |
| POST | `/v1/embeddings` | Embeddings |
| POST | `/v1/messages` | Anthropic-compatible messages |
| POST | `/v1/messages/count_tokens` | Token counting |

## Multi-Model Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/models` | List available models |
| POST | `/models/load` | Load a model |
| POST | `/models/unload` | Unload a model |
