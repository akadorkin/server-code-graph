"""clean_secrets.py DIR  — scrub secrets in place, recursively.   clean_secrets.py --self-test

Errs on the side of redacting: a value that merely looks like a secret is replaced with <REDACTED>.
It cannot prove a tree is clean — run gitleaks/trufflehog on the result and read the report before sharing.
"""
import json, os, re, sys
from pathlib import Path

R = '<REDACTED>'
# whole files that are secret stores by nature
KILL = re.compile(r'(^|/)(\.env(\..*)?|[^/]*\.env|[^/]*\.(pem|key|p12|pfx|keystore|jks)|id_(rsa|dsa|ecdsa|ed25519)[^/]*|[^/]*secrets?[^/]*\.(conf|ya?ml|json|txt)|\.netrc|\.pgpass|htpasswd)$', re.I)
# well-known token shapes, anywhere in any file
TOKENS = [re.compile(p) for p in (
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----',
    r'\b[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}',             # Telegram bot token
    r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*',  # JWT
    r'\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}',          # Anthropic / OpenAI
    r'\btskey-[a-z]+-[A-Za-z0-9-]{10,}',                # Tailscale
    r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b',                   # AWS access key id
    r'\bgh[pousr]_[A-Za-z0-9]{30,}', r'\bgithub_pat_[A-Za-z0-9_]{30,}', r'\bglpat-[A-Za-z0-9_-]{20,}',
    r'\bxox[abprs]-[A-Za-z0-9-]{10,}',                  # Slack
    r'\bAGE-SECRET-KEY-1[A-Z0-9]{50,}',
    r'(?<=://)[^:/@\s"\']+:[^@/\s"\'${<]{3,}(?=@)',     # user:password@ in URLs
    r'(?i)(?<=PrivateKey)(\s*[=:]\s*)[A-Za-z0-9+/]{42,}={0,2}',  # WireGuard / x25519
    r'(?i)(?<=PresharedKey)(\s*[=:]\s*)[A-Za-z0-9+/]{42,}={0,2}',
    r'(?<=stats auth )(\S+:)\S+', r'(?<=insecure-password )()\S+',   # haproxy
)]
# KEY = value / key: value where the key name says "secret"
NAME = r'[A-Za-z0-9_.-]*(?:token|passw(?:or)?d|pass|pwd|secret|apikey|api_key|access_key|private_?key|privatekey|credential|auth|dsn|salt|shortids?|_key)[A-Za-z0-9_]*'
KV = re.compile(r'''(?i)^(\s*(?:-\s*)?(?:export\s+|Environment=["']?)?["']?''' + NAME + r'''["']?\s*[:=]\s*)(["']?)([^"'\s,#;]+)''')
# values that are references, not secrets
REF = re.compile(r'^(\$|%|<|\{|\[|/|\.{0,2}/|os\.|env|getenv|process\.env|None|null|nil|true|false|yes|no|on|off|required|changeme|redacted|\*+$)', re.I)
CODE = {'.py', '.js', '.ts', '.tsx', '.go', '.rs', '.kt', '.vue', '.java', '.rb', '.php'}
SHORT = 3   # min secret length in config files; code needs 8 (less noise like `token = t`)
UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
JKEYS = {'privatekey', 'password', 'pass', 'passwd', 'secret', 'secretkey', 'psk', 'presharedkey', 'seed', 'token',
         'shortid', 'shortids', 'uuid', 'auth', 'key', 'apikey', 'api_key', 'mldsa65seed', 'x25519privatekey'}


def scrub_line(line, code):
    m = KV.match(line)
    if m:
        v = m.group(3)
        looks_ref = REF.match(v) or (code and (len(v) < 8 or v.isupper() or re.search(r'[(\[.]', v)))
        if not looks_ref and len(v) >= SHORT:
            line = line[:m.start(3)] + R + line[m.end(3):]
    return line


def scrub_json(o):
    n = 0
    if isinstance(o, dict):
        for k, v in o.items():
            kl = k.lower()
            if kl in JKEYS and v not in ('', [], None) and isinstance(v, (str, list)):
                o[k] = R if isinstance(v, str) else [R]; n += 1
            elif kl == 'id' and isinstance(v, str) and UUID.match(v):  # xray client ids are credentials
                o[k] = R; n += 1
            else:
                n += scrub_json(v)
    elif isinstance(o, list):
        for x in o: n += scrub_json(x)
    return n


