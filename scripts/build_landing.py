"""Render the GitHub Pages root index.html from site/index.html.tmpl + site/copy.ja.json.

The installer link and every shown version derive from site/release.json, the last
*published* release. It is advanced only after that release's installer asset is live,
so a source version bump on main never points the page at a missing asset.
receipt_ledger/__init__.py is read only as a sanity bound (published <= source).
Legacy Pages serves main/(root) as-is, so the rendered index.html is committed.

  python scripts/build_landing.py           # write index.html
  python scripts/build_landing.py --check   # fail if index.html is stale, copy is unfinished or release.json is invalid
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'site/index.html.tmpl'
COPY = ROOT / 'site/copy.ja.json'
RELEASE = ROOT / 'site/release.json'
SOURCE = ROOT / 'receipt_ledger/__init__.py'
OUTPUT = ROOT / 'index.html'
REPO = 'https://github.com/studio-yebisu/ryoshu'
MOVIE_URL = 'https://studio-yebisu.github.io/yaoyorozu-ai-toolbox/media/ryoshu-copy-renewal-20261026/ryoshu-promo-v1.4.0-notice.mp4'
PLACEHOLDER = 'TODO_KIMI'
FIELD = re.compile(r'\{\{([a-z0-9_.]+)\}\}')
# ASCII digits only: \d also matches non-ASCII digits, which int() accepts but release URLs do not.
SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')


def semver(text):
    match = SEMVER.fullmatch(text) if isinstance(text, str) else None
    return tuple(map(int, match.groups())) if match else None


def app_version(source=SOURCE):
    match = re.search(r'^__version__ = "([^"]*)"$', Path(source).read_text('utf-8'), re.M)
    if match is None or semver(match.group(1)) is None:
        raise ValueError(f'{Path(source).name} has no canonical __version__')
    return match.group(1)


def _unique(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('release.json has duplicate keys')
    return dict(pairs)


def published_version(release=RELEASE, source=SOURCE):
    """The explicitly published release; must be canonical X.Y.Z and not ahead of the source."""
    try:
        data = json.loads(Path(release).read_text('utf-8'), object_pairs_hook=_unique)
    except json.JSONDecodeError as error:
        raise ValueError(f'release.json is not valid JSON: {error}') from None
    if not isinstance(data, dict) or set(data) != {'version'}:
        raise ValueError('release.json must be exactly {"version": "X.Y.Z"}')
    version = data['version']
    if semver(version) is None:
        raise ValueError(f'release.json version {version!r} is not canonical X.Y.Z')
    if semver(version) > semver(app_version(source)):
        raise ValueError(f'release.json version {version} is ahead of source {app_version(source)}')
    return version


def values(version, copy):
    tag = 'v' + version
    found = {
        'version': version,
        'installer_url': f'{REPO}/releases/download/{tag}/Ryoshu-{version}-windows-x64-setup.exe',
        'release_url': f'{REPO}/releases/tag/{tag}',
        'movie_url': MOVIE_URL,
    }
    for key, text in copy.items():
        found['copy.' + key] = text
    return found


def render(template, copy, version, allow_placeholder=False):
    problems = []
    for key, text in copy.items():
        if not isinstance(text, str) or not text.strip():
            problems.append(f'copy {key} is empty')
        elif PLACEHOLDER in text and not allow_placeholder:
            problems.append(f'copy {key} is still a placeholder')
        elif '{{' in text or '\n' in text:
            problems.append(f'copy {key} contains a template marker or newline')
    available = values(version, copy)
    used = set(FIELD.findall(template))
    problems += [f'template field {k} has no value' for k in sorted(used - set(available))]
    problems += [f'copy {k[5:]} is unused by the template' for k in sorted(set(available) - used) if k.startswith('copy.')]
    if problems:
        raise ValueError('; '.join(problems))
    # Every value is escaped for both text and double-quoted attribute contexts.
    return FIELD.sub(lambda m: html.escape(available[m.group(1)], quote=True), template)


def build(allow_placeholder=False, release=RELEASE, source=SOURCE):
    copy = json.loads(COPY.read_text('utf-8'))
    return render(TEMPLATE.read_text('utf-8'), copy, published_version(release, source), allow_placeholder)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='verify the committed index.html instead of writing it')
    parser.add_argument('--preview', type=Path, help='write a placeholder-tolerant preview here (never index.html)')
    args = parser.parse_args()
    try:
        if args.preview:
            if args.preview.resolve() == OUTPUT.resolve():
                parser.error('preview must not overwrite index.html')
            args.preview.write_text(build(allow_placeholder=True), encoding='utf-8', newline='\n')
            return
        page = build()
        version = published_version()
    except ValueError as error:
        sys.exit(f'FAIL: {error}')
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text('utf-8') != page:
            sys.exit('FAIL: index.html is stale; run python scripts/build_landing.py')
        print(f'Landing page is current for published v{version}.')
        return
    OUTPUT.write_text(page, encoding='utf-8', newline='\n')
    print(f'Wrote index.html for published v{version}.')


if __name__ == '__main__':
    main()
