# 使用コンポーネントと出典

2026-10-21に公式の配布情報とインストール済み環境を確認。

| 用途 | コンポーネント | 出典・条件 |
| --- | --- | --- |
| OCR | Indra OCR 1.1.2 | [PyPI](https://pypi.org/project/indra-ocr/1.1.2/)、[公式ソース](https://github.com/datalab-to/indra)。コードはApache-2.0 |
| OCR重み | datalab-to/indra-ocr | [公式モデルカード](https://huggingface.co/datalab-to/indra-ocr)、[重みのライセンス](https://huggingface.co/datalab-to/indra-ocr/blob/main/LICENSE)。modified OpenRAIL-M。コードと重みの条件は異なる |
| 意味判断 | Qwen3 4B / Q4_K_M | [Qwen公式モデル](https://huggingface.co/Qwen/Qwen3-4B)、[Ollama配布タグ](https://ollama.com/library/qwen3:4b)。Apache-2.0、約2.5GB |
| ローカル推論 | Ollama | [公式ソース](https://github.com/ollama/ollama)、[構造化出力](https://docs.ollama.com/capabilities/structured-outputs) |
| API・画面配信 | FastAPI、Uvicorn | 依存の固定版は `requirements.lock.txt`。画面はバンドル不要のHTML/CSS/JS |
| 保存 | Python SQLite | SQLiteスナップショットでバックアップ。原本を別ファイルで保持 |
| 経費の考え方 | 国税庁 必要経費の知識 | [No.2210](https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2210.htm)。個別支出の適否を自動確定する根拠には使わない |

Indraの重みは、コードのApache-2.0と異なる **AI Pubs Open RAIL-M (Modified)** です。[検証に使った固定revisionの原文](https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE)のAttachment Aには、前年売上がUS$20,000,000を超える事業者、累計の株式・負債による調達額がUS$20,000,000を超える事業者の商用利用や、提供元・関連会社と競合する製品・サービスを提供する場合等の制限があります。売上・調達額の条項には個人・研究用途の例外があります。ここでの説明は全文や個別の利用可否判断の代わりにはなりません。導入・再配布・事業化の前に原文と現在の条件を確認してください。アプリのMIT Licenseでこれらの制限は解除されません。

このソースフォルダーと配布ZIPにはOCR・LLMの重みを同梱していません。セットアップ時に公式配布元から取得します。コードを公開できることと、任意の利用者・競合サービスがその重みを使えることは別の条件です。

取得したIndraソースのmainは `0e96b93c6810f17cf8352acbb4487cc9e45b66d5`、OCRキャッシュのモデルrevisionは `bd02167fdf2005a19962cc15188f8496a2c87d3b`。

実行したQwen3のOllama digest：`359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`。実際に使ったdigestを各候補にも保存します。

無償の取得経路とローカル推論だけを使用しています。DatalabやOllamaのクラウド推論、クレジット付きサービス、有料OCRへの切り替えは実装していません。

## その他の依存と検証用データ

Ollamaのコードは[MIT](https://github.com/ollama/ollama/blob/main/LICENSE)。FastAPIはMIT、UvicornはBSD-3-Clause、PillowはMIT-CMU、ONNX RuntimeはMIT、OpenCVはApache-2.0、pypdfium2はBSD-3-Clause / Apache-2.0と依存先の条件があります。依存はpip等で各配布元から導入し、バイナリをこのリポジトリへ再配布しません。固定版の一覧は[requirements.lock.txt](requirements.lock.txt)、検証環境のライセンス・出典メタデータは[docs/dependencies.json](docs/dependencies.json)です。

架空PDFは保存済みのモデル生成コードから作成しました。単体のフォントファイルは同梱していません。電子PDFには生成時のフォントが埋め込まれています。生成コードで参照したWindowsフォント6ファイルは、各書体の埋込フラグ`fsType=8`（editable embedding）を確認しました。[フラグの記録](docs/font-embedding.json) / [Microsoftの文書埋込・再配布条件](https://learn.microsoft.com/en-us/typography/fonts/font-faq)

Windowsに付属するフォント等の埋込・利用条件は各提供元に帰属します。再生成用コードはフォントの存在と利用権を前提にしており、アプリコードのMITをフォントやモデルに適用するものではありません。[作成条件](samples/receipts-100-v2/README.md)