def scrub_text(s, suffix):
    if suffix == '.json':
        try:
            d = json.loads(s)
            if scrub_json(d): s = json.dumps(d, ensure_ascii=False, indent=2)
        except ValueError:
            pass
    code = suffix in CODE
    s = '\n'.join(scrub_line(l, code) for l in s.split('\n'))
    for rx in TOKENS:
        s = rx.sub(lambda m: (m.group(1) if m.groups() else '') + R, s)
    return s


def scrub_tree(root):
    killed = changed = 0
    for p in sorted(Path(root).rglob('*')):
        if p.is_symlink() or not p.is_file() or 'graphify-out' in p.parts: continue
        if KILL.search(str(p)):
            p.unlink(); killed += 1; continue
        try: s = p.read_text()
        except (UnicodeDecodeError, OSError): continue
        t = scrub_text(s, p.suffix.lower())
        if t != s: p.write_text(t); changed += 1
    print(f'clean_secrets: {killed} secret files removed, {changed} files scrubbed')


def self_test():
    wg = 'aB3' * 14 + 'c='
    # fake values are split with + so secret scanners do not flag this file
    bad = {  # (text, suffix) -> secret that must vanish
        ('BOT_TOKEN=123456789:' + 'AAHfakefakefakefakefakefakefakefake12\n', '.env'): 'AAHfake',
        ('[Interface]\nPrivateKey = %s\n' % wg, '.conf'): wg,
        ('db:\n  password: hunter2\n', '.yml'): 'hunter2',
        ('  - POSTGRES_PASSWORD=s3cr3tpass\n', '.yml'): 's3cr3tpass',
        ('url = "postgres://app:' + 'S3cretPw@db:5432/app"\n', '.py'): 'S3cretPw',
        ('API_KEY = "abcd1234' + 'efgh5678ijkl"\n', '.py'): 'abcd1234efgh5678',
        ('{"inbounds":[{"settings":{"clients":[{"id":"6f1c2b9e-1111-4222-8333-944455556666"}]},'
         '"streamSettings":{"realitySettings":{"privateKey":"kPriv123","shortIds":["ab12cd"]}}}]}', '.json'): 'kPriv123',
        ('{"clients":[{"id":"6f1c2b9e-1111-4222-8333-944455556666"}]}', '.json'): '6f1c2b9e',
        ('{"shortIds":["ab12cd34"]}', '.json'): 'ab12cd34',
        ('-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAb3BlbnNzaA\n-----END OPENSSH PRIVATE KEY-----\n', '.txt'): 'AAAAb3Bl',
        ('Environment="AUTH_TOKEN=' + 'tok_9f8e7d6c5b4a"\n', '.service'): 'tok_9f8e7d',
        ('export GH=ghp_' + 'x' * 36 + '\n', '.sh'): 'ghp_xxx',
        ('listen stats\n    stats auth admin:Adm1nPw\n', '.cfg'): 'Adm1nPw',
    }
    for (text, suf), secret in bad.items():
        out = scrub_text(text, suf)
        assert secret not in out, f'leaked {secret!r}: {out!r}'
    good = [  # must stay byte-identical
        ('password = os.environ["DB_PASSWORD"]\n', '.py'),
        ('token = get_token(cfg)\n', '.py'),
        ('TOKEN=${TOKEN}\n', '.env'),
        ('ExecStart=/usr/local/bin/backup.sh --daily\n', '.service'),
        ('auth_basic "Restricted";\nproxy_pass http://127.0.0.1:8080;\n', '.conf'),
        ('    password: <REDACTED>\n', '.yml'),
        ('{"tag": "direct", "protocol": "freedom"}', '.json'),
    ]
    for text, suf in good:
        assert scrub_text(text, suf) == text, f'over-redacted {text!r} -> {scrub_text(text, suf)!r}'
    assert KILL.search('/x/.env') and KILL.search('/x/prod.env') and KILL.search('/x/tls.key') and not KILL.search('/x/keys.py')
    print('clean_secrets self-test: ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']: self_test()
    elif len(sys.argv) == 2 and os.path.isdir(sys.argv[1]): scrub_tree(sys.argv[1])
    else: sys.exit(__doc__)
