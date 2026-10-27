"""Build static report data from the curated public JSON; no network or inference.

Default (no arguments): docs/testing/results.json -> docs/testing/results.js, unchanged behaviour.

v1.4 mode (separate, versioned, never touches existing history):
  python scripts/build_evidence.py v14 --data <approved-public-data.json> --data-sha256 <hex>
      --copy <adopted-kimi-copy.json> --copy-sha256 <hex> [--out docs/testing/v1.4]
The data file must be the root-sanitized projection (public-data contract); the copy file must be the
adopted Kimi manuscript. Everything is validated fail-closed; nothing is written unless all checks pass,
and an existing non-empty output directory is never overwritten.
"""
import argparse
import hashlib
import html
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_default():
    report = json.loads((ROOT / 'docs/testing/results.json').read_text(encoding='utf-8'))
    payload = json.dumps(report, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (ROOT / 'docs/testing/results.js').write_text('window.RYOSHU_RESULTS = ' + payload + ';\n', encoding='utf-8')
    print('Built static report for', len(report['cases']), 'cases.')


# ---------------------------------------------------------------- v1.4 public evidence (fail-closed)

DATA_SCHEMA = 'ryoshu.public-evidence/1.4'
COPY_SCHEMA = 'ryoshu.kimi-evidence-copy/1.4'
SAMPLES = 'samples/receipts-100-v2'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
HEX40 = re.compile(r'^[0-9a-f]{40}$')
CASE_ID = re.compile(r'^V\d{3}$')
REL_PATH = re.compile(r'^(?!/)(?![A-Za-z]:)(?!.*\.\.)[A-Za-z0-9._\-/]+$')
# Whole '_'-delimited key tokens only (so 'catalog' or 'runtime_pin' are not refused).
FORBIDDEN_KEY = re.compile(r'(^|_)(receipt_id|data_?dir|tokens?|session|password|passphrase|secret|api_?key|key|authori[sz]ation|bearer|cookie|logs?|instance|pid|owner|username)(_|$)', re.I)
# The only schema-bound exception: the numeric generation limit at exactly $.pins.config.max_tokens.
ALLOWED_KEY_AT = {('$.pins.config', 'max_tokens')}
ABSOLUTE = re.compile(r'(^[A-Za-z]:[\\/])|(^\\\\)|(^/)|([\\/]Users[\\/])|(/home/)', re.I)
PLACEHOLDER = re.compile(r'(\[COPY_SLOT|\[RYOSHU|\bTODO\b|\bTBD\b|\bPENDING\b|<pending>|\bXXX\b)', re.I)

# Populations are fixed by the 100-case fixture truth; the build refuses any other split.
POPULATIONS = {
    'amount': {'numeric_jpy': 95, 'fx_unresolved': 2, 'refund_excluded': 3},
    'account': {'ordinary': 48, 'special': 52},
    'tax': {'numeric': 52, 'blank': 11, 'not_rated': 37},
}
CHECK_KEYS = ('ocr_completed', 'initial_output_completed', 'total_exact', 'date_exact', 'merchant_exact',
              'account_exact', 'decision_exact', 'account_or_decision_exact', 'tax_exact')
INITIAL_KEYS = ('date', 'merchant', 'total', 'account', 'decision', 'tax_amount')
EXPECTED_KEYS = ('date', 'merchant', 'total', 'acceptable_accounts', 'decision', 'tax_amount')
# Display pairs (initial key, expected key) shown per case, in this column order.
FIELD_PAIRS = (('date', 'date'), ('merchant', 'merchant'), ('total', 'total'), ('account', 'acceptable_accounts'),
               ('decision', 'decision'), ('tax_amount', 'tax_amount'))
# metric id -> (population axis, population values, check key); evaluated must equal the population size.
METRICS = {
    'ocr_completed': (None, None, 'ocr_completed'),
    'initial_output_completed': (None, None, 'initial_output_completed'),
    'amount_numeric_jpy_exact': ('amount', ('numeric_jpy',), 'total_exact'),
    'amount_fx_unresolved_state_kept': ('amount', ('fx_unresolved',), 'total_exact'),
    'date_exact': (None, None, 'date_exact'),
    'merchant_exact': (None, None, 'merchant_exact'),
    'account_ordinary_exact': ('account', ('ordinary',), 'account_exact'),
    'special_handling_exact': ('account', ('special',), 'account_or_decision_exact'),
    'account_or_handling_exact': (None, None, 'account_or_decision_exact'),
    'tax_numeric_exact': ('tax', ('numeric',), 'tax_exact'),
    'tax_blank_kept': ('tax', ('blank',), 'tax_exact'),
}
EXPECTED_EVALUATED = {'ocr_completed': 100, 'initial_output_completed': 100, 'amount_numeric_jpy_exact': 95,
                      'amount_fx_unresolved_state_kept': 2, 'date_exact': 100, 'merchant_exact': 100,
                      'account_ordinary_exact': 48, 'special_handling_exact': 52, 'account_or_handling_exact': 100,
                      'tax_numeric_exact': 52, 'tax_blank_kept': 11}
LEGACY_TOTAL_EVALUATED = 97   # legacy scorer: numeric JPY 95 + FX unresolved 2
GENERATED_V14_FILES = ('results-v1.4.json', 'index.html')
V14_STYLESHEET = 'report-v14.css'                # v1.4-only source stylesheet (scoped under .v14)
PRESERVED_V14_FILES = ('source-changes.json', V14_STYLESHEET)   # source files present before the result build; never written
FIXED_DEFINITIONS = {
    'measured_output': 'initial_automated_app_output_after_ocr_native_pdf_grounding_extraction_and_checked_decision_policies_no_human_edits',
    'not': 'raw_model_benchmark',
    'timing_kind': 'cold_app_not_disk_cold',
    'timer_start': 'immediately_before_first_upload_post',
    'timer_end': 'first_poll_list_response_with_all_100_terminal',
    'timer_includes': 'two_50_file_uploads_ocr_and_local_model',
    'timer_excludes': 'setup_download_human_confirmation_csv_and_scoring',
    'comparison_kind': 'runtime_and_model_variant_changed_not_causal_runtime_only',
    'dataset_kind': 'existing_100_fictional_development_regression_dataset_not_unseen_or_general_real_receipt_accuracy',
    'claims_excluded': 'no_completed_tax_correctness_no_vulnerability_zero',
}
TOP_KEYS = {'schema', 'approved', 'definitions', 'pins', 'timing', 'populations', 'metrics', 'cases', 'known_limitations', 'legacy_audit'}
LIMITATION_ID = re.compile(r'^[a-z][a-z0-9_]{2,63}$')
# Known native residual that must never be dropped from the public page.
REQUIRED_LIMITATIONS = ('native_sqlcipher_openssl_provider',)
BASE_COPY_SLOTS = (
    'page_title', 'page_lead', 'nav_history', 'nav_gallery', 'nav_json',
    'section_summary', 'section_method', 'section_limitations', 'section_cases',
    'col_metric', 'col_matched', 'col_evaluated', 'col_case', 'col_original',
    'method_measured_output', 'method_not_raw_model', 'method_timing', 'method_timing_values',
    'method_comparison', 'method_dataset', 'method_claims_excluded', 'method_pins',
    'method_population_amount', 'method_population_account', 'method_population_tax',
    'label_elapsed_seconds', 'label_poll_interval_seconds', 'label_tax_not_rated',
    'legend_initial_expected', 'legend_marks', 'mark_not_applicable',
) + tuple('metric_' + m for m in METRICS) + tuple('field_' + i for i, _ in FIELD_PAIRS) + tuple('check_' + k for k in CHECK_KEYS)


class EvidenceError(Exception):
    """Machine diagnostic (English reason code + pointer); never shown as product copy."""


def fail(code, where=''):
    raise EvidenceError(code + (': ' + where if where else ''))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exact_keys(obj, keys, where):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        fail('unexpected_or_missing_keys', where)


def scan(value, where='$'):
    """Reject forbidden key names, absolute/private paths, placeholders and non-finite numbers anywhere."""
    if isinstance(value, dict):
        path_keys = where.endswith(('.source_files', '.working_copy_files'))
        for key, item in value.items():
            if not isinstance(key, str):
                fail('forbidden_key', where)
            if path_keys:
                if REL_PATH.match(key) is None:
                    fail('invalid_path_key', where + '.' + key)
            elif FORBIDDEN_KEY.search(key) is not None and (where, key) not in ALLOWED_KEY_AT:
                fail('forbidden_key', where + '.' + key)
            scan(item, where + '.' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            scan(item, where + '[' + str(index) + ']')
    elif isinstance(value, str):
        if ABSOLUTE.search(value):
            fail('absolute_or_private_path', where)
        if PLACEHOLDER.search(value):
            fail('placeholder_value', where)
    elif isinstance(value, float) and not math.isfinite(value):
        fail('non_finite_number', where)
    elif value is not None and not isinstance(value, (bool, int, float)):
        fail('unsupported_value_type', where)


def number(value, where, minimum=0.0, strict=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum or (strict and value <= minimum):
        fail('invalid_number', where)
    return value


def integer(value, where, minimum=None, maximum=None):
    if type(value) is not int or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        fail('invalid_integer', where)
    return value


def text(value, where):
    if not isinstance(value, str) or not value.strip():
        fail('empty_identity_string', where)
    return value


def manifest_cases():
    manifest = ROOT / SAMPLES / 'manifest.json'
    cases = json.loads(manifest.read_text(encoding='utf-8'))['cases']
    files = {}
    for case in cases:
        if not CASE_ID.match(str(case.get('case_id'))) or not REL_PATH.match(str(case.get('file'))):
            fail('manifest_case_invalid', str(case.get('case_id')))
        files[case['case_id']] = case['file']
    return sha256_file(manifest), files


def validate_pins(pins, manifest_sha):
    exact_keys(pins, {'runtime', 'model', 'config', 'source_files', 'baseline', 'dataset'}, 'pins')
    runtime = pins['runtime']
    exact_keys(runtime, {'llama_cpp_tag', 'llama_cpp_commit', 'runtime_pin_sha256', 'crt_version'}, 'pins.runtime')
    text(runtime['llama_cpp_tag'], 'pins.runtime.llama_cpp_tag')
    text(runtime['crt_version'], 'pins.runtime.crt_version')
    if not HEX40.match(str(runtime['llama_cpp_commit'])) or not HEX64.match(str(runtime['runtime_pin_sha256'])):
        fail('invalid_runtime_pin', 'pins.runtime')
    model = pins['model']
    exact_keys(model, {'repo', 'revision', 'file', 'sha256', 'chat_template_sha256', 'variant'}, 'pins.model')
    for key in ('repo', 'file', 'variant'):
        text(model[key], 'pins.model.' + key)
    if not HEX40.match(str(model['revision'])) or not HEX64.match(str(model['sha256'])) or not HEX64.match(str(model['chat_template_sha256'])):
        fail('invalid_model_pin', 'pins.model')
    config = pins['config']
    exact_keys(config, {'ctx_size', 'threads', 'n_gpu_layers', 'temperature', 'seed', 'max_tokens', 'enable_thinking', 'reasoning_flag'}, 'pins.config')
    integer(config['ctx_size'], 'pins.config.ctx_size', 1)
    integer(config['threads'], 'pins.config.threads', 1)
    integer(config['n_gpu_layers'], 'pins.config.n_gpu_layers', 0)
    number(config['temperature'], 'pins.config.temperature')
    integer(config['seed'], 'pins.config.seed')
    integer(config['max_tokens'], 'pins.config.max_tokens', 1)
    text(config['reasoning_flag'], 'pins.config.reasoning_flag')
    if config['enable_thinking'] is not False:
        fail('invalid_config', 'pins.config.enable_thinking')
    files = pins['source_files']
    if not isinstance(files, dict) or not files:
        fail('missing_source_files', 'pins.source_files')
    for rel, digest in files.items():
        if not REL_PATH.match(rel) or not HEX64.match(str(digest)):
            fail('invalid_source_file_pin', 'pins.source_files.' + rel)
    baseline = pins['baseline']
    exact_keys(baseline, {'model_variant', 'model_sha256', 'working_copy_files'}, 'pins.baseline')
    text(baseline['model_variant'], 'pins.baseline.model_variant')
    if not HEX64.match(str(baseline['model_sha256'])) or not isinstance(baseline['working_copy_files'], dict) or not baseline['working_copy_files']:
        fail('invalid_baseline_pin', 'pins.baseline')
    for rel, digest in baseline['working_copy_files'].items():
        if not REL_PATH.match(rel) or not HEX64.match(str(digest)):
            fail('invalid_baseline_file_pin', 'pins.baseline.working_copy_files.' + rel)
    if baseline['model_variant'] == model['variant'] or baseline['model_sha256'] == model['sha256']:
        fail('model_variant_not_changed_contradicts_comparison_kind', 'pins')
    dataset = pins['dataset']
    exact_keys(dataset, {'fixture_manifest_sha256', 'cases'}, 'pins.dataset')
    if dataset['fixture_manifest_sha256'] != manifest_sha or dataset['cases'] != 100:
        fail('fixture_manifest_mismatch', 'pins.dataset')


def validate_value(value, where):
    if value is not None and not isinstance(value, (str, int)) or type(value) is bool:
        fail('invalid_field_value', where)


def validate_cases(cases, manifest_ids):
    if not isinstance(cases, list) or len(cases) != 100:
        fail('case_count_not_100', 'cases')
    ids = [c.get('case_id') if isinstance(c, dict) else None for c in cases]
    if not all(isinstance(i, str) and CASE_ID.match(i) for i in ids) or len(set(ids)) != 100 or set(ids) != set(manifest_ids):
        fail('case_ids_mismatch_manifest', 'cases')
    sizes = {axis: {k: 0 for k in values} for axis, values in POPULATIONS.items()}
    for case in cases:
        where = 'cases.' + case['case_id']
        exact_keys(case, {'case_id', 'populations', 'initial', 'expected', 'checks'}, where)
        exact_keys(case['populations'], set(POPULATIONS), where + '.populations')
        for axis, value in case['populations'].items():
            if value not in POPULATIONS[axis]:
                fail('unknown_population', where + '.populations.' + axis)
            sizes[axis][value] += 1
        exact_keys(case['initial'], set(INITIAL_KEYS), where + '.initial')
        exact_keys(case['expected'], set(EXPECTED_KEYS), where + '.expected')
        for key in INITIAL_KEYS:
            validate_value(case['initial'][key], where + '.initial.' + key)
        for key in EXPECTED_KEYS:
            if key == 'acceptable_accounts':
                accounts = case['expected'][key]
                if not isinstance(accounts, list) or not all(isinstance(a, str) and a.strip() for a in accounts) or len(set(accounts)) != len(accounts):
                    fail('acceptable_accounts_must_be_unique_string_list', where + '.expected.acceptable_accounts')
            else:
                validate_value(case['expected'][key], where + '.expected.' + key)
        exact_keys(case['checks'], set(CHECK_KEYS), where + '.checks')
        pops = case['populations']
        for key, value in case['checks'].items():
            not_applicable = ((key == 'total_exact' and pops['amount'] == 'refund_excluded') or
                              (key == 'account_exact' and pops['account'] != 'ordinary') or
                              (key == 'tax_exact' and pops['tax'] == 'not_rated'))
            if not_applicable and value is not None:
                fail('check_must_be_null_outside_population', where + '.checks.' + key)
            if not not_applicable and type(value) is not bool:
                fail('check_must_be_boolean', where + '.checks.' + key)
        for key in ('total', 'tax_amount'):
            for side in ('initial', 'expected'):
                value = case[side][key]
                if value is not None and type(value) is not int:
                    fail('amount_must_be_integer_or_null', where + '.' + side + '.' + key)
        if pops['account'] == 'ordinary' and not case['expected']['acceptable_accounts']:
            fail('ordinary_case_needs_acceptable_accounts', where)
    if sizes != POPULATIONS:
        fail('population_sizes_mismatch', json.dumps(sizes, sort_keys=True))


def recompute(cases):
    out = {}
    for metric, (axis, values, check) in METRICS.items():
        selected = [c for c in cases if axis is None or c['populations'][axis] in values]
        out[metric] = {'matched': sum(c['checks'][check] is True for c in selected), 'evaluated': len(selected)}
    return out


def validate_data(data):
    scan(data)
    exact_keys(data, TOP_KEYS, '$')
    if data['schema'] != DATA_SCHEMA:
        fail('unknown_schema', 'schema')
    approved = data['approved']
    exact_keys(approved, {'by', 'mapping_sha256', 'private_sources_sha256'}, 'approved')
    if approved['by'] != 'root' or not HEX64.match(str(approved['mapping_sha256'])):
        fail('not_root_approved', 'approved')
    if not isinstance(approved['private_sources_sha256'], dict) or not approved['private_sources_sha256'] or \
            not all(HEX64.match(str(v)) and re.match(r'^[a-z_]+$', k) for k, v in approved['private_sources_sha256'].items()):
        fail('invalid_private_source_hashes', 'approved.private_sources_sha256')
    if data['definitions'] != FIXED_DEFINITIONS:
        fail('definitions_differ_from_contract', 'definitions')
    manifest_sha, files = manifest_cases()
    validate_pins(data['pins'], manifest_sha)
    timing = data['timing']
    exact_keys(timing, {'elapsed_seconds', 'poll_interval_seconds', 'upload_batches', 'batch_size', 'first_post_utc', 'all_terminal_observed_utc'}, 'timing')
    number(timing['elapsed_seconds'], 'timing.elapsed_seconds', strict=True)
    number(timing['poll_interval_seconds'], 'timing.poll_interval_seconds', strict=True)
    if timing['upload_batches'] != 2 or timing['batch_size'] != 50 or type(timing['upload_batches']) is not int or type(timing['batch_size']) is not int:
        fail('upload_shape_mismatch', 'timing')
    for key in ('first_post_utc', 'all_terminal_observed_utc'):
        if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$', str(timing[key])):
            fail('invalid_utc_timestamp', 'timing.' + key)
    if data['populations'] != POPULATIONS:
        fail('population_definition_mismatch', 'populations')
    validate_cases(data['cases'], files)
    computed = recompute(data['cases'])
    if data['metrics'] != computed:
        fail('declared_metrics_differ_from_cases', 'metrics')
    if {m: v['evaluated'] for m, v in computed.items()} != EXPECTED_EVALUATED:
        fail('denominators_mismatch', 'metrics')
    limits = data['known_limitations']
    if not isinstance(limits, list) or len(set(limits)) != len(limits) or not all(isinstance(i, str) and LIMITATION_ID.match(i) for i in limits) \
            or not set(REQUIRED_LIMITATIONS) <= set(limits):
        fail('invalid_or_missing_required_limitations', 'known_limitations')
    legacy = data['legacy_audit']
    exact_keys(legacy, {'total_exact_legacy'}, 'legacy_audit')
    total = legacy['total_exact_legacy']
    exact_keys(total, {'matched', 'evaluated', 'note_id'}, 'legacy_audit.total_exact_legacy')
    integer(total['evaluated'], 'legacy_audit.total_exact_legacy.evaluated', LEGACY_TOTAL_EVALUATED, LEGACY_TOTAL_EVALUATED)
    integer(total['matched'], 'legacy_audit.total_exact_legacy.matched', 0, LEGACY_TOTAL_EVALUATED)
    if total['matched'] != computed['amount_numeric_jpy_exact']['matched'] + computed['amount_fx_unresolved_state_kept']['matched']:
        fail('legacy_total_differs_from_numeric_plus_fx', 'legacy_audit.total_exact_legacy')
    if total['note_id'] != 'legacy_scorer_total_exact_not_numeric_jpy_population':
        fail('legacy_note_required', 'legacy_audit')
    return data, files


def validate_copy(copy, data):
    scan(copy)
    exact_keys(copy, {'schema', 'provenance', 'slots'}, '$copy')
    if copy['schema'] != COPY_SCHEMA:
        fail('unknown_copy_schema', 'schema')
    exact_keys(copy['provenance'], {'author_alias', 'configured_model_id', 'manuscript_sha256', 'root_adopted'}, '$copy.provenance')
    text(copy['provenance']['author_alias'], '$copy.provenance.author_alias')
    text(copy['provenance']['configured_model_id'], '$copy.provenance.configured_model_id')
    if copy['provenance']['root_adopted'] is not True or not HEX64.match(str(copy['provenance']['manuscript_sha256'])):
        fail('copy_not_adopted', '$copy.provenance')
    required = set(BASE_COPY_SLOTS) | {'limitation_' + i for i in data['known_limitations']}
    exact_keys(copy['slots'], required, '$copy.slots')
    for slot, value in copy['slots'].items():
        if not isinstance(value, str) or not value.strip():
            fail('empty_copy_slot', slot)
    return copy


def cell(value):
    if value is None:
        return '&#8211;'
    if isinstance(value, list):
        return ' / '.join(html.escape(v) for v in value) if value else '&#8211;'
    return html.escape(str(value))


def render_html(data, copy, files):
    s = {k: html.escape(v) for k, v in copy['slots'].items()}
    samples = '../../../' + SAMPLES + '/'
    rows = ''.join('<tr><th>' + s['metric_' + m] + '</th><td>' + str(v['matched']) + '</td><td>' + str(v['evaluated']) + '</td></tr>'
                   for m, v in data['metrics'].items())
    timing = data['timing']
    method = ''.join('<p>' + s[k] + '</p>' for k in (
        'method_measured_output', 'method_not_raw_model', 'method_timing', 'method_comparison', 'method_dataset',
        'method_population_amount', 'method_population_account', 'method_population_tax', 'method_claims_excluded', 'method_pins'))
    values = ('<p>' + s['method_timing_values'] + '</p><dl><dt>' + s['label_elapsed_seconds'] + '</dt><dd>' + html.escape(str(timing['elapsed_seconds'])) +
              '</dd><dt>' + s['label_poll_interval_seconds'] + '</dt><dd>' + html.escape(str(timing['poll_interval_seconds'])) + '</dd></dl>')
    limits = ''.join('<p>' + s['limitation_' + i] + '</p>' for i in data['known_limitations'])
    state = lambda v: 'is-pass' if v is True else 'is-fail' if v is False else 'is-na'
    mark = lambda v: '&#10003;' if v is True else '&#10007;' if v is False else '&#8211;'
    cards = []
    for c in data['cases']:
        cid = html.escape(c['case_id'])
        original = samples + html.escape(files[c['case_id']])
        thumb = ROOT / SAMPLES / 'thumbnails' / (c['case_id'] + '.jpg')
        inner = ('<img loading="lazy" width="72" height="72" src="' + samples + 'thumbnails/' + cid + '.jpg" alt="' + s['col_original'] + ' ' + cid + '">'
                 if thumb.is_file() else s['col_original'])
        preview = '<a class="v14-original" href="' + original + '" target="_blank" rel="noopener">' + inner + '</a>'
        fields = ''.join('<dt>' + s['field_' + i] + '</dt><dd><span class="v14-initial">' + cell(c['initial'][i]) +
                         '</span><span class="v14-expected">' + cell(c['expected'][e]) + '</span></dd>' for i, e in FIELD_PAIRS)
        checks = ''.join('<li class="' + state(c['checks'][k]) + '" data-check="' + k + '"><span>' + s['check_' + k] + '</span> <span aria-hidden="true">' +
                         mark(c['checks'][k]) + '</span></li>' for k in CHECK_KEYS)
        flagged = ' has-mismatch' if any(v is False for v in c['checks'].values()) else ''
        cards.append('<li class="v14-case' + flagged + '" id="' + cid + '"><div class="v14-case-head"><h3>' + cid + '</h3>' + preview +
                     '</div><dl class="v14-fields">' + fields + '</dl><ul class="v14-checks">' + checks + '</ul></li>')
    legend = ('<p class="v14-legend">' + s['legend_initial_expected'] + '</p><p class="v14-legend">' + s['legend_marks'] +
              ' &#10003; &#10007; &#8211; ' + s['mark_not_applicable'] + '</p>')
    return ('<!doctype html>\n<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + s['page_title'] + '</title><link rel="stylesheet" href="../report.css"><link rel="stylesheet" href="' + V14_STYLESHEET + '"></head>\n'
            '<body class="v14"><main>'
            '<div class="v14-top"><nav class="v14-nav"><a href="../index.html">' + s['nav_history'] + '</a><a href="' + samples + 'gallery.html">' + s['nav_gallery'] +
            '</a><a href="results-v1.4.json" download>' + s['nav_json'] + '</a></nav><h1>' + s['page_title'] + '</h1><p class="v14-lead">' + s['page_lead'] + '</p></div>\n'
            '<section class="v14-section" id="summary"><h2>' + s['section_summary'] + '</h2><div class="v14-scroll"><table class="v14-summary"><thead><tr><th>' +
            s['col_metric'] + '</th><th>' + s['col_matched'] + '</th><th>' + s['col_evaluated'] + '</th></tr></thead><tbody>' + rows +
            '</tbody></table></div><p>' + s['label_tax_not_rated'] + ' ' + str(POPULATIONS['tax']['not_rated']) + '</p></section>\n'
            '<section class="v14-section v14-method" id="method"><h2>' + s['section_method'] + '</h2>' + method +
            values.replace('<dl>', '<dl class="v14-values">') + '</section>\n'
            '<section class="v14-section" id="limitations"><h2>' + s['section_limitations'] + '</h2>' + limits + '</section>\n'
            '<section class="v14-section" id="cases"><h2>' + s['section_cases'] + '</h2>' + legend +
            '<ol class="v14-cases" aria-label="' + s['col_case'] + '">' + ''.join(cards) + '</ol></section>\n</main></body></html>\n')


def build_v14(argv):
    parser = argparse.ArgumentParser(prog='build_evidence.py v14')
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--data-sha256', required=True)
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--copy-sha256', required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'docs/testing/v1.4')
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out != (ROOT / 'docs/testing/v1.4').resolve():
        fail('output_must_be_docs_testing_v1.4', str(args.out))
    if out.exists():
        # Only the pre-existing source-change metadata may already be there; it is preserved, never rewritten.
        existing = {p.name: p for p in out.iterdir()}
        unknown = sorted(n for n in existing if n not in PRESERVED_V14_FILES)
        if unknown:
            fail('output_exists_refusing_to_overwrite', ','.join(unknown))
        if any(not p.is_file() or p.is_symlink() for p in existing.values()):
            fail('preserved_metadata_not_regular_file', 'docs/testing/v1.4')
    if not (out / V14_STYLESHEET).is_file():
        fail('missing_v14_stylesheet', V14_STYLESHEET)
    for path, digest in ((args.data, args.data_sha256), (args.copy, args.copy_sha256)):
        if not HEX64.match(digest) or sha256_file(path) != digest:
            fail('input_sha256_mismatch', path.name)
    data, files = validate_data(json.loads(args.data.read_text(encoding='utf-8')))
    copy = validate_copy(json.loads(args.copy.read_text(encoding='utf-8')), data)
    public = {**data, 'copy_provenance': copy['provenance']}
    page = render_html(data, copy, files)
    out.mkdir(parents=True, exist_ok=True)
    for name in GENERATED_V14_FILES:
        if (out / name).exists():
            fail('output_exists_refusing_to_overwrite', name)
    (out / 'results-v1.4.json').write_text(json.dumps(public, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'index.html').write_text(page, encoding='utf-8')
    print(json.dumps({'built': 'docs/testing/v1.4', 'cases': len(data['cases']), 'metrics': len(data['metrics'])}))


def main(argv):
    if not argv:
        build_default()
        return 0
    if argv[0] != 'v14':
        print('unknown_mode: ' + argv[0], file=sys.stderr)
        return 2
    try:
        build_v14(argv[1:])
    except (EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
        print('evidence_build_refused: ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
