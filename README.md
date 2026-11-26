# Seenical GitBook

Seenical uses this template for hosted documentation sites. Each language has its own homepage, navigation, articles and assets. The default language controls the homepage served at `/`.

Run `make build` with Node.js 10.24.1, GitBook CLI 2.3.2, GitBook 3.2.3, Python 3 and Make. The public site is built in `.build/_book`. Use `make package` to package that directory as `book.tar.gz` for deployment.

New sites publish each language under its own prefix. On an upgraded site, `legacy_root_language` in `site-layout.json` retains the original language's existing article and asset paths. Change `default_language` to switch the root homepage. Keep the language entries in `site-layout.json` and `LANGS.md` in sync.

For an upgrade PR, review the `seenical-site-preview` workflow artifact before merging. Extract it and serve the `_book` directory over HTTP to check the homepage and old links. The production workflow packages the merged revision.
