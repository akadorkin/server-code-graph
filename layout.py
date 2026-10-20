"""layout.py RAW CORPUS [GIT_DIR ...] — build the corpus that graphify reads.

corpus/git/<repo>/   committed files of each local checkout (`git archive HEAD`)
corpus/nodes/<host>/ files collected from each host, minus exact copies (sha256) of anything already in the corpus
corpus/.paths.json   "<host>:/abs/path" -> corpus-relative path, so cfg_graph.py can link a systemd unit to a
                     script even when that script was deduplicated into git/.
Runs BEFORE clean_secrets.py so hashes compare unscrubbed bytes on both sides.
"""
import hashlib, json, os, shutil, subprocess, sys

raw, corpus, gits = sys.argv[1], sys.argv[2], sys.argv[3:]
H = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()
for d in ('git', 'nodes'):
    shutil.rmtree(os.path.join(corpus, d), ignore_errors=True)

for g in gits:
    dst = os.path.join(corpus, 'git', os.path.basename(os.path.abspath(g)))
    os.makedirs(dst)
    tar = subprocess.run(['git', '-C', g, 'archive', 'HEAD'], check=True, capture_output=True).stdout
    subprocess.run(['tar', '-C', dst, '-xf', '-'], input=tar, check=True)

seen = {}
for root, _, fs in os.walk(os.path.join(corpus, 'git')):
    for f in fs:
        p = os.path.join(root, f); seen.setdefault(H(p), os.path.relpath(p, corpus))

paths, kept, dup = {}, 0, 0
for h in sorted(x for x in os.listdir(raw) if os.path.isdir(os.path.join(raw, x))):
    base = os.path.join(raw, h)
    for root, _, fs in os.walk(base):
        for f in sorted(fs):
            p = os.path.join(root, f)
            if os.path.islink(p) or not os.path.isfile(p): continue
            rel = os.path.relpath(p, base); key = H(p)
            if rel == '_containers.jsonl': rel = '_docker/containers.jsonl'
            is_etc = rel.startswith(('etc/', 'var/', '_docker/'))  # host config is kept even when identical elsewhere
            if not is_etc and key in seen:
                paths[f'{h}:/{rel}'] = seen[key]; dup += 1; continue
            if not is_etc and '.' not in os.path.basename(rel):  # extension-less script: give graphify a parser hint
                rel += '.py' if b'python' in open(p, 'rb').read(80).split(b'\n')[0] else '.sh'
            t = os.path.join(corpus, 'nodes', h, rel)
            os.makedirs(os.path.dirname(t), exist_ok=True); shutil.copy2(p, t)
            r = os.path.relpath(t, corpus); seen.setdefault(key, r); kept += 1
            paths[f'{h}:/{os.path.relpath(p, base)}'] = r
json.dump(paths, open(os.path.join(corpus, '.paths.json'), 'w'), indent=0)
print(f'layout: {len(gits)} repos, {kept} host files kept, {dup} duplicates of repo files skipped')
