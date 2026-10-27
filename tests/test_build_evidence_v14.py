"""Focused checks for scripts/build_evidence.py: default payload identity and the fail-closed v1.4 mode.

Everything runs in an isolated temporary repo with a synthetic 100-case manifest and ASCII machine-only
copy; no real result data, no Japanese text, no network or native process.
"""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts/build_evidence.py", root / "scripts/build_evidence.py")
    (root / "docs/testing").mkdir(parents=True)
    shutil.copy2(REPO / "docs/testing/results.json", root / "docs/testing/results.json")
    samples = root / "samples/receipts-100-v2"
    (samples / "thumbnails").mkdir(parents=True)
    cases = [{"case_id": "V%03d" % i, "file": "individual/V%03d.pdf" % i} for i in range(1, 101)]
    (samples / "manifest.json").write_text(json.dumps({"cases": cases}), "utf-8")
    (samples / "thumbnails/V001.jpg").write_bytes(b"thumb")
    # The v1.4 source stylesheet is a preserved, required source asset next to the generated page.
    (root / "docs/testing/v1.4").mkdir(parents=True)
    shutil.copy2(REPO / "docs/testing/v1.4/report-v14.css", root / "docs/testing/v1.4/report-v14.css")
    spec = importlib.util.spec_from_file_location("build_evidence_under_test", root / "scripts/build_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROOT == root
    return root, module


def synthetic_data(root, module):
    cases = []
    for i in range(1, 101):
        amount = "numeric_jpy" if i <= 95 else "fx_unresolved" if i <= 97 else "refund_excluded"
        account = "ordinary" if i <= 48 else "special"
        tax = "numeric" if i <= 52 else "blank" if i <= 63 else "not_rated"
        good = i % 7 != 0
        cases.append({
            "case_id": "V%03d" % i,
            "populations": {"amount": amount, "account": account, "tax": tax},
            "initial": {"date": "2024-01-01", "merchant": "Shop-%03d" % i, "total": 100 + i, "account": "acct-a" if good else "acct-b",
                        "decision": "propose", "tax_amount": 10 if tax == "numeric" else None},
            "expected": {"date": "2024-01-01", "merchant": "Shop-%03d" % i, "total": 100 + i,
                         "acceptable_accounts": ["acct-a", "acct-c"] if account == "ordinary" else [],
                         "decision": "propose" if account == "ordinary" else "hold", "tax_amount": 10 if tax == "numeric" else None},
            "checks": {"ocr_completed": True, "initial_output_completed": True,
                       "total_exact": None if amount == "refund_excluded" else good,
                       "date_exact": i != 50, "merchant_exact": good,
                       "account_exact": good if account == "ordinary" else None,
                       "decision_exact": good, "account_or_decision_exact": good,
                       "tax_exact": None if tax == "not_rated" else good},
        })
    metrics = module.recompute(cases)
    return {
        "schema": module.DATA_SCHEMA,
        "approved": {"by": "root", "mapping_sha256": "d" * 64, "private_sources_sha256": {"report": "e" * 64}},
        "definitions": dict(module.FIXED_DEFINITIONS),
        "pins": {
            "runtime": {"llama_cpp_tag": "b0000", "llama_cpp_commit": "a" * 40, "runtime_pin_sha256": "a" * 64, "crt_version": "1.0"},
            "model": {"repo": "synthetic/model", "revision": "b" * 40, "file": "model.gguf", "sha256": "c" * 64,
                      "chat_template_sha256": "f" * 64, "variant": "new-variant"},
            "config": {"ctx_size": 8192, "threads": 6, "n_gpu_layers": 0, "temperature": 0, "seed": 42, "max_tokens": 850,
                       "enable_thinking": False, "reasoning_flag": "off"},
            "source_files": {"receipt_ledger/runtime.py": "1" * 64, "tests/test_session_security.py": "2" * 64},
            "baseline": {"model_variant": "old-variant", "model_sha256": "9" * 64, "working_copy_files": {"receipt_ledger/semantic.py": "3" * 64}},
            "dataset": {"fixture_manifest_sha256": sha(root / "samples/receipts-100-v2/manifest.json"), "cases": 100},
        },
        "timing": {"elapsed_seconds": 1.5, "poll_interval_seconds": 5, "upload_batches": 2, "batch_size": 50,
                   "first_post_utc": "2026-01-01T00:00:00Z", "all_terminal_observed_utc": "2026-01-01T00:00:01.5Z"},
        "populations": json.loads(json.dumps(module.POPULATIONS)),
        "metrics": metrics,
        "cases": cases,
        "known_limitations": ["native_sqlcipher_openssl_provider"],
        "legacy_audit": {"total_exact_legacy": {
            "matched": metrics["amount_numeric_jpy_exact"]["matched"] + metrics["amount_fx_unresolved_state_kept"]["matched"],
            "evaluated": 97, "note_id": "legacy_scorer_total_exact_not_numeric_jpy_population"}},
    }


def synthetic_copy(module, data):
    slots = {slot: "slot " + slot for slot in module.BASE_COPY_SLOTS}
    slots.update({"limitation_" + i: "limit " + i for i in data["known_limitations"]})
    return {"schema": module.COPY_SCHEMA,
            "provenance": {"author_alias": "synthetic", "configured_model_id": "synthetic", "manuscript_sha256": "0" * 64, "root_adopted": True},
            "slots": slots}


def run(root, module, data, copy, capsys, data_sha=None, copy_sha=None):
    data_path, copy_path = root.parent / "data.json", root.parent / "copy.json"
    data_path.write_text(json.dumps(data), "utf-8")
    copy_path.write_text(json.dumps(copy), "utf-8")
    code = module.main(["v14", "--data", str(data_path), "--data-sha256", data_sha or sha(data_path),
                        "--copy", str(copy_path), "--copy-sha256", copy_sha or sha(copy_path)])
    return code, capsys.readouterr().err


def test_default_mode_payload_equals_the_published_results_js(repo):
    # Same contract as scripts/check_publication.py: decoded text equality (Path.write_text uses the platform newline).
    root, module = repo
    module.build_default()
    built = (root / "docs/testing/results.js").read_text("utf-8")
    report = json.loads((REPO / "docs/testing/results.json").read_text("utf-8"))
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    assert built == "window.RYOSHU_RESULTS = " + payload + ";\n" == (REPO / "docs/testing/results.js").read_text("utf-8")


def test_valid_fixed_population_projection_builds_and_preserves_source_change_metadata(repo, capsys):
    root, module = repo
    out = root / "docs/testing/v1.4"
    (out / "source-changes.json").write_text('{"kept": true}\n', "utf-8")
    before = {name: sha(out / name) for name in ("source-changes.json", "report-v14.css")}
    data = synthetic_data(root, module)
    code, err = run(root, module, data, synthetic_copy(module, data), capsys)
    assert code == 0, err
    assert sorted(p.name for p in out.iterdir()) == ["index.html", "report-v14.css", "results-v1.4.json", "source-changes.json"]
    assert {name: sha(out / name) for name in before} == before
    public = json.loads((out / "results-v1.4.json").read_text("utf-8"))
    assert public["metrics"] == data["metrics"] and public["copy_provenance"]["root_adopted"] is True
    assert public["cases"][0]["expected"]["acceptable_accounts"] == ["acct-a", "acct-c"]
    page = (out / "index.html").read_text("utf-8")
    assert 'href="../../../samples/receipts-100-v2/individual/V001.pdf"' in page
    assert 'src="../../../samples/receipts-100-v2/thumbnails/V001.jpg"' in page
    assert 'href="../../../samples/receipts-100-v2/individual/V002.pdf"' in page and "thumbnails/V002.jpg" not in page
    assert 'id="V100"' in page and "acct-a / acct-c" in page
    assert all("slot check_" + k in page for k in module.CHECK_KEYS) and "slot method_population_tax" in page


def mutate(path, value):
    def apply(data, copy):
        target = data if path[0] == "data" else copy
        for key in path[1:-1]:
            target = target[key]
        if value is DELETE:
            del target[path[-1]]
        else:
            target[path[-1]] = value(target.get(path[-1])) if callable(value) else value
    return apply


DELETE = object()
NEGATIVE = {
    "unknown_top_key": (mutate(("data", "extra"), 1), "unexpected_or_missing_keys"),
    "secret_key": (mutate(("data", "approved", "session_secret"), "x"), "forbidden_key"),
    "tokens_outside_config": (mutate(("data", "timing", "max_tokens"), 850), "forbidden_key"),
    "receipt_id_in_case": (mutate(("data", "cases", 0, "receipt_id"), "r1"), "forbidden_key"),
    "non_finite": (mutate(("data", "timing", "elapsed_seconds"), float("inf")), "non_finite_number"),
    "nan_temperature": (mutate(("data", "pins", "config", "temperature"), float("nan")), "non_finite_number"),
    "float_max_tokens": (mutate(("data", "pins", "config", "max_tokens"), 850.0), "invalid_integer"),
    "empty_identity": (mutate(("data", "pins", "model", "variant"), " "), "empty_identity_string"),
    "same_baseline": (mutate(("data", "pins", "baseline", "model_variant"), "new-variant"), "model_variant_not_changed"),
    "population_split": (mutate(("data", "cases", 0, "populations", "amount"), "fx_unresolved"), "population_sizes_mismatch"),
    "unknown_case_id": (mutate(("data", "cases", 0, "case_id"), "V101"), "case_ids_mismatch_manifest"),
    "metric_mismatch": (mutate(("data", "metrics", "date_exact", "matched"), lambda v: v + 1), "declared_metrics_differ_from_cases"),
    "legacy_matched": (mutate(("data", "legacy_audit", "total_exact_legacy", "matched"), lambda v: v + 1), "legacy_total_differs"),
    "legacy_denominator": (mutate(("data", "legacy_audit", "total_exact_legacy", "evaluated"), 96), "invalid_integer"),
    "ordinary_without_accounts": (mutate(("data", "cases", 0, "expected", "acceptable_accounts"), []), "ordinary_case_needs_acceptable_accounts"),
    "absolute_path": (mutate(("data", "cases", 0, "initial", "merchant"), "C:\\Users\\someone"), "absolute_or_private_path"),
    "missing_required_limitation": (lambda d, c: (d.__setitem__("known_limitations", ["other_limit"]),
                                                   c["slots"].pop("limitation_native_sqlcipher_openssl_provider"),
                                                   c["slots"].__setitem__("limitation_other_limit", "x")), "invalid_or_missing_required_limitations"),
    "missing_copy_slot": (mutate(("copy", "slots", "legend_marks"), DELETE), "unexpected_or_missing_keys"),
    "extra_copy_slot": (mutate(("copy", "slots", "invented_slot"), "x"), "unexpected_or_missing_keys"),
    "placeholder_copy": (mutate(("copy", "slots", "page_title"), "TODO later"), "placeholder_value"),
    "copy_not_adopted": (mutate(("copy", "provenance", "root_adopted"), False), "copy_not_adopted"),
}


@pytest.mark.parametrize("name", sorted(NEGATIVE))
def test_invalid_inputs_are_refused_and_nothing_is_written(repo, capsys, name):
    root, module = repo
    data = synthetic_data(root, module)
    copy = synthetic_copy(module, data)
    change, reason = NEGATIVE[name]
    change(data, copy)
    code, err = run(root, module, data, copy, capsys)
    assert code == 1 and reason in err, err
    assert not (root / "docs/testing/v1.4/results-v1.4.json").exists() and not (root / "docs/testing/v1.4/index.html").exists()


@pytest.mark.parametrize("which", ["data", "copy"])
def test_input_sha_mismatch_is_refused(repo, capsys, which):
    root, module = repo
    data = synthetic_data(root, module)
    code, err = run(root, module, data, synthetic_copy(module, data), capsys,
                    **({"data_sha": "0" * 64} if which == "data" else {"copy_sha": "0" * 64}))
    assert code == 1 and "input_sha256_mismatch" in err


@pytest.mark.parametrize("existing", ["index.html", "results-v1.4.json", "notes.txt"])
def test_existing_generated_or_unknown_files_are_never_overwritten(repo, capsys, existing):
    root, module = repo
    out = root / "docs/testing/v1.4"
    (out / existing).write_text("keep", "utf-8")
    data = synthetic_data(root, module)
    code, err = run(root, module, data, synthetic_copy(module, data), capsys)
    assert code == 1 and "output_exists_refusing_to_overwrite" in err
    assert (out / existing).read_text("utf-8") == "keep"


def test_missing_v14_stylesheet_is_refused_instead_of_building_an_unstyled_page(repo, capsys):
    root, module = repo
    (root / "docs/testing/v1.4/report-v14.css").unlink()
    data = synthetic_data(root, module)
    code, err = run(root, module, data, synthetic_copy(module, data), capsys)
    assert code == 1 and "missing_v14_stylesheet" in err
    assert not (root / "docs/testing/v1.4/index.html").exists()


def test_layout_contract_cards_keep_every_case_value_check_and_original(repo, capsys):
    root, module = repo
    data = synthetic_data(root, module)
    code, err = run(root, module, data, synthetic_copy(module, data), capsys)
    assert code == 0, err
    page = (root / "docs/testing/v1.4/index.html").read_text("utf-8")
    # Page structure: own scoped stylesheet, no globally styled <header>, one compact summary table only.
    assert '<link rel="stylesheet" href="../report.css"><link rel="stylesheet" href="report-v14.css">' in page
    assert '<body class="v14">' in page and "<header" not in page
    assert page.count("<table") == 1 and '<table class="v14-summary">' in page
    assert page.count('<li class="v14-case') == 100
    for case in data["cases"]:
        cid = case["case_id"]
        card = page.split('id="' + cid + '"', 1)[1].split('<li class="v14-case', 1)[0]
        assert 'href="../../../samples/receipts-100-v2/individual/' + cid + '.pdf"' in card
        assert card.count("<dt>") == len(module.FIELD_PAIRS) and card.count("data-check=") == len(module.CHECK_KEYS)
        fails = sum(v is False for v in case["checks"].values())
        assert card.count('class="is-fail"') == fails and card.count('class="is-na"') == sum(v is None for v in case["checks"].values())
        assert ("has-mismatch" in page.split('id="' + cid + '"', 1)[0].rsplit("<li", 1)[1]) == bool(fails)
    for section in ("summary", "method", "limitations", "cases"):
        assert 'id="' + section + '"' in page


def test_v14_stylesheet_is_scoped_so_historical_pages_are_unaffected():
    css = (REPO / "docs/testing/v1.4/report-v14.css").read_text("utf-8")
    import re
    selectors = [s.strip() for block in re.sub(r"/\*.*?\*/", "", css, flags=re.S).split("}")
                 for s in block.split("{")[0].split(",") if "{" in block and not s.strip().startswith("@")]
    assert selectors and all(s.startswith(".v14") for s in selectors), [s for s in selectors if not s.startswith(".v14")]


def test_max_tokens_is_accepted_only_at_the_config_path(repo):
    _, module = repo
    module.scan({"pins": {"config": {"max_tokens": 850}}})
    with pytest.raises(module.EvidenceError):
        module.scan({"pins": {"model": {"max_tokens": 850}}})


def test_html_escapes_copy_and_case_values(repo, capsys):
    root, module = repo
    data = synthetic_data(root, module)
    data["cases"][0]["initial"]["merchant"] = "<b>shop</b>"
    copy = synthetic_copy(module, data)
    copy["slots"]["page_title"] = "<script>alert(1)</script>"
    code, err = run(root, module, data, copy, capsys)
    assert code == 0, err
    page = (root / "docs/testing/v1.4/index.html").read_text("utf-8")
    assert "<script>" not in page and "&lt;script&gt;" in page and "&lt;b&gt;shop&lt;/b&gt;" in page
