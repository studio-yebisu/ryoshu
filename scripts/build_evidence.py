"""Build static report data from the curated public JSON; no network or inference."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / 'docs/testing/results.json').read_text(encoding='utf-8'))
payload = json.dumps(report, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
(ROOT / 'docs/testing/results.js').write_text('window.RYOSHU_RESULTS = ' + payload + ';\n', encoding='utf-8')
print('Built static report for', len(report['cases']), 'cases.')
