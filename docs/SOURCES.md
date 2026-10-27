# 参照資料

この一覧では、コード・モデルの重み・税務資料・CSV形式を分けて出所を記録します。確認日は2026-10-21です。freeeの科目一覧と国税庁No.2210・No.2100は2026-10-23に追加で確認しました。配布元や仕様が変わった場合は再確認してください。

Ryoshuのカテゴリー・説明・確認事項は独自に作成しています。freeeの分類体系や、税務上の統一された判断基準を転記したものではありません。

| 対象 | 一次資料 | このアプリでの使い方 |
| --- | --- | --- |
| Indra OCR 1.1.2 | [PyPI](https://pypi.org/project/indra-ocr/1.1.2/) / [公式ソース](https://github.com/datalab-to/indra/tree/0e96b93c6810f17cf8352acbb4487cc9e45b66d5) | コードの版とApache-2.0を確認。上流の性能値をこのアプリの実測として引用しない |
| OCR重み | [固定revisionのモデルカード](https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/README.md) / [ライセンス原文](https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE) | コードと異なるmodified OpenRAIL-M。事業規模・資金調達・競合用途の制限を案内 |
| Qwen3 4B | [Qwen公式](https://huggingface.co/Qwen/Qwen3-4B) / [Ollamaタグ](https://ollama.com/library/qwen3:4b) | ローカルPC上で購入内容と科目の候補を作成。重みは同梱せず、実行したdigestを候補に保存 |
| Ollama | [公式ソース](https://github.com/ollama/ollama) / [構造化出力](https://docs.ollama.com/capabilities/structured-outputs) | JSON schemaで候補を受け取り、さらにアプリ側で根拠と科目を検証 |
| 必要経費 | [国税庁 No.2210](https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2210.htm) | 業務と支出の関係、家事関連費の確認事項。個別の適否の自動確定には使わない |
| 科目名の参考 | [freee：勘定科目の一覧](https://navi.freee.co.jp/account_items) | 個人事業主の経費・仕入・資産に使う科目名を参考にする。説明・例・選択カテゴリーは独自に用意 |
| 資産 | [国税庁 No.2100](https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2100.htm) | 資産候補を通常の経費合計から分ける。償却計算・特例判定は未実装 |
| freee・MF・弥生 | [CSV仕様と各社の公式資料](CSV-EXPORT.md) | 列・符号・文字コード・長さ・税区分を照合。実アカウントでの取込確認とは区別 |

固定したPython依存は[requirements.lock.txt](../requirements.lock.txt)を、確認環境の配布メタデータは[dependencies.json](dependencies.json)を参照してください。モデルと推移的な依存の利用条件は、この一覧で代替できません。

架空データの作成経緯は[manifest.json](../samples/receipts-100-v2/manifest.json)と[sources](../samples/receipts-100-v2/sources/BRIEF.md)に記録しています。実在の領収書を外部から集めたデータセットではありません。
