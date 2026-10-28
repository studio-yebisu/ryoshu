"""The committed Pages root is rendered from the template, finished copy and the published release."""
import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_landing', ROOT / 'scripts/build_landing.py')
landing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(landing)


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parsed(page):
    parser = Links(); parser.feed(page); return parser.tags


def downloads(page):
    return [a['href'] for tag, a in parsed(page) if tag == 'a' and 'download' in a]


def installer(version):
    return f'https://github.com/studio-yebisu/ryoshu/releases/download/v{version}/Ryoshu-{version}-windows-x64-setup.exe'


def write(path, text):
    path.write_text(text, encoding='utf-8'); return path


def test_committed_index_matches_template_copy_and_release():
    assert (ROOT / 'index.html').read_text('utf-8') == landing.build()


def test_single_download_points_at_published_installer():
    version = json.loads((ROOT / 'site/release.json').read_text('utf-8'))['version']
    page = (ROOT / 'index.html').read_text('utf-8')
    assert downloads(page) == [installer(version)]
    assert f'releases/tag/v{version}' in page
    assert 'TODO_KIMI' not in page


def test_future_source_version_does_not_move_published_download(tmp_path):
    published = landing.published_version()
    major, minor, patch = landing.semver(landing.app_version())
    future = write(tmp_path / '__init__.py', f'__version__ = "{major}.{minor}.{patch + 1}"\n')
    page = landing.build(source=future)
    assert page == landing.build()
    assert downloads(page) == [installer(published)]
    assert f'{major}.{minor}.{patch + 1}' not in page


@pytest.mark.parametrize('text', [
    '', '[]', '"1.4.2"', '{}', '{"version": 142}', '{"version": "1.4.2", "extra": 1}',
    '{"version": "1.4.2", "version": "1.4.2"}', '{"version": "v1.4.2"}', '{"version": "1.4"}',
    '{"version": "01.4.2"}', '{"version": "1.4.2-rc1"}', '{"version": " 1.4.2"}', '{"version": "1.4.2\\n"}',
])
def test_malformed_release_metadata_is_refused(tmp_path, text):
    with pytest.raises(ValueError):
        landing.published_version(write(tmp_path / 'release.json', text))


@pytest.mark.parametrize('version', [
    '1.3.2٢', '1.٣.2', '١.3.2', '1.3.２', '１.３.２', '1.3.2४',
])
def test_non_ascii_digits_are_refused(tmp_path, version):
    assert landing.semver(version) is None
    with pytest.raises(ValueError):
        landing.published_version(write(tmp_path / 'release.json', json.dumps({'version': version})))
    with pytest.raises(ValueError):
        landing.app_version(write(tmp_path / '__init__.py', f'__version__ = "{version}"\n'))


def test_release_ahead_of_source_is_refused(tmp_path):
    source = write(tmp_path / '__init__.py', '__version__ = "1.4.2"\n')
    assert landing.published_version(write(tmp_path / 'same.json', '{"version": "1.4.2"}'), source) == '1.4.2'
    assert landing.published_version(write(tmp_path / 'older.json', '{"version": "1.3.10"}'), source) == '1.3.10'
    for ahead in ('1.4.3', '1.5.0', '2.0.0', '1.4.10'):
        with pytest.raises(ValueError):
            landing.published_version(write(tmp_path / 'ahead.json', json.dumps({'version': ahead})), source)


def test_page_is_static_and_first_party():
    page = (ROOT / 'index.html').read_text('utf-8')
    tags = parsed(page)
    assert not any(tag in {'script', 'iframe', 'form', 'input', 'video'} for tag, _ in tags)
    hosts = {re.match(r'https://([^/]+)/', a.get('href') or a.get('src') or '').group(1)
             for _, a in tags if re.match(r'https://', a.get('href') or a.get('src') or '')}
    assert hosts <= {'github.com', 'studio-yebisu.github.io'}
    assert '@import' not in page and 'url(' not in page


def test_copy_keys_match_template_exactly():
    copy = json.loads((ROOT / 'site/copy.ja.json').read_text('utf-8'))
    used = {k[5:] for k in landing.FIELD.findall((ROOT / 'site/index.html.tmpl').read_text('utf-8')) if k.startswith('copy.')}
    assert set(copy) == used


def test_values_are_escaped_and_unfinished_copy_is_refused():
    template = '<p title="{{copy.a}}">{{copy.a}} {{version}}</p>'
    out = landing.render(template, {'a': '<b>"&\''}, '9.8.7')
    assert out == '<p title="&lt;b&gt;&quot;&amp;&#x27;">&lt;b&gt;&quot;&amp;&#x27; 9.8.7</p>'
    for bad in ({'a': 'TODO_KIMI a'}, {'a': ''}, {'a': 'x', 'b': 'unused'}, {'a': '{{version}}'}):
        with pytest.raises(ValueError):
            landing.render(template, bad, '9.8.7')
    with pytest.raises(ValueError):
        landing.render('{{copy.missing}}', {}, '9.8.7')
