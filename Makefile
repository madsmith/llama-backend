.PHONY: install-dev frontend dev run clean

install-dev:
	uv pip install -e .

frontend: frontend/node_modules
	cd frontend && pnpm build

frontend/node_modules: frontend/package.json
	cd frontend && pnpm install

dev: .venv/bin/llama-manager frontend/node_modules
	llama-manager --dev

run: .venv/bin/llama-manager frontend
	llama-manager

.venv/bin/llama-manager: pyproject.toml
	uv pip install -e .

clean:
	rm -rf frontend/dist frontend/node_modules
