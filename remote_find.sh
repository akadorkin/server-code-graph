#!/bin/bash
# Runs ON the server (piped via `ssh host bash -s`). Prints the files to collect, one per line.
# Read-only: it only lists files. Settings arrive as env vars from collect.sh.
: "${INCLUDE_DIRS:=/usr/local/bin /usr/local/sbin /usr/local/etc /opt /srv /root/bin /root/scripts}"
: "${EXCLUDE_NAMES:=venv .venv node_modules site-packages dist-packages __pycache__ .git build dist backups logs cache}"
: "${EXCLUDE_REGEX:=^$}"
prune=(); for n in $EXCLUDE_NAMES; do prune+=(-o -name "$n"); done
{
# 1) self-written code and docs
find $INCLUDE_DIRS \( -false "${prune[@]}" \) -prune -o -type f -size -400k -print 2>/dev/null |
while read -r f; do
  case "$f" in  # never pull obvious secret stores
    *.bak*|*.env|*/.env*|*/secrets*|*.key|*.pem|*.p12|*.pass|*/id_rsa*|*/id_ed25519*|*/authorized_keys|*/known_hosts|*.log|*.sqlite*|*.db) continue;;
  esac
  case "$f" in
    *.py|*.sh|*.md|*.cfg|*.yml|*.yaml|*.toml|*.sql|*.js|*.ts|*.tsx|*.go|*.rs|*.kt|*.vue|*.html|*.conf|*.service|*.timer|*.ini) echo "$f";;
    *) case "${f##*/}" in *.*) ;; *) head -c 64 "$f" 2>/dev/null | grep -qE '^#!.*(bash|sh|python)' && echo "$f";; esac;;
  esac
done
# 2) service wiring the config parser understands
find /etc/systemd/system -maxdepth 1 -type f \( -name '*.service' -o -name '*.timer' \) 2>/dev/null
find /etc/systemd/system -maxdepth 2 -path '*.d/*' -name '*.conf' -type f 2>/dev/null
ls /etc/haproxy/haproxy.cfg /etc/hosts /etc/crontab /etc/cron.d/* /var/spool/cron/crontabs/* 2>/dev/null
ls /etc/ufw/user.rules /etc/ufw/user6.rules /etc/nftables.conf /etc/docker/daemon.json 2>/dev/null
find /etc/nginx/sites-enabled /etc/nginx/conf.d -maxdepth 1 \( -type f -o -type l \) 2>/dev/null
find /opt /srv /root -maxdepth 4 \( -name 'docker-compose*.y*ml' -o -name 'compose*.y*ml' \) -size -200k 2>/dev/null
find /etc/wireguard /etc/amnezia /etc/xray /usr/local/etc/xray /etc/sysctl.d -maxdepth 3 -type f \
  \( -name '*.conf' -o -name '*.json' \) -size -300k 2>/dev/null
} | grep -vE '\.bak|/(backups?|trash[^/]*)/' | grep -vE "$EXCLUDE_REGEX" | sort -u
