"""Turn native GitBook language directories into the site's public layout."""
import argparse
import html
import json
import os
import posixpath
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote, quote

ATTR = re.compile(r'''(?P<space>\s+)(?P<key>[^\s=/>]+)\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))''')
URL_ATTRIBUTES = ('href', 'src', 'poster', 'data-basepath')
CSS_URL = re.compile(r'''url\(\s*(?P<q>["']?)(?P<url>[^)'"\s]+)(?P=q)\s*\)''', re.I)
LOC = re.compile(r'(<loc>)(.*?)(</loc>)')


def mapped(path, default):
    if path == default:
        return ''
    if path.startswith(default + '/'):
        return path[len(default) + 1:]
    return path


def rewrite_url(url, old_file, new_file, default, site_url, *, preview=False):
    parsed = urlsplit(url)
    if parsed.scheme not in ('', 'http', 'https') or (not parsed.path and not (preview and parsed.netloc)):
        return url
    site = urlsplit(site_url)
    if parsed.netloc and (parsed.netloc != site.netloc or (parsed.scheme and parsed.scheme != site.scheme)):
        return url
    path = unquote(parsed.path or '/')
    absolute = path.startswith('/') or bool(parsed.netloc)
    base = site.path.rstrip('/')
    if absolute:
        # Outside a configured deployment base is not owned by this site.
        if base and path != base and not path.startswith(base + '/'):
            return url
        old_target = path[len(base):].lstrip('/')
    else:
        old_target = posixpath.normpath(posixpath.join(posixpath.dirname(old_file), path))
    target = mapped(old_target, default)
    if absolute:
        new_path = base + '/' + target
    else:
        new_path = posixpath.relpath(target or '.', posixpath.dirname(new_file) or '.')
    if path.endswith('/') and not new_path.endswith('/'):
        new_path += '/'
    if preview and parsed.netloc:
        return urlunsplit(('', '', quote(new_path, safe='/@:+~!$&()*;,=-._'), parsed.query, parsed.fragment))
    if new_path == path:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, quote(new_path, safe='/@:+~!$&()*;,=-._'), parsed.query, parsed.fragment))


def rewrite_css(text, rewrite):
    return CSS_URL.sub(lambda m: 'url(' + m['q'] + rewrite(m['url']) + m['q'] + ')', text)


class HtmlLinks(HTMLParser):
    """Rewrite actual tag attributes while preserving script and HTML source text."""

    def __init__(self, text, rewrite):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.rewrite = rewrite
        self.line_offsets = [0] + [match.end() for match in re.finditer('\n', text)]
        self.edits = []

    def handle_starttag(self, tag, attrs):
        original = self.get_starttag_text()
        canonical = tag == 'link' and dict(attrs).get('rel') == 'canonical'

        def replace(match):
            key = match['key'].lower()
            group = match.lastgroup
            url = match[group]
            if key == 'style':
                new_value = rewrite_css(url, self.rewrite)
            elif key in URL_ATTRIBUTES and not (canonical and key == 'href'):
                new_value = self.rewrite(url)
            else:
                return match[0]
            first, last = match.span(group)
            return match[0][:first - match.start()] + new_value + match[0][last - match.start():]

        updated = ATTR.sub(replace, original)
        if updated != original:
            line, column = self.getpos()
            start = self.line_offsets[line - 1] + column
            self.edits.append((start, start + len(original), updated))

    def render(self):
        self.feed(self.text)
        self.close()
        parts, previous = [], 0
        for start, end, updated in self.edits:
            parts.extend((self.text[previous:start], updated))
            previous = end
        parts.append(self.text[previous:])
        return ''.join(parts)


def sitemap_locations(path):
    return {element.text for element in ET.parse(path).iter()
            if element.tag.split('}')[-1] == 'loc'}


