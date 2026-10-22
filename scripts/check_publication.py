"""Check the public source tree and recorded evidence without downloading models."""
import csv
import fnmatch
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'samples/receipts-100-v2'
errors = []

def require(condition, message):
    if not condition:
        errors.append(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution_sha(path):
    """Compare Git's canonical text bytes across checkout and source ZIP EOLs."""
    name = path.relative_to(ROOT).as_posix()
    text = True
    for line in (ROOT / '.gitattributes').read_text('utf-8').splitlines():
        parts = line.split()
        if not parts or parts[0].startswith('#'):
            continue
        pattern = parts[0]
        if not fnmatch.fnmatchcase(name if '/' in pattern else path.name, pattern):
            continue
        for attribute in parts[1:]:
            if attribute in {'-text', 'binary'}:
                text = False
            elif attribute == 'text' or attribute.startswith('text='):
                text = True
    raw = path.read_bytes()
    return hashlib.sha256(raw.replace(b'\r\n', b'\n') if text else raw).hexdigest()

def link(source, target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith('/'):
        return
    path = (source.parent / unquote(parsed.path)).resolve()
    require(path.is_relative_to(ROOT) and path.exists(), f'{source.relative_to(ROOT)}: broken link {target}')

manifest = json.loads((SAMPLE / 'manifest.json').read_text('utf-8'))
require(sha(SAMPLE / 'manifest.json') == '1b9e40688cc730724328923c7f3b5ce1cd7941fb102fdf369dd58968d3b151c7', 'Original fixture manifest changed')
cases = manifest['cases']
require(len(cases) == 100 and {c['case_id'] for c in cases} == {f'V{i:03d}' for i in range(1,101)}, 'Fixture IDs/count changed')
require(len({c['sha256'] for c in cases}) == 100, 'Fixture PDFs must be distinct')
require(len({c['layout_id'] for c in cases}) == 100, 'Fixture layout IDs must be distinct')
for c in cases:
    path = (SAMPLE / c['file']).resolve()
    require(path.is_relative_to(SAMPLE) and path.is_file(), f"Missing/unsafe PDF {c['case_id']}")
    if path.is_file():
        require(sha(path) == c['sha256'], f"PDF changed: {c['case_id']}")
    require((SAMPLE / 'thumbnails' / (c['case_id'] + '.jpg')).is_file(), f"Missing thumbnail {c['case_id']}")
for author in manifest['authors']:
    path = SAMPLE / 'sources/authors' / author['author'] / 'generate.py'
    require(sha(path) == author['generator_file_sha256'], f"Archived generator changed: {author['author']}")

with zipfile.ZipFile(SAMPLE / 'receipts-100.zip') as z:
    require(z.testzip() is None, 'Sample ZIP CRC failed')
    names = z.namelist()
    require(set(n for n in names if n.endswith('.pdf')) == {c['file'] for c in cases}, 'Sample ZIP must contain exactly 100 individual PDFs')
    for name in names:
        parts = PurePosixPath(name).parts
        require(not PurePosixPath(name).is_absolute() and '..' not in parts and '\\' not in name, 'Unsafe ZIP entry')
        require(not any(part in {'audit','__pycache__','.git','raw-output.jsonl','tail-output.jsonl'} for part in parts), f'Private ZIP entry {name}')
    for c in cases:
        require(hashlib.sha256(z.read(c['file'])).hexdigest() == c['sha256'], f"ZIP PDF changed: {c['case_id']}")

report = json.loads((ROOT / 'docs/testing/results.json').read_text('utf-8'))
require(report['fixture_manifest_sha256'] == sha(SAMPLE / 'manifest.json'), 'Report uses different fixtures')
require(len(report['cases']) == 100 and report['confirmed'] == 0, 'Report scope changed')
truth = {c['case_id']:c for c in cases}
for row in report['cases']:
    c = truth[row['id']]
    require(row['sha256'] == c['sha256'], f"Report PDF hash differs: {row['id']}")
    require(row['expected'] == {k:c[k] for k in ['merchant','date','total','acceptable_accounts','expected_decision']}, f"Report truth differs: {row['id']}")
for key, score in report['after'].items():
    values = [c['checks'].get(key) for c in report['cases'] if c['checks'].get(key) is not None]
    require({'matched':sum(v is True for v in values),'evaluated':len(values)} == score, f'Aggregate score differs: {key}')
with (ROOT / 'docs/testing/comparison-100.csv').open(encoding='utf-8-sig',newline='') as f:
    rows = list(csv.DictReader(f))
require(len(rows) == 100 and {r['case_id'] for r in rows} == set(truth), 'Comparison CSV scope differs')
meta = json.loads((ROOT / 'docs/testing/csv-verification.json').read_text('utf-8'))
for item in meta['exports']:
    path = ROOT / 'docs/testing/csv' / item['file']
    require(sha(path) == item['sha256'], f"CSV example changed: {item['file']}")
    rows = list(csv.reader(io.StringIO(path.read_bytes().decode(item['encoding']))))
    data = rows if item['profile'] == 'yayoi' else rows[1:]
    require(len(data) == 7, f"CSV example rows differ: {item['profile']}")

# These are the processing modules from the real 100-case run, before branding.
critical = ['data/account-catalog.json', 'receipt_ledger/accounting.py','receipt_ledger/catalog.py',
            'receipt_ledger/extraction.py','receipt_ledger/exports.py','receipt_ledger/ocr_focus.py',
            'receipt_ledger/pipeline.py','receipt_ledger/schemas.py','receipt_ledger/semantic.py','receipt_ledger/store.py']
changes_path = ROOT / 'docs/testing/v1.2/source-changes.json'
if not changes_path.is_file():
    changes_path = ROOT / 'docs/testing/security/source-changes.json'
if not changes_path.is_file():
    changes_path = ROOT / 'docs/testing/v1/source-changes.json'
changes = json.loads(changes_path.read_text('utf-8')) if changes_path.is_file() else {}
for name in critical:
    original_hash = report['current_source_hashes'][name]
    current_hash = sha(ROOT / name)
    if current_hash == original_hash:
        continue
    change = changes.get(name, {})
    archive = (ROOT / change.get('baseline', 'missing-baseline')).resolve()
    require(archive.is_relative_to(ROOT / 'docs/testing/baseline') and archive.is_file(), f'Missing original audit baseline: {name}')
    if archive.is_file():
        require(sha(archive) == original_hash == change.get('before_sha256'), f'Original baseline hash differs: {name}')
    require(current_hash == change.get('after_sha256'), f'Unrecorded processing change: {name}')
    require(bool(change.get('scope')) and bool(change.get('regression_tests')), f'Missing change scope/tests: {name}')

# New runs are additional evidence; historical scores and baselines stay immutable.
security = ROOT / 'docs/testing/security'
if (security / 'results-100.json').is_file():
    latest = json.loads((security / 'results-100.json').read_text('utf-8'))
    require(latest['execution_status'] == 'complete', 'Security 100-case run is incomplete')
    require(latest['fixture_manifest_sha256'] == sha(SAMPLE / 'manifest.json'), 'Security run uses different fixtures')
    require(len(latest['cases']) == 100 and {c['case_id'] for c in latest['cases']} == set(truth), 'Security run IDs/count differ')
    require(latest['confirmed_count'] == 0 and latest['journal_row_count'] == 0, 'Security run must remain unconfirmed')
    for row in latest['cases']:
        original = truth[row['case_id']]
        require(row['sha256'] == original['sha256'], f"Security PDF hash differs: {row['case_id']}")
        require(row['expected'] == {k:original[k] for k in ['merchant','date','total','acceptable_accounts','expected_decision']}, f"Security truth differs: {row['case_id']}")
    for key, score in latest['metrics'].items():
        values = [c['checks'].get(key) for c in latest['cases'] if c['checks'].get(key) is not None]
        require({'matched':sum(v is True for v in values),'evaluated':len(values)} == score, f'Security aggregate differs: {key}')
    with (security / 'comparison-100.csv').open(encoding='utf-8-sig',newline='') as f:
        latest_rows = list(csv.DictReader(f))
    require(len(latest_rows) == 100 and {r['case_id'] for r in latest_rows} == set(truth), 'Security comparison CSV differs')
    dependencies = json.loads((security / 'python-dependency-audit.json').read_text('utf-8'))
    require(len(dependencies['dependencies']) == 45 and not dependencies['ignored_advisories'], 'Python dependency audit scope differs')
    require(all(not d.get('vulns') and not d.get('skip_reason') for d in dependencies['dependencies']), 'Python audit contains a match or skip')
    http = json.loads((security / 'http-security.json').read_text('utf-8'))
    require(len(http['checks']) == 18 and all(c['expected_http'] == c['observed_http'] for c in http['checks']), 'HTTP security result differs')
    require(http['owned_listener_stopped'] and not http['database_header_is_plain_sqlite'], 'HTTP security cleanup/encryption proof differs')
    csv_meta = json.loads((security / 'csv-verification.json').read_text('utf-8'))
    for item in csv_meta['exports']:
        require(sha(security / 'csv' / item['file']) == item['sha256'], f"Security CSV changed: {item['file']}")
        require(item['receipts'] == 6 and item['rows'] == 7 and item['debit'] == item['credit'] == 299095, f"Security CSV totals differ: {item['profile']}")
    finalization = json.loads((security / 'finalization.json').read_text('utf-8'))
    if finalization.get('status') in {'passed','complete','completed'}:
        recorded_sources = json.loads((security / 'source-hashes.json').read_text('utf-8'))
        snapshot = recorded_sources['current_snapshot']
        archive_info = recorded_sources.get('historical_source_archive')
        if archive_info:
            archive = security / archive_info['file']
            require(archive.resolve().is_relative_to(security) and archive.is_file(), 'Missing historical security source archive')
            if archive.is_file():
                require(sha(archive) == archive_info['sha256'], 'Historical security source archive changed')
                with zipfile.ZipFile(archive) as source_zip:
                    require(source_zip.testzip() is None and set(source_zip.namelist()) == set(snapshot), 'Historical security source archive scope differs')
                    for name, expected_hash in snapshot.items():
                        require(hashlib.sha256(source_zip.read(name)).hexdigest() == expected_hash, f'Historical security source differs: {name}')
        else:
            for name, expected_hash in snapshot.items():
                require(distribution_sha(ROOT / name) == expected_hash, f'Final security source snapshot is stale: {name}')

private_path = re.compile(r'(?i)[a-z]:[\\/](?:Users[\\/][^\\/\s"\']+|Codex[\\/]|Tools[\\/])')
secret = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9_-]{35,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
skipped = {'.git','.venv','venv','__pycache__','.pytest_cache','.local','dist','node_modules'}
text_suffixes = {'.py','.md','.json','.js','.mjs','.cjs','.css','.html','.txt','.yml','.yaml','.ps1','.cmd','.svg'}
class Assets(HTMLParser):
    def __init__(self, path):
        super().__init__(); self.path = path
    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {'src','href'} and value:
                link(self.path,value)

files = [p for p in ROOT.rglob('*') if not set(p.relative_to(ROOT).parts) & skipped and p.is_file()]
for p in files:
    relative = p.relative_to(ROOT).as_posix()
    require(not p.is_symlink(), f'Symlink is not part of the public package: {relative}')
    require(p.suffix.lower() not in {'.sqlite','.sqlite3','.db','.onnx','.safetensors','.gguf','.log','.pem','.key','.ttf','.ttc'}, f'Private/model/font artifact: {relative}')
    require(p.suffix.lower() not in {'.dpapi','.ryoshubak'} and not p.name.startswith('.ryoshu-')
            and not re.search(r'\.(?:sqlite3?|db)-(?:wal|shm|journal)$',p.name,re.I), f'Private protected artifact: {relative}')
    require(not p.name.startswith('.env'), f'Environment file in source package: {relative}')
    if p.suffix.lower() not in text_suffixes:
        continue
    content = p.read_text('utf-8-sig')
    require(not private_path.search(content), f'Machine-specific path: {relative}')
    require(not secret.search(content), f'Possible secret: {relative}')
    if p.suffix == '.md':
        content = re.sub(r'```[\s\S]*?```','',content)
        for target in re.findall(r'\]\(([^\s)]+)(?:\s+"[^"]*")?\)',content):
            link(p,target)
    elif p.suffix == '.html':
        Assets(p).feed(content)

payload = json.dumps(report,ensure_ascii=False,separators=(',', ':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
require((ROOT / 'docs/testing/results.js').read_text('utf-8') == 'window.RYOSHU_RESULTS = ' + payload + ';\n', 'Run scripts/build_evidence.py to update static report')
if errors:
    for error in errors:
        print('FAIL:',error)
    sys.exit(1)
print(f'Publication checks passed: {len(files)} files; 100 PDF hashes, ZIP, links, scores, public content and processing sources.')
