#!/usr/bin/env bash
# mediaflow service control
#
# Usage:
#   scripts/ctl.sh status
#   scripts/ctl.sh start   [all|docker|whisper|watcher|worker|diarize]
#   scripts/ctl.sh stop    [all|docker|whisper|watcher|worker|diarize]
#   scripts/ctl.sh restart [all|api|web|redis|whisper|watcher|worker|diarize]
#   scripts/ctl.sh rebuild [all|docker|api|web]
#   scripts/ctl.sh logs    [api|web|redis|whisper|watcher|worker|diarize]
#
# 'all'    = docker + whisper + watcher + worker  (default for start/stop/restart)
# 'diarize' must be started explicitly (optional, heavy model)

set -euo pipefail
cd "$(dirname "$0")/.."

PID_DIR="data/pids"
LOG_DIR="data/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# ── Compose detection ──────────────────────────────────────────
if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif podman compose version &>/dev/null 2>&1; then
    COMPOSE="podman compose"
elif command -v podman-compose &>/dev/null; then
    COMPOSE="podman-compose"
else
    echo "ERROR: docker compose / podman compose not found" >&2; exit 1
fi

# ── Colour helpers ─────────────────────────────────────────────
_ok()   { printf '\033[32m✓\033[0m  %s\n' "$*"; }
_fail() { printf '\033[31m✗\033[0m  %s\n' "$*"; }
_info() { printf '\033[36m→\033[0m  %s\n' "$*"; }
_head() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ── Process helpers ────────────────────────────────────────────
_pid_file()  { echo "$PID_DIR/$1.pid"; }

_is_running() {
    local f; f=$(_pid_file "$1")
    [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null
}

_start_bg() {
    local name="$1"; shift
    if _is_running "$name"; then
        _info "$name already running (pid $(cat "$(_pid_file "$name")")"
        return
    fi
    nohup "$@" >> "$LOG_DIR/$name.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$(_pid_file "$name")"
    disown "$pid"
    sleep 1
    if _is_running "$name"; then
        _ok "$name started (pid $pid)"
    else
        _fail "$name failed to start — check: ctl.sh logs $name"
        rm -f "$(_pid_file "$name")"
        return 1
    fi
}

_stop_proc() {
    local name="$1"
    local f; f=$(_pid_file "$name")
    if _is_running "$name"; then
        kill "$(cat "$f")" 2>/dev/null || true
        sleep 1
        kill -0 "$(cat "$f")" 2>/dev/null && kill -9 "$(cat "$f")" 2>/dev/null || true
        rm -f "$f"
        _ok "$name stopped"
    else
        rm -f "$f"
        _info "$name not running"
    fi
}

_ensure_venv() {
    if [[ ! -f venv/bin/activate ]]; then
        _info "Creating venv..."
        python3 -m venv venv
    fi
    # shellcheck disable=SC1091
    source venv/bin/activate
    pip install -q -r requirements.txt
    [[ -f requirements-worker.txt ]] && pip install -q -r requirements-worker.txt
}

# ── Health checks ──────────────────────────────────────────────
_http_ok() { curl -sf "$1" &>/dev/null; }

_wait_ready() {
    local url="$1" name="$2" max="${3:-30}"
    local n=0
    while ! _http_ok "$url" && [[ $n -lt $max ]]; do sleep 1; ((n++)); done
    _http_ok "$url" && _ok "$name ready" || { _fail "$name not ready after ${max}s"; return 1; }
}

# ── Commands ──────────────────────────────────────────────────

do_status() {
    _head "Docker containers"
    $COMPOSE ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null \
        | tail -n +2 | while IFS= read -r line; do _info "$line"; done

    _head "Native processes"
    for svc in whisper watcher worker; do
        _is_running "$svc" \
            && _ok  "$svc  pid=$(cat "$(_pid_file "$svc")")" \
            || _fail "$svc  not running"
    done
    _is_running "diarize" \
        && _ok  "diarize  pid=$(cat "$(_pid_file diarize)")" \
        || _info "diarize  not running (optional)"
    _is_running "asr" \
        && _ok  "asr          pid=$(cat "$(_pid_file asr)")" \
        || _info "asr          not running (optional — alternative to whisper)"
    _is_running "gpu-exporter" \
        && _ok  "gpu-exporter pid=$(cat "$(_pid_file gpu-exporter)")" \
        || _info "gpu-exporter not running (optional — needs sudoers for powermetrics)"

    _head "Health"
    _http_ok http://localhost:8080/health            && _ok "api    :8080" || _fail "api    :8080"
    _http_ok http://localhost:3000/health            && _ok "web    :3000" || _fail "web    :3000"
    _http_ok http://localhost:9001/health            && _ok "whisper:9001" || _fail "whisper:9001"
    _http_ok http://localhost:9000/minio/health/live && _ok "minio  :9000" || _fail "minio  :9000"
    _http_ok http://localhost:9003/health            && _ok "diarize:9003" || _info "diarize:9003 (optional)"
    _http_ok http://localhost:9004/health            && _ok "asr    :9004" || _info "asr    :9004 (optional)"
    _http_ok http://localhost:11434/api/tags         && _ok "ollama :11434" || _info "ollama :11434 (optional — needed only for llm.backend=ollama)"
}

do_start() {
    local svc="${1:-all}"
    [[ -f config.yaml ]] || { _fail "config.yaml missing — cp config.yaml.example config.yaml"; exit 1; }

    if [[ "$svc" == "all" || "$svc" == "docker" ]]; then
        _head "Starting Docker"
        $COMPOSE up -d
        _wait_ready http://localhost:8080/health "api :8080"
        _wait_ready http://localhost:3000/health "web :3000"
    fi

    if [[ "$svc" == "all" || "$svc" == "whisper" ]]; then
        _head "Starting Whisper"
        if [[ ! -d venv-whisper ]]; then
            _info "Creating venv-whisper..."
            python3 -m venv venv-whisper
            venv-whisper/bin/pip install --quiet -r whisper/requirements.txt
        fi
        WHISPER_MODEL=${WHISPER_MODEL:-mlx-community/whisper-medium-mlx} \
        _start_bg whisper venv-whisper/bin/uvicorn whisper.service:app --host 0.0.0.0 --port 9001
    fi

    if [[ "$svc" == "all" || "$svc" == "watcher" ]]; then
        _head "Starting watcher"
        _ensure_venv
        _start_bg watcher python -m pipeline.watcher
    fi

    if [[ "$svc" == "all" || "$svc" == "worker" ]]; then
        _head "Starting worker"
        _ensure_venv
        _start_bg worker python -m pipeline.worker
    fi

    if [[ "$svc" == "gpu-exporter" ]]; then
        _head "Starting gpu-exporter (Apple Silicon)"
        _ensure_venv
        _start_bg gpu-exporter python monitoring/gpu_exporter.py
    fi

    if [[ "$svc" == "diarize" ]]; then
        _head "Starting diarize"
        if [[ ! -d venv-diarize ]]; then
            _info "Creating venv-diarize..."
            python3 -m venv venv-diarize
            venv-diarize/bin/pip install --quiet -r diarize/requirements.txt
        fi
        _start_bg diarize venv-diarize/bin/uvicorn diarize.service:app --host 0.0.0.0 --port 9003
    fi

    if [[ "$svc" == "asr" ]]; then
        _head "Starting asr (Qwen3-ASR MLX)"
        if [[ ! -d venv-asr-mlx ]]; then
            _info "Creating venv-asr-mlx (first run downloads ~1.7 GB model)..."
            python3.11 -m venv venv-asr-mlx
            venv-asr-mlx/bin/pip install --quiet -r asr/requirements-mlx.txt
        fi
        _start_bg asr venv-asr-mlx/bin/uvicorn asr.service:app --host 0.0.0.0 --port 9004
    fi
}

do_stop() {
    local svc="${1:-all}"
    if [[ "$svc" == "all" || "$svc" == "watcher" ]]; then _stop_proc watcher; fi
    if [[ "$svc" == "all" || "$svc" == "worker"  ]]; then _stop_proc worker;  fi
    if [[ "$svc" == "all" || "$svc" == "whisper" ]]; then _stop_proc whisper; fi
    if [[ "$svc" == "all" || "$svc" == "diarize" ]]; then _stop_proc diarize; fi
    if [[ "$svc" == "asr" ]]; then _stop_proc asr; fi
    if [[ "$svc" == "all" || "$svc" == "docker"  ]]; then
        _head "Stopping Docker"
        $COMPOSE down && _ok "Docker stopped"
    fi
    case "$svc" in api|web|redis)
        $COMPOSE stop "$svc" && _ok "$svc stopped" ;;
    esac
}