def finalize(source, destination, default, site_url, sitemap_url=None, layout=None, *, preview=False):
    source, destination = Path(source), Path(destination)
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9-]*', default):
        raise ValueError('invalid default language directory')
    if not (source / default / 'index.html').is_file():
        raise ValueError('native default-language homepage missing')
    if source.resolve() in destination.resolve().parents or destination.resolve() in source.resolve().parents:
        raise ValueError('native and published directories must not contain each other')
    if destination.exists():
        raise ValueError('output must be a new directory; do not overwrite existing artifacts')
    root_language = layout.get('legacy_root_language', '') if layout else default
    languages = layout['languages'] if layout else {}
    # A language namespace cannot absorb existing default-language content.
    for sibling in source.iterdir():
        if root_language and sibling.is_dir() and sibling.name != root_language and (sibling / 'index.html').is_file():
            if (source / root_language / sibling.name).exists():
                raise ValueError('language path already occupied: ' + sibling.name)
    # Plan every destination before copying anything. Only GitBook's chooser is replaced.
    files = {}
    for item in sorted(source.rglob('*')):
        if item.is_symlink():
            raise ValueError('symlink in native output: ' + str(item))
        if not item.is_file():
            continue
        old = item.relative_to(source).as_posix()
        if old == 'search_index.json' and (source / root_language / 'search_index.json').is_file() and bool(root_language):
            # Root chooser index is not a language index. Publish the default's index.
            continue
        if old == 'index.html':
            continue
        if old == root_language + '/sitemap.xml' and (source / 'sitemap.xml').is_file():
            # The real template plugin writes an aggregate root sitemap and a
            # language-local copy. Keep the aggregate only after proving coverage.
            if not sitemap_locations(item).issubset(sitemap_locations(source / 'sitemap.xml')):
                raise ValueError('root sitemap does not cover default-language sitemap')
            continue
        new = mapped(old, root_language)
        if new in files:
            raise ValueError('output collision: ' + new)
        files[new] = old
    if layout:
        files['index.html'] = default + '/index.html'
        if root_language:
            files[root_language + '/index.html'] = root_language + '/index.html'
    for new in files:
        for parent in Path(new).parents:
            if parent.as_posix() in files:
                raise ValueError('file/directory collision: ' + new)
    destination.mkdir(parents=True)
    for new, old in files.items():
        src, dst = source / old, destination / new
        dst.parent.mkdir(parents=True, exist_ok=True)
        rewrite = lambda url: rewrite_url(url, old, new, root_language, site_url, preview=preview)
        if src.suffix == '.html':
            page = HtmlLinks(src.read_text(), rewrite).render()
            if layout:
                canonical_path = default + '/' if new == 'index.html' else new
                if canonical_path.endswith('/index.html'):
                    canonical_path = canonical_path[:-len('index.html')]
                page = add_language_navigation(page, new, canonical_path, languages, site_url)
            dst.write_text(page)
        elif src.suffix == '.css':
            dst.write_text(rewrite_css(src.read_text(), rewrite))
        elif src.name == 'sitemap.xml':
            def rewrite_location(match):
                url = match[2]
                # The old language homepage also gets a stable switcher URL.
                if layout and root_language and url.rstrip('/').endswith('/' + root_language):
                    return match[0]
                return match[1] + rewrite_url(url, old, new, root_language, sitemap_url or site_url) + match[3]

            dst.write_text(LOC.sub(rewrite_location, src.read_text()))
        else:
            shutil.copy2(src, dst)
    favicon = destination / 'assets/favicon.ico'
    theme_favicon = destination / 'gitbook/images/favicon.ico'
    if favicon.is_file() and theme_favicon.is_file():
        shutil.copy2(favicon, theme_favicon)
    return files


