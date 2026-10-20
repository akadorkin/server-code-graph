"""cfg_graph.py CORPUS HOSTS_FILE GRAPH_JSON — turn host configs into graph nodes/edges, merged into GRAPH_JSON.

Deterministic, no LLM. Reads every file layout.py recorded in CORPUS/.paths.json and understands:
  systemd units/timers (ExecStart -> script, Timer -> service, Requires/After), cron lines -> scripts,
  haproxy frontends/backends/servers/SNI, nginx server_name/proxy_pass, /etc/hosts pins,
  docker compose images, running containers (_docker/containers.jsonl), wireguard endpoints,
  xray outbounds, ufw rule counts.
Addresses that match a host from HOSTS_FILE become edges to that host's node, so the graph shows
which machine proxies, tunnels or pins DNS to which. If GRAPH_JSON exists (graphify node-link output)
the new nodes and links are appended to it; otherwise a fresh node-link file is written.
"""
import collections, json, os, re, sys

CORPUS, HOSTS, GRAPH = sys.argv[1:4]
sid = lambda s: re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')
file_id = lambda rel: sid(os.path.splitext(rel)[0])  # same id graphify's AST pass gives a file

names = {}  # address/alias -> host name
for line in open(HOSTS):
    f = line.split('#')[0].split()
    if len(f) >= 2:
        names[f[0]] = f[0]; names[f[1].split('@')[-1]] = f[0]
paths = json.load(open(os.path.join(CORPUS, '.paths.json')))
by_host = collections.defaultdict(dict)  # host -> {/abs/path: corpus rel}
for k, rel in paths.items():
    h, p = k.split(':', 1); by_host[h][p] = rel

N, E = {}, []
def node(i, label, sf, ft='concept'):
    N.setdefault(i, {'id': i, 'label': label, 'file_type': ft, 'source_file': sf, 'source_location': None, '_origin': 'cfg'})
    return i
def edge(a, b, rel, sf):
    E.append({'source': a, 'target': b, 'relation': rel, 'confidence': 'EXTRACTED', 'confidence_score': 1.0,
              'source_file': sf, 'source_location': None, 'weight': 1.0, '_origin': 'cfg'})
def host(h): return node('host_' + sid(h), h + ' (host)', f'nodes/{h}')
def addr(a, sf):
    a = re.sub(r'^\w+://', '', a.strip())
    m6 = re.match(r'^\[([0-9a-fA-F:]+)\]', a)
    hp = m6.group(1) if m6 else (a.split('/')[0] if a.split('/')[0].count(':') >= 2 else re.split(r'[:/]', a)[0])
    if hp in ('localhost', '::1', '0.0.0.0', '') or hp.startswith('127.') or hp.startswith('$'): return None
    h = names.get(hp)
    return host(h) if h else node('addr_' + sid(hp), hp, sf)
def read(rel): return open(os.path.join(CORPUS, rel), errors='replace').read()

