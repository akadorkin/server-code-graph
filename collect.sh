#!/bin/bash
# collect.sh [config.env] — pull code and configs from every host in HOSTS_FILE into $OUT/raw/<name>/.
# Read-only on the servers: `find`, `tar -c`, `docker inspect`. Output is NOT scrubbed yet — run build.sh next.
set -u
cd "$(dirname "$0")"; CFG=${1:-config.env}; [ -f "$CFG" ] || { echo "no $CFG (copy config.example.env)"; exit 1; }
. "./$CFG"; export INCLUDE_DIRS EXCLUDE_NAMES EXCLUDE_REGEX
RAW=$OUT/raw; mkdir -p "$RAW"; chmod 700 "$OUT"

one() {  # one <name> <ssh-target> [jump]
  local h=$1 c=$2 O=(-o ConnectTimeout=10 -o BatchMode=yes) S="sudo -n" d=$RAW/$1
  [ -n "${3:-}" ] && O+=(-J "$3")
  [ "${c%%@*}" = root ] && S=""
  rm -rf "$d"; mkdir -p "$d"
  local envs="INCLUDE_DIRS='$INCLUDE_DIRS' EXCLUDE_NAMES='$EXCLUDE_NAMES' EXCLUDE_REGEX='$EXCLUDE_REGEX'"
  timeout 180 ssh "${O[@]}" "$c" "$S env $envs bash -s" < remote_find.sh > "$d.list" 2>/dev/null
  [ -s "$d.list" ] && timeout 600 ssh "${O[@]}" "$c" "$S tar -chf - -T - 2>/dev/null" < "$d.list" | tar -C "$d" -xf - 2>/dev/null
  timeout 60 ssh "${O[@]}" "$c" "$S sh -s" < remote_docker.sh > "$d/_containers.jsonl" 2>/dev/null
  [ -s "$d/_containers.jsonl" ] || rm -f "$d/_containers.jsonl"
  echo "$h: $(find "$d" -type f | wc -l | tr -d ' ') files"
}
export -f one; export RAW
grep -vE '^\s*(#|$)' "$HOSTS_FILE" | xargs -P "${PARALLEL:-4}" -L1 bash -c 'one "$@"' _
