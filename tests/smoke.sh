#!/bin/bash
# Offline smoke test: fake "collected" hosts -> layout -> clean_secrets -> cfg_graph. No ssh, no servers.
# If graphify is installed, also runs the full build.sh against the fake data.
set -eu
cd "$(dirname "$0")/.."; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; [ -z "${KEEP:-}" ] || trap - EXIT
python3 -I clean_secrets.py --self-test

R=$T/out/raw
mkdir -p $R/node-a/etc/systemd/system $R/node-a/usr/local/bin $R/node-a/etc/haproxy $R/node-a/opt/app \
         $R/node-a/etc/wireguard $R/exit-de-1/etc/nginx/sites-enabled $R/exit-de-1/usr/local/bin $T/repo
printf '#!/bin/bash\necho backup\n' > $R/node-a/usr/local/bin/backup
printf '[Unit]\nDescription=Nightly backup\n[Service]\nExecStart=/usr/local/bin/backup\n' > $R/node-a/etc/systemd/system/backup.service
printf '[Timer]\nOnCalendar=daily\n' > $R/node-a/etc/systemd/system/backup.timer
printf 'frontend tls\n  bind :443\n  use_backend be_exit if { req.ssl_sni -i app.example.com }\nbackend be_exit\n  server s1 203.0.113.20:443\nlisten stats\n  stats auth admin:Adm1nPw\n' > $R/node-a/etc/haproxy/haproxy.cfg
printf 'services:\n  app:\n    image: nginx:1.27\n    environment:\n      - DB_PASSWORD=hunter22\n' > $R/node-a/opt/app/docker-compose.yml
printf 'BOT_TOKEN=123456789:AAHfakefakefakefakefakefakefakefake12\n' > $R/node-a/opt/app/.env
printf '[Interface]\nPrivateKey = %s\n[Peer]\nEndpoint = exit-de-1.example.org:51820\n' "$(printf 'aB3%.0s' {1..14})c=" > $R/node-a/etc/wireguard/wg0.conf
printf '{"name":"/app-app-1","image":"nginx:1.27","state":"running","restart":"always","workdir":"/opt/app","ports":{"80/tcp":[{"HostPort":"8080"}]},"net":"bridge","mounts":["/opt/app/data"]}\n' > $R/node-a/_containers.jsonl
printf 'server { server_name exit.example.org; location / { proxy_pass http://203.0.113.10:8080; } }\n' > $R/exit-de-1/etc/nginx/sites-enabled/site
# a script that also lives in a git repo -> must be deduplicated, yet still linked from its unit
printf 'def main():\n    print("sync")\n' > $T/repo/sync.py
cp $T/repo/sync.py $R/exit-de-1/usr/local/bin/sync.py
mkdir -p $R/exit-de-1/etc/systemd/system
printf '[Service]\nExecStart=/usr/bin/python3 /usr/local/bin/sync.py\n' > $R/exit-de-1/etc/systemd/system/sync.service
git -C $T/repo init -q && git -C $T/repo add . && git -C $T/repo -c user.name=t -c user.email=t@example.com commit -qm x

printf 'node-a admin@203.0.113.10\nexit-de-1 admin@exit-de-1.example.org\n' > $T/hosts.txt
C=$T/out/corpus
python3 -I layout.py $R $C $T/repo
python3 -I clean_secrets.py $C
! grep -rqE 'Adm1nPw|hunter22|AAHfake|aB3aB3' $C || { echo "FAIL: secret survived"; exit 1; }
[ ! -e $C/nodes/node-a/opt/app/.env ] || { echo "FAIL: .env kept"; exit 1; }
[ ! -e $C/nodes/exit-de-1/usr/local/bin/sync.py ] || { echo "FAIL: git duplicate kept"; exit 1; }
[ -e $C/nodes/node-a/usr/local/bin/backup.sh ] || { echo "FAIL: extension hint"; exit 1; }
python3 -I cfg_graph.py $C $T/hosts.txt $T/g.json
python3 - $T/g.json <<'PY'
import json, sys
g = json.load(open(sys.argv[1])); E = {(l['source'], l['relation'], l['target']) for l in g['links']}
need = [('unit_node_a_backup_service', 'executes', 'nodes_node_a_usr_local_bin_backup'),
        ('unit_node_a_backup_timer', 'triggers', 'unit_node_a_backup_service'),
        ('unit_exit_de_1_sync_service', 'executes', 'git_repo_sync'),
        ('hap_node_a_backend_be_exit', 'proxies_to', 'addr_203_0_113_20'),
        ('dom_app_example_com', 'served_by', 'hap_node_a_frontend_tls'),
        ('wg_node_a_etc_wireguard_wg0_conf', 'tunnels_to', 'host_exit_de_1'),
        ('nginx_exit_de_1_site', 'proxies_to', 'host_node_a'),
        ('ctr_node_a_app_app_1', 'part_of', 'compose_node_a_opt_app'),
        ('compose_node_a_opt_app', 'uses', 'img_nginx')]
miss = [e for e in need if e not in E]
assert not miss, f'missing edges: {miss}\nhave: {sorted(E)}'
print(f'cfg_graph: ok ({len(g["nodes"])} nodes, {len(E)} edges)')
PY
if command -v graphify >/dev/null; then
  printf 'HOSTS_FILE=%s\nOUT=%s\nGIT_DIRS=%s\n' $T/hosts.txt $T/out $T/repo > $T/config.env
  cp $T/config.env ./.smoke.env && trap 'rm -f ./.smoke.env; [ -n "${KEEP:-}" ] || rm -rf "$T"' EXIT
  ./build.sh .smoke.env | tail -1
  python3 -c "import json,sys; g=json.load(open(sys.argv[1])); ids={n['id'] for n in g['nodes']}; assert 'unit_node_a_backup_service' in ids and 'git_repo_sync' in ids, sorted(ids)[:40]; print('build.sh: ok', len(ids), 'nodes')" $T/out/corpus/graphify-out/graph.json
fi
echo SMOKE OK${KEEP:+ (kept $T)}
