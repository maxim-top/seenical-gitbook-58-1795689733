import json
from pathlib import Path
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from language_layout import HtmlLinks, finalize, publish_build, rewrite_url


class LanguageLayoutTest(unittest.TestCase):
    def test_shared_assets_move_relative_to_published_page(self):
        self.assertEqual('../../gitbook/app.js', rewrite_url(
            '../../../gitbook/app.js', 'zh-hans/articles/section/demo.html',
            'articles/section/demo.html', 'zh-hans', 'https://fixture.invalid/'))

    def test_language_switch_from_default_and_secondary(self):
        self.assertEqual('en/', rewrite_url('../en/', 'zh-hans/index.html',
            'index.html', 'zh-hans', 'https://fixture.invalid/'))
        self.assertEqual('../', rewrite_url('../zh-hans/', 'en/index.html',
            'en/index.html', 'zh-hans', 'https://fixture.invalid/'))

    def test_encoded_filename_query_and_fragment(self):
        self.assertEqual('/articles/%E4%B8%AD%E6%96%87.html?q=1#details', rewrite_url(
            '/zh-hans/articles/%E4%B8%AD%E6%96%87.html?q=1#details',
            'zh-hans/index.html', 'index.html', 'zh-hans', 'https://fixture.invalid/'))

    def test_preview_keeps_same_site_links_on_preview_origin(self):
        official = 'https://fixture.invalid'
        url = official + '/zh-hans/articles/guide.html?mode=quick#start'
        arguments = (url, 'en/index.html', 'index.html', 'zh-hans', official)
        self.assertEqual('/articles/guide.html?mode=quick#start', rewrite_url(*arguments, preview=True))
        self.assertEqual(official + '/articles/guide.html?mode=quick#start', rewrite_url(*arguments))

        external = 'https://elsewhere.invalid/en/guide.html'
        self.assertEqual(external, rewrite_url(external, 'en/index.html', 'index.html', '', official, preview=True))
        href = f'<link rel="canonical" href="{official}/en/"><a href="{official}/en/">Guide</a>'
        rewrite = lambda value: rewrite_url(value, 'en/index.html', 'index.html', '', official, preview=True)
        self.assertEqual(f'<link rel="canonical" href="{official}/en/"><a href="/en/">Guide</a>',
                         HtmlLinks(href, rewrite).render())

    def test_preview_treats_same_site_host_without_a_path_as_root(self):
        official = 'https://fixture.invalid'
        for suffix in ('', '?lang=en#top'):
            value = official + suffix
            self.assertEqual('/' + suffix, rewrite_url(value, 'en/index.html', 'index.html', '', official, preview=True))
            self.assertEqual(value, rewrite_url(value, 'en/index.html', 'index.html', '', official))
        external = 'https://elsewhere.invalid?lang=en#top'
        self.assertEqual(external, rewrite_url(external, 'en/index.html', 'index.html', '', official, preview=True))

    def test_external_urls_and_anchor_only_are_untouched(self):
        for value in ('https://other.invalid/zh-hans/a', 'mailto:a@b.invalid', '#details', 'data:image/png;base64,a'):
            self.assertEqual(value, rewrite_url(value, 'zh-hans/index.html',
                'index.html', 'zh-hans', 'https://fixture.invalid/'))

    def test_subpath_hosting_does_not_rewrite_outside_site(self):
        self.assertEqual('https://fixture.invalid/docs/articles/a.html', rewrite_url(
            'https://fixture.invalid/docs/zh-hans/articles/a.html', 'zh-hans/index.html',
            'index.html', 'zh-hans', 'https://fixture.invalid/docs/'))
        self.assertEqual('/zh-hans/a.html', rewrite_url('/zh-hans/a.html',
            'zh-hans/index.html', 'index.html', 'zh-hans', 'https://fixture.invalid/docs/'))

    def test_html_rewrites_only_actual_url_attributes(self):
        source = """<script>const config = 'href="/zh-hans/"';</script>
<!-- <a href="/zh-hans/"> -->
<a data-copy='href="/zh-hans/"' href="/zh-hans/">home</a>
<link rel="canonical" href="https://fixture.invalid/zh-hans/">"""
        expected = source.replace('href="/zh-hans/">home', 'href="/">home')
        rewrite = lambda url: rewrite_url(url, 'zh-hans/index.html', 'index.html', 'zh-hans', 'https://fixture.invalid/')
        self.assertEqual(expected, HtmlLinks(source, rewrite).render())

    def test_failed_rebuild_preserves_previous_managed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            destination = root / '.build/_book'
            publish_build(source, destination, 'zh-hans', 'https://fixture.invalid/', None)
            (source / 'asset.txt').write_text('shared')
            (source / 'zh-hans/asset.txt').write_text('language-specific')
            with self.assertRaisesRegex(ValueError, 'collision'):
                publish_build(source, destination, 'zh-hans', 'https://fixture.invalid/', None)
            self.assertEqual('<h1>Chinese homepage</h1>', (destination / 'index.html').read_text())

    def source(self, root):
        source = root / 'native'
        (source / 'zh-hans').mkdir(parents=True)
        (source / 'index.html').write_text('Native language chooser')
        (source / 'zh-hans/index.html').write_text('<h1>Chinese homepage</h1>')
        return source

    def test_output_keeps_hidden_files_and_native_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            (source / 'zh-hans/.well-known').mkdir()
            (source / 'zh-hans/.well-known/test').write_bytes(b'attachment')
            finalize(source, root / 'site', 'zh-hans', 'https://fixture.invalid/')
            self.assertEqual(b'attachment', (root / 'site/.well-known/test').read_bytes())
            self.assertEqual('Native language chooser', (source / 'index.html').read_text())

    def test_collision_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            (source / 'asset.txt').write_text('shared')
            (source / 'zh-hans/asset.txt').write_text('language-specific')
            with self.assertRaisesRegex(ValueError, 'collision'):
                finalize(source, root / 'site', 'zh-hans', 'https://fixture.invalid/')
            self.assertFalse((root / 'site').exists())

    def test_new_language_namespace_cannot_absorb_old_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            (source / 'en').mkdir()
            (source / 'en/index.html').write_text('English')
            (source / 'zh-hans/en').mkdir()
            (source / 'zh-hans/en/old.html').write_text('Old Chinese page')
            with self.assertRaisesRegex(ValueError, 'already occupied'):
                finalize(source, root / 'site', 'zh-hans', 'https://fixture.invalid/')
            self.assertFalse((root / 'site').exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            (root / 'site').mkdir()
            (root / 'site/keep.txt').write_text('keep')
            with self.assertRaisesRegex(ValueError, 'new directory'):
                finalize(source, root / 'site', 'zh-hans', 'https://fixture.invalid/')
            self.assertEqual('keep', (root / 'site/keep.txt').read_text())

    def test_sitemap_must_cover_default_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            (source / 'sitemap.xml').write_text('<urlset/>')
            (source / 'zh-hans/sitemap.xml').write_text('<urlset><url><loc>https://fixture.invalid/zh-hans/</loc></url></urlset>')
            with self.assertRaisesRegex(ValueError, 'does not cover'):
                finalize(source, root / 'site', 'zh-hans', 'https://fixture.invalid/')
            self.assertFalse((root / 'site').exists())

    def test_default_search_index_wins_over_language_chooser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root)
            (source / 'search_index.json').write_text('{"store": {"wrong": {}}}')
            (source / 'zh-hans/search_index.json').write_text('{"store": {"./": {"title": "Chinese"}}}')
            finalize(source, root / 'site', 'zh-hans', 'https://fixture.invalid/')
            result = json.loads((root / 'site/search_index.json').read_text())
            self.assertEqual({'./': {'title': 'Chinese'}}, result['store'])

    def test_cannot_publish_inside_native_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(Path(directory))
            with self.assertRaisesRegex(ValueError, 'contain each other'):
                finalize(source, source / 'site', 'zh-hans', 'https://fixture.invalid/')


if __name__ == '__main__':
    unittest.main()