do_restart() {
    local svc="${1:-all}"
    case "$svc" in
        api|web|redis)
            _head "Restarting $svc"
            $COMPOSE restart "$svc"
            [[ "$svc" == "api" ]] && _wait_ready http://localhost:8080/health "api :8080"
            [[ "$svc" == "web" ]] && _wait_ready http://localhost:3000/health "web :3000"
            return ;;
    esac
    do_stop "$svc"
    sleep 1
    do_start "$svc"
}

do_rebuild() {
    local svc="${1:-docker}"
    _head "Rebuilding $svc"
    _stop_proc watcher
    _stop_proc worker
    case "$svc" in
        all|docker)
            $COMPOSE build api web && $COMPOSE up -d
            _wait_ready http://localhost:8080/health "api :8080"
            _wait_ready http://localhost:3000/health "web :3000"
            ;;
        api|web)
            $COMPOSE build "$svc" && $COMPOSE up -d "$svc"
            [[ "$svc" == "api" ]] && _wait_ready http://localhost:8080/health "api :8080"
            [[ "$svc" == "web" ]] && _wait_ready http://localhost:3000/health "web :3000"
            ;;
        *) _fail "rebuild: must be all, docker, api, or web"; exit 1 ;;
    esac
    _head "Restarting watcher + worker"
    _ensure_venv
    _start_bg watcher python -m pipeline.watcher
    _start_bg worker  python -m pipeline.worker
}

do_logs() {
    local svc="${1:-api}"
    case "$svc" in
        api|web|redis)                    $COMPOSE logs -f "$svc" ;;
        watcher|worker|whisper|diarize|asr|gpu-exporter)   tail -f "$LOG_DIR/$svc.log" ;;
        *) _fail "unknown service: $svc"; exit 1 ;;
    esac
}

# ── Dispatch ──────────────────────────────────────────────────
CMD="${1:-status}"
SVC="${2:-}"

case "$CMD" in
    status)          do_status ;;
    start)           do_start   "${SVC:-all}" ;;
    stop)            do_stop    "${SVC:-all}" ;;
    restart)         do_restart "${SVC:-all}" ;;
    rebuild)         do_rebuild "${SVC:-docker}" ;;
    logs)            do_logs    "${SVC:-api}" ;;
    -h|--help|help)  sed -n '2,11p' "$0" ;;
    *)  _fail "unknown command: $CMD"
        echo "Usage: $0 {status|start|stop|restart|rebuild|logs} [service]"
        exit 1 ;;
esac