for h, files in sorted(by_host.items()):
    hn = host(h)
    def script(path):  # a path on host h -> code node of the corpus file holding it
        rel = files.get(path)
        return node(file_id(rel), os.path.basename(path), rel, 'code') if rel else None
    def path_node(p, sf): return node('path_' + sid(h + p), f'{p} @{h}', sf)
    units = {os.path.basename(p): node(f'unit_{sid(h)}_{sid(os.path.basename(p))}', f'{os.path.basename(p)} @{h}', rel)
             for p, rel in files.items() if re.match(r'^/etc/systemd/system/[^/]+\.(service|timer)$', p)}
    for p, rel in sorted(files.items()):
        f = os.path.basename(p)
        if p.startswith('/etc/systemd/system/') and f in units:
            un = units[f]; txt = read(rel)
            txt += ''.join('\n' + read(r) for q, r in files.items() if q.startswith(p + '.d/'))
            edge(hn, un, 'runs', rel)
            d = re.search(r'^Description=(.+)$', txt, re.M)
            if d: N[un]['rationale'] = d.group(1)[:200]
            for m in re.finditer(r'^Exec(?:Start|StartPre|StartPost|Stop|Reload)=[-@+!:]*(\S+)(.*)$', txt, re.M):
                for c in [m.group(1)] + re.findall(r'(/\S+\.(?:py|sh|js))', m.group(2)):
                    t = script(c)
                    if t: edge(un, t, 'executes', rel)
                    elif c.startswith(('/usr/local', '/opt', '/srv', '/root')): edge(un, path_node(c, rel), 'executes', rel)
            if f.endswith('.timer'):
                t = re.search(r'^Unit=(\S+)', txt, re.M); t = t.group(1) if t else f[:-6] + '.service'
                if t in units: edge(un, units[t], 'triggers', rel)
            for m in re.finditer(r'^(?:Requires|Wants|After|BindsTo|PartOf)=(.+)$', txt, re.M):
                for d in m.group(1).split():
                    if d in units and units[d] != un: edge(un, units[d], 'depends_on', rel)
        elif p == '/etc/haproxy/haproxy.cfg':
            cur = None
            for line in read(rel).splitlines():
                l = line.split('#')[0].strip()
                m = re.match(r'^(frontend|backend|listen)\s+(\S+)', l)
                if m:
                    cur = node(f'hap_{sid(h)}_{m[1]}_{sid(m[2])}', f'haproxy {m[1]} {m[2]} @{h}', rel); edge(hn, cur, 'runs', rel); continue
                if not cur: continue
                m = re.match(r'^(?:use_backend|default_backend)\s+(\S+)', l)
                if m: edge(cur, node(f'hap_{sid(h)}_backend_{sid(m[1])}', f'haproxy backend {m[1]} @{h}', rel), 'routes_to', rel)
                m = re.match(r'^server\s+\S+\s+(\S+)', l)
                if m and (t := addr(m[1], rel)) and t != hn: edge(cur, t, 'proxies_to', rel)
                m = re.search(r'(?:req\.ssl_sni|hdr\(host\))\s+-i\s+(.+)', l)
                if m:
                    for d in m[1].split()[:6]:
                        if '.' in d: edge(node('dom_' + sid(d), d, rel), cur, 'served_by', rel)
        elif re.match(r'^/etc/nginx/(sites-enabled|conf\.d)/', p):
            txt = read(rel); site = node(f'nginx_{sid(h)}_{sid(f)}', f'nginx {f} @{h}', rel); edge(hn, site, 'runs', rel)
            for m in re.finditer(r'server_name\s+([^;]+);', txt):
                for d in m[1].split():
                    if '.' in d: edge(node('dom_' + sid(d), d, rel), site, 'served_by', rel)
            for m in re.finditer(r'proxy_pass\s+(\S+);', txt):
                if (t := addr(m[1], rel)) and t != hn: edge(site, t, 'proxies_to', rel)
        elif p == '/etc/hosts':
            for line in read(rel).splitlines():
                l = line.split('#')[0].split()
                if len(l) < 2 or l[0].startswith(('127.', '::1', 'ff0', 'fe00', '0.0.0.0')): continue
                t = addr(l[0], rel)
                for nm in l[1:]:
                    if nm in (h, 'localhost') or nm.startswith('ip6-'): continue
                    ent = node(f'hosts_{sid(h)}_{sid(nm)}', f'{nm} -> {l[0]} (/etc/hosts @{h})', rel)
                    edge(hn, ent, 'pins_dns', rel); edge(ent, node('dom_' + sid(nm), nm, rel), 'overrides', rel)
                    if t: edge(ent, t, 'points_to', rel)
        elif re.match(r'^(docker-)?compose.*\.ya?ml$', f):
            d = os.path.dirname(p); proj = node(f'compose_{sid(h)}_{sid(d)}', f'compose {os.path.basename(d)} @{h}', rel)
            edge(hn, proj, 'runs', rel)
            for m in re.finditer(r'^\s+image:\s*["\']?([^\s"\']+)', read(rel), re.M):
                img = m[1].split('@')[0].split(':')[0]; edge(proj, node('img_' + sid(img), 'docker ' + img, rel), 'uses', rel)
        elif p == '/_containers.jsonl':
            for line in read(rel).splitlines():
                try: c = json.loads(line)
                except ValueError: continue
                nm = c['name'].lstrip('/'); ct = node(f'ctr_{sid(h)}_{sid(nm)}', f'{nm} (container @{h})', rel)
                ports = sorted({b.get('HostPort') for v in (c.get('ports') or {}).values() for b in (v or []) if b.get('HostPort')})
                N[ct]['rationale'] = f"{c.get('state')}, restart={c.get('restart')}, net={c.get('net')}" + (f", ports {','.join(ports)}" if ports else '')
                edge(hn, ct, 'runs', rel)
                if img := (c.get('image') or '').split('@')[0].split(':')[0]: edge(ct, node('img_' + sid(img), 'docker ' + img, rel), 'uses', rel)
                if wd := c.get('workdir'): edge(ct, node(f'compose_{sid(h)}_{sid(wd)}', f'compose {os.path.basename(wd)} @{h}', rel), 'part_of', rel)
                for mnt in c.get('mounts') or []:
                    if mnt and mnt.startswith(('/opt', '/srv', '/mnt', '/data', '/etc', '/var/lib')) and not mnt.startswith('/var/lib/docker'):
                        edge(ct, path_node(mnt, rel), 'mounts', rel)
        elif p == '/etc/crontab' or re.match(r'^/(etc/cron\.d|var/spool/cron/crontabs)/', p):
            for i, line in enumerate(read(rel).splitlines(), 1):
                l = line.strip()
                if not l or l.startswith('#') or re.match(r'^[A-Z_]+=', l): continue
                parts = l.split()
                sched, cmd = (parts[0], parts[1:]) if l.startswith('@') else (' '.join(parts[:5]), parts[5:])
                if p.startswith('/etc/'): cmd = cmd[1:]  # system crontabs carry a user column
                scripts = re.findall(r'(/(?:usr/local|opt|srv|root|home)/[^\s;|&>"\']+)', ' '.join(cmd))
                if not scripts or 'run-parts' in cmd: continue
                cr = node(f'cron_{sid(h)}_{sid(f)}_{i}', f'cron {sched} {os.path.basename(scripts[0])} @{h}', rel); edge(hn, cr, 'runs', rel)
                for sc in scripts: edge(cr, script(sc) or path_node(sc, rel), 'executes', rel)
        elif re.match(r'^/etc/(wireguard|amnezia)/.*\.conf$', p):
            txt = read(rel)
            if '[Interface]' not in txt: continue
            tun = node(f'wg_{sid(h)}_{sid(p)}', f'tunnel {os.path.splitext(f)[0]} @{h}', rel); edge(hn, tun, 'runs', rel)
            lp = re.search(r'^ListenPort\s*=\s*(\d+)', txt, re.M)
            N[tun]['rationale'] = ('amnezia' if 'amnezia' in p else 'wireguard') + (f', port {lp[1]}' if lp else '')
            for m in re.finditer(r'^Endpoint\s*=\s*(\S+)', txt, re.M):
                if (t := addr(m[1], rel)) and t != hn: edge(tun, t, 'tunnels_to', rel)
        elif 'xray' in p and p.endswith('.json'):
            try: cfg = json.loads(read(rel))
            except ValueError: continue
            xr = node(f'xray_{sid(h)}_{sid(p)}', f'xray {f} @{h}', rel); edge(hn, xr, 'runs', rel)
            for ob in cfg.get('outbounds') or []:
                st = ob.get('settings') or {}
                for srv in (st.get('vnext') or []) + (st.get('servers') or []):
                    if (a := srv.get('address')) and (t := addr(str(a), rel)) and t != hn: edge(xr, t, 'proxies_to', rel)
        elif p.startswith('/etc/ufw/user'):
            fw = node(f'ufw_{sid(h)}', f'ufw @{h}', rel)
            N[fw]['rationale'] = f"{read(rel).count('### tuple ###')} rules"; edge(hn, fw, 'runs', rel)

if os.path.exists(GRAPH):
    g = json.load(open(GRAPH))
    g['nodes'] = [n for n in g['nodes'] if n.get('_origin') != 'cfg']  # idempotent re-run
    g['links'] = [l for l in g['links'] if l.get('_origin') != 'cfg']
else:
    g = {'directed': False, 'multigraph': False, 'graph': {}, 'nodes': [], 'links': []}
have = {n['id'] for n in g['nodes']}
g['nodes'] += [n for i, n in N.items() if i not in have]
g['links'] += E
os.makedirs(os.path.dirname(os.path.abspath(GRAPH)), exist_ok=True)
json.dump(g, open(GRAPH, 'w'), ensure_ascii=False)
print(f'cfg_graph: {len(N)} nodes, {len(E)} edges', dict(collections.Counter(e["relation"] for e in E)))
