.PHONY: help start stop restart status \
        start-docker start-whisper start-watcher start-diarize \
        stop-docker stop-whisper stop-watcher stop-diarize \
        restart-all restart-docker restart-whisper restart-watcher restart-api restart-web \
        logs logs-api logs-web logs-redis logs-watcher logs-whisper logs-diarize \
        rebuild rebuild-api rebuild-web

CTL := scripts/ctl.sh

# ── Default ───────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  mediaflow — service control"
	@echo ""
	@echo "  Core"
	@echo "    make start            start all services (Docker + Whisper + Watcher)"
	@echo "    make stop             stop all services"
	@echo "    make restart          restart all services"
	@echo "    make status           show status of all services"
	@echo ""
	@echo "  Individual services"
	@echo "    make start-docker     Redis + API + Web (Docker)"
	@echo "    make start-whisper    Whisper transcription service :9001"
	@echo "    make start-watcher    Pipeline watcher (watches 1_input/)"
	@echo "    make start-diarize    Speaker diarization service :9003 (optional)"
	@echo ""
	@echo "    make stop-docker / stop-whisper / stop-watcher / stop-diarize"
	@echo ""
	@echo "  Restart individual"
	@echo "    make restart-api      Restart API container"
	@echo "    make restart-web      Restart Web container"
	@echo "    make restart-whisper  Restart Whisper service"
	@echo "    make restart-watcher  Restart pipeline watcher"
	@echo ""
	@echo "  Logs"
	@echo "    make logs             API logs (default)"
	@echo "    make logs-api / logs-web / logs-redis"
	@echo "    make logs-watcher / logs-whisper / logs-diarize"
	@echo ""
	@echo "  Build"
	@echo "    make rebuild          Rebuild Docker images + restart"
	@echo "    make rebuild-api      Rebuild API image only"
	@echo "    make rebuild-web      Rebuild Web image only"
	@echo ""
	@echo "  External (manage manually)"
	@echo "    ollama serve          Ollama LLM service :11434"
	@echo ""

# ── Core ──────────────────────────────────────────────────────────────────────

start:
	$(CTL) start all

stop:
	$(CTL) stop all

restart:
	$(CTL) restart all

status:
	$(CTL) status

# ── Individual start ──────────────────────────────────────────────────────────

start-docker:
	$(CTL) start docker

start-whisper:
	$(CTL) start whisper

start-watcher:
	$(CTL) start watcher

start-diarize:
	$(CTL) start diarize

# ── Individual stop ───────────────────────────────────────────────────────────

stop-docker:
	$(CTL) stop docker

stop-whisper:
	$(CTL) stop whisper

stop-watcher:
	$(CTL) stop watcher

stop-diarize:
	$(CTL) stop diarize

# ── Restart individual ────────────────────────────────────────────────────────

restart-all:
	$(CTL) restart all

restart-docker:
	$(CTL) restart docker

restart-api:
	$(CTL) restart api

restart-web:
	$(CTL) restart web

restart-whisper:
	$(CTL) restart whisper

restart-watcher:
	$(CTL) restart watcher

# ── Logs ──────────────────────────────────────────────────────────────────────

logs:
	$(CTL) logs api

logs-api:
	$(CTL) logs api

logs-web:
	$(CTL) logs web

logs-redis:
	$(CTL) logs redis

logs-watcher:
	$(CTL) logs watcher

logs-whisper:
	$(CTL) logs whisper

logs-diarize:
	$(CTL) logs diarize

# ── Build ─────────────────────────────────────────────────────────────────────

rebuild:
	$(CTL) rebuild all

rebuild-api:
	$(CTL) rebuild api

rebuild-web:
	$(CTL) rebuild web