def add_language_navigation(page, path, canonical_path, languages, site_url):
    canonical = html.escape(site_url.rstrip('/') + '/' + quote(canonical_path), quote=True)
    page = re.sub(r'<link\b(?=[^>]*\brel=["\']canonical["\'])[^>]*>', '', page, flags=re.I)
    if site_url:
        page = page.replace('</head>', f'<link rel="canonical" href="{canonical}">\n</head>', 1)
    if len(languages) < 2:
        return page
    # Carry a restricted site's query credential to its own language links.
    # URLs in the source, canonical and sitemap remain credential-free.
    directory = posixpath.dirname(path) or '.'
    names = {'zh-hans': '简体中文', 'en': 'English'}
    links = []
    for code in languages:
        target = posixpath.relpath(code, directory)
        links.append(f'<a href="{target}/" lang="{code}">{names[code]}</a>')
    navigation = ('<nav class="seenical-languages" aria-label="Languages" '
                  'style="position:fixed;right:12px;bottom:12px;z-index:100;background:white;'
                  'padding:8px;border:1px solid #ddd;border-radius:6px">'
                  + ' · '.join(links) + '</nav>'
                  '<script>(function () {'
                  'var token = new URLSearchParams(location.search).get("cdn_token");'
                  'if (!token) return;'
                  'document.querySelectorAll(".seenical-languages a").forEach(function (link) {'
                  'var url = new URL(link.href);'
                  'if (url.origin !== location.origin) return;'
                  'url.searchParams.set("cdn_token", token); link.href = url.href;'
                  '});})();</script>')
    return page.replace('</body>', navigation + '</body>', 1)


def publish_build(source, destination, default, site_url, sitemap_url, layout=None, *, preview=False):
    """Replace only the managed local output, after a complete build."""

    if destination.parent.is_symlink() or destination.is_symlink():
        raise ValueError('managed build paths must not be symlinks')
    destination.parent.mkdir(exist_ok=True)
    marker = destination.parent / '.language-build'
    if destination.exists() and (not destination.is_dir() or not marker.is_file()):
        raise ValueError('refusing to replace an unmanaged build directory: ' + str(destination))
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        staged, previous = Path(directory) / 'output', Path(directory) / 'previous'
        files = finalize(source, staged, default, site_url, sitemap_url, layout, preview=preview)
        if destination.exists():
            destination.rename(previous)
        try:
            staged.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
        marker.touch()
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('native_output', type=Path, nargs='?', default=Path('_book'))
    parser.add_argument('published_output', type=Path, nargs='?')
    parser.add_argument('--config', type=Path, default=Path('site-layout.json'))
    parser.add_argument('--site-url')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    default = config['default_language']
    if default not in config['languages']:
        raise ValueError('default language is missing')
    for code, language in config['languages'].items():
        expected = '/' if code == config.get('legacy_root_language') else '/' + code + '/'
        if config.get('version') != 2:
            expected = '/' if code == default else '/' + code + '/'
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9-]*', code):
            raise ValueError('invalid language directory: ' + code)
        if language != {'source_dir': code, 'public_path': expected}:
            raise ValueError('expected a language directory and public path ' + expected)
        if not (args.native_output / code / 'index.html').is_file():
            raise ValueError('language homepage missing: ' + code)
    root_language = config.get('legacy_root_language')
    if root_language and root_language not in config['languages']:
        raise ValueError('legacy root language is missing')
    book = json.loads((args.config.parent / 'book.json').read_text())
    plugins = book.get('pluginsConfig', {})
    active = {name.split('@', 1)[0] for name in book['plugins']} if 'plugins' in book else plugins
    plugin_key = next((name for name in ('seenical', 'lanying-grow-ai') if name in active), None)
    site_url = args.site_url or plugins.get('canonical-link', {}).get('baseURL', '')
    sitemap_url = plugins.get(plugin_key, {}).get('sitemap_hostname')
    destination = args.published_output or Path('.build/_book')
    publish = finalize if args.published_output else publish_build
    layout = config if config.get('version') == 2 else None
    files = publish(args.native_output, destination, default, site_url, sitemap_url, layout,
                    preview=bool(layout) and os.environ.get('SEENICAL_PREVIEW_BUILD') == '1')
    print('Published {} files to {}'.format(len(files), destination))


if __name__ == '__main__':
    main()
