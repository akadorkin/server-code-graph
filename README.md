# server-code-graph

Pull the self-written code and service configs off a set of Linux servers over ssh, scrub secrets,
and turn the result into a [graphify](https://github.com/Graphify-Labs/graphify) knowledge graph you can query:
*"which unit runs this script?"*, *"what proxies to exit-de-1?"*, *"where is this domain served?"*

It grew out of ad-hoc scripts used on a small fleet of proxy/VPN nodes (haproxy, nginx, xray, WireGuard,
docker compose, cron, systemd timers) where the "code" is spread over `/usr/local/bin`, `/opt`, unit files
and config, and half of it was copied by hand from git. This repo is the generic, cleaned-up version.

## Pipeline

```
 hosts.txt ──► collect.sh ──ssh──► each server (read-only: find, tar -c, docker inspect)
                  │  remote_find.sh lists: code under INCLUDE_DIRS, systemd units, haproxy.cfg,
                  │  nginx sites, compose files, crontabs, /etc/hosts, wireguard/xray json, ufw
                  ▼
           out/raw/<host>/...            ← UNSCRUBBED copies, mode 700
                  │
 build.sh ────────┤
                  ▼
   layout.py      git checkouts (GIT_DIRS) → corpus/git/<repo>
                  host files → corpus/nodes/<host>, skipping exact sha256 copies of repo files
                  corpus/.paths.json remembers host:/path → corpus file (also for skipped ones)
                  ▼
   clean_secrets.py   drop .env/*.key/*.pem…, redact tokens, passwords, private keys, xray ids
                  ▼
   graphify update corpus   AST pass over code (no LLM)  → graph.json
                  ▼
   cfg_graph.py   unit→script, timer→unit, cron→script, haproxy/nginx→backend host,
                  domain→frontend, wg/xray→peer host, container→compose→image
                  merged into graph.json
                  ▼
   graphify cluster-only    communities + GRAPH_REPORT.md
                  ▼
   graphify query "…" --graph out/corpus/graphify-out/graph.json
```

## Requirements

- `ssh` access to each host (key auth, `BatchMode`), root or passwordless `sudo -n`; optional bastion (`ssh -J`)
- `bash`, `tar`, `timeout` (GNU coreutils; on macOS `brew install coreutils`), `python3` ≥ 3.8 (stdlib only)
- [`graphify`](https://pypi.org/project/graphifyy/) CLI on `PATH` (`pipx install graphifyy`); it is also a Claude Code skill,
  so after a build you can ask Claude questions about the graph
- `git` if you use `GIT_DIRS`

## Install / configure

```bash
git clone <this repo> && cd server-code-graph
cp hosts.example.txt hosts.txt        # your hosts
cp config.example.env config.env      # paths, parallelism, git checkouts
```

`hosts.txt` — one host per line, `name  ssh-target  [jump-host]`:

```
node-a      admin@node-a.example.com
ingress-1   root@203.0.113.10
home-box    admin@192.0.2.20            bastion.example.com
```

`config.env` — see `config.example.env`: `INCLUDE_DIRS` (where your own code lives), `EXCLUDE_NAMES`
(pruned directory names: venvs, node_modules, backups…), `EXCLUDE_REGEX`, `GIT_DIRS`, `OUT`, `PARALLEL`.
Nothing about your hosts is hardcoded in the scripts; both real files are in `.gitignore`.

## Usage

```bash
./collect.sh            # ssh to every host, fill out/raw/   (read-only on the servers)
./build.sh              # layout → scrub → graphify → config edges → clusters
graphify query "what executes backup.sh" --graph out/corpus/graphify-out/graph.json
graphify explain "haproxy frontend tls @ingress-1" --graph out/corpus/graphify-out/graph.json
less out/corpus/graphify-out/GRAPH_REPORT.md
```

Re-run `collect.sh` after changing things on the servers and `build.sh` after changing the git repos.
For the semantic (LLM) pass over docs, use graphify itself (`/graphify` in Claude Code or `graphify extract`) on `out/corpus`;
this tool only does the deterministic part.

Tests (offline, no servers; uses graphify if installed):

```bash
python3 clean_secrets.py --self-test
bash tests/smoke.sh
```

## Files

| file | what |
|---|---|
| `collect.sh` | parallel collection over ssh (`xargs -P`), optional jump host |
| `remote_find.sh`, `remote_docker.sh` | run on the server via `bash -s`; list files / dump container metadata (no env vars) |
| `layout.py` | corpus layout + sha256 dedupe against git checkouts |
| `clean_secrets.py` | in-place secret scrubber, `--self-test` |
| `cfg_graph.py` | deterministic config parser → graph nodes/edges |
| `build.sh` | runs the build steps |
| `tests/smoke.sh` | fake hosts → full pipeline, asserts edges and that secrets are gone |

## Safety notes

- **The output is a map of your infrastructure.** Hostnames, addresses, ports, domains, tunnel peers, which
  service runs where. Even scrubbed, keep `out/` private: do not commit it, do not upload it, do not paste it into public issues.
- `out/raw/` holds **unscrubbed** copies (wireguard configs with private keys, compose files with passwords).
  `collect.sh` sets `out/` to mode 700; delete `out/raw` when you do not need it.
- The scrubber is pattern-based and errs toward redacting, but it cannot prove a tree is clean.
  Before sharing anything derived from the corpus, run `gitleaks dir out/corpus` / `trufflehog filesystem out/corpus` and read the hits.
- The tool never changes anything on the servers: it only runs `find`, `ls`, `head`, `tar -c` and `docker inspect`.
  It does use `sudo -n` on non-root targets so it can read root-owned configs.
- Collection skips obvious secret stores (`*.env`, `*.key`, `*.pem`, `secrets*`, ssh keys, sqlite) at the source;
  `clean_secrets.py` deletes them again if they get in some other way.

## Limitations

- Linux + systemd hosts; configs are parsed with regexes, not full grammars (haproxy/nginx includes, templated units
  `foo@.service`, and nginx `upstream` blocks are not followed).
- Addresses are linked to hosts only when they match a name or ssh-target in `hosts.txt`; other addresses stay as plain address nodes.
- Requires a graphify version that has `update` and `cluster-only` (tested with graphifyy 0.9.92).

## License

MIT, see `LICENSE`.

## Кратко по-русски

Инструмент собирает по ssh собственный код и конфиги с набора Linux-серверов (скрипты в `/usr/local`, `/opt`,
юниты systemd, haproxy, nginx, compose, cron, WireGuard/xray), убирает дубли того, что уже лежит в git (по sha256),
вычищает секреты и строит граф знаний graphify с рёбрами «юнит → скрипт», «таймер → юнит», «haproxy → хост», «домен → фронтенд».
Хосты и пути — в `hosts.txt` и `config.env` (примеры рядом). На серверах ничего не меняет, только читает.
Результат — карта инфраструктуры: держите `out/` закрытым, а `out/raw/` содержит данные до чистки секретов.
