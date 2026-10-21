#!/bin/sh
set -eu

DATA_DIR="${CINEFLOW_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

# 默认以 root 运行，确保能写入挂载的媒体目录。
# 如果设置了 PUID / PGID，则以对应 UID/GID 运行（更安全，推荐）。
if [ -n "${PUID:-}" ] && [ -n "${PGID:-}" ] && [ "$(id -u)" = "0" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" cineflow
    fi
    if ! id -u "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin cineflow
    fi
    chown -R "$PUID:$PGID" "$DATA_DIR"
    exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
