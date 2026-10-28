# 紹介・ダウンロードページの保守

Opus 5.5が作成した `site/index.html.tmpl` と、Kimiが記述した `site/copy.ja.json` から、`python scripts/build_landing.py` でページを生成します。GitHub Pages（main／ルート）向けに、生成したルートの `index.html` と `.nojekyll` をコミットし、変更後は `python scripts/build_landing.py --check` で生成物の一致を確認します。現在の版とインストーラーURLは `site/release.json` から取得するため、文案やテンプレートに版を直書きしません。

動画・スクリーンショット・検証記録は、それぞれ明示したv1.4／v1.4.0の履歴資料として扱い、現行版の新規検証と区別します。分析、CDN、JavaScript、第三者への通信は追加しません。新版の紹介・ダウンロードページは、対応するGitHub Releaseのインストーラー資産が実際に存在するまで公開せず、公開前にリンクを確認してください。
