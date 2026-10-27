# 使用コンポーネントと出典

2026-10-22に公式の配布情報とインストール済み環境を確認。v1.4.0候補のllama.cpp・Qwen GGUF・Microsoft Visual C++ v14の固定情報を追加したが、実ファイル・同梱ライセンス・実推論の検証は継続中である。

| 用途 | コンポーネント | 出典・条件 |
| --- | --- | --- |
| OCR | Indra OCR 1.1.2 | [PyPI](https://pypi.org/project/indra-ocr/1.1.2/)、[公式ソース](https://github.com/datalab-to/indra)。コードはApache-2.0 |
| OCR重み | datalab-to/indra-ocr | [公式モデルカード](https://huggingface.co/datalab-to/indra-ocr)、[重みのライセンス](https://huggingface.co/datalab-to/indra-ocr/blob/main/LICENSE)。modified OpenRAIL-M。コードと重みの条件は異なる |
| 意味判断 | Qwen3 4B / Q4_K_M | [Qwen公式GGUFの固定revision](https://huggingface.co/Qwen/Qwen3-4B-GGUF/tree/bc640142c66e1fdd12af0bd68f40445458f3869b)、[ライセンス](https://huggingface.co/Qwen/Qwen3-4B-GGUF/blob/bc640142c66e1fdd12af0bd68f40445458f3869b/LICENSE)。Apache-2.0、`Qwen3-4B-Q4_K_M.gguf`、2,497,280,256バイト |
| ローカル推論 | llama.cpp b11700 | [公式リリース](https://github.com/ggml-org/llama.cpp/releases/tag/b11700)、[MITライセンス](https://github.com/ggml-org/llama.cpp/blob/23f7aabc16c72f6f707dba227251c35d7bd1ed96/LICENSE)。commit `23f7aabc16c72f6f707dba227251c35d7bd1ed96` |
| Microsoft実行環境 | Microsoft Visual C++ v14 Redistributable 14.51.36247.0 x64 | [Microsoft公式パッケージ](https://download.visualstudio.microsoft.com/download/pr/ebdab8e5-1d7b-4d9f-a11b-cbb1720c3b12/843068991DAAA1F73AD9F6239BCE4D0F6A07A51F18C37EA2A867E9BECA71295C/VC_redist.x64.exe)、[Microsoftの利用条件DOCX](https://visualstudio.microsoft.com/wp-content/uploads/2025/10/Visual-C-V14-License-Redistributable_and_Runtime_ENU.docx)。パッケージは18,731,856バイト、SHA-256 `843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c`。利用条件は39,553バイト、SHA-256 `08651651a7602fc7c0e2763de0fde1ff9f868df2780597cd1775ee9d6441c783`、EULA ID `Cpp_v14_ENU.1033` |
| API・画面配信 | FastAPI、Uvicorn | 依存の固定版は `requirements.lock.txt`。画面はバンドル不要のHTML/CSS/JS |
| 帳簿DB | sqlcipher3 0.6.4 / SQLCipher 4.19.0 | Pythonバインディングは[zlib](https://github.com/coleifer/sqlcipher3/blob/0.6.4/LICENSE)、SQLCipher本体は[BSD-3-Clause](https://github.com/sqlcipher/sqlcipher/blob/master/LICENSE.md)。Windows配布wheelの同梱ライブラリも各条件に従う |
| 原本・バックアップ暗号化 | cryptography 50.0.2 | [公式ライセンス](https://github.com/pyca/cryptography/blob/50.0.2/LICENSE)。Apache-2.0 OR BSD-3-Clause。依存するOpenSSL等にも各条件がある |
| 初回の環境準備 | uv 0.13.5 | [公式配布](https://github.com/astral-sh/uv/releases/tag/0.13.5)、[MIT](https://github.com/astral-sh/uv/blob/0.13.5/LICENSE-MIT) OR [Apache-2.0](https://github.com/astral-sh/uv/blob/0.13.5/LICENSE-APACHE)。ZIPと実行ファイルのSHA-256を固定・照合して取得 |
| 経費の考え方 | 国税庁 必要経費の知識 | [No.2210](https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2210.htm)。個別支出の適否を自動確定する根拠には使わない |

Microsoftの利用条件の表示名は「MICROSOFT SOFTWARE LICENSE TERMS - MICROSOFT VISUAL C++ V14 REDISTRIBUTABLE and RUNTIME (Last Updated: October 1, 2025)」です。セットアップでは、このDOCXの本文をDLLを配置する前に表示し、明示された同意がある場合だけ配置します。対象は`msvcp140.dll`、`msvcp140_1.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`です。Ryoshu専用のCRT・Python・llama.cpp保存先へ配置し、置き換えるPython DLLはハッシュ付きでバックアップします。パッケージを実行したり、管理者権限やWindows全体への導入を使ったりしません。

Indraの重みは、コードのApache-2.0と異なる **AI Pubs Open RAIL-M (Modified)** です。[検証に使った固定revisionの原文](https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE)のAttachment Aには、前年売上がUS$20,000,000を超える事業者、累計の株式・負債による調達額がUS$20,000,000を超える事業者の商用利用や、提供元・関連会社と競合する製品・サービスを提供する場合等の制限があります。売上・調達額の条項には個人・研究用途の例外があります。ここでの説明は全文や個別の利用可否判断の代わりにはなりません。導入・再配布・事業化の前に原文と現在の条件を確認してください。アプリのMIT Licenseでこれらの制限は解除されません。

このソースフォルダーと配布ZIPにはOCR・LLMの重み、llama.cppのバイナリ、MicrosoftのパッケージとDLLを同梱していません。セットアップ時に利用者が公式配布元から直接取得します。Python・uv・依存ライブラリの実行ファイルも初回準備で取得します。取得物に含まれるライセンス・著作権表示は各配布物のものを保持してください。llama.cppのMITライセンスは実行環境の隣に、QwenモデルのApache-2.0ライセンスはモデルの隣に配置します。コードを公開できることと、任意の利用者・競合サービスがその重みを使えることは別の条件です。Microsoftパッケージと利用条件の案内は、提供元の再配布権や個別の利用可否を法的に判断するものではありません。

取得したIndraソースのmainは `0e96b93c6810f17cf8352acbb4487cc9e45b66d5`、OCRキャッシュのモデルrevisionは `bd02167fdf2005a19962cc15188f8496a2c87d3b`。

v1.4.0候補で使うQwenモデルのSHA-256は `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` です。以前のOllamaモデル（Qwen3 4B Thinking 2507）とは別の重みであり、旧モデルのdigestや精度結果を候補版へ引き継ぎません。

過去の記録として、v1.3系で実行したQwen3のOllama digest：`359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`。これはllama.cpp候補版のモデル識別子ではありません。

無償の取得経路とローカル推論だけを使用しています。DatalabやOllamaのクラウド推論、クレジット付きサービス、有料OCRへの切り替えは実装していません。

## その他の依存と検証用データ

llama.cppのコードは[MIT](https://github.com/ggml-org/llama.cpp/blob/23f7aabc16c72f6f707dba227251c35d7bd1ed96/LICENSE)です。FastAPIはMIT、UvicornはBSD-3-Clause、PillowはMIT-CMU、ONNX RuntimeはMIT、OpenCVはApache-2.0、pypdfium2はBSD-3-Clause / Apache-2.0と依存先の条件があります。依存はpip等で各配布元から導入し、バイナリをこのリポジトリへ再配布しません。固定版の一覧は[requirements.lock.txt](requirements.lock.txt)、検証環境のライセンス・出典メタデータは[docs/dependencies.json](docs/dependencies.json)です。

架空PDFは保存済みのモデル生成コードから作成しました。単体のフォントファイルは同梱していません。電子PDFには生成時のフォントが埋め込まれています。生成コードで参照したWindowsフォント6ファイルは、各書体の埋込フラグ`fsType=8`（editable embedding）を確認しました。[フラグの記録](docs/font-embedding.json) / [Microsoftの文書埋込・再配布条件](https://learn.microsoft.com/en-us/typography/fonts/font-faq)

Windowsに付属するフォント等の埋込・利用条件は各提供元に帰属します。再生成用コードはフォントの存在と利用権を前提にしており、アプリコードのMITをフォントやモデルに適用するものではありません。[作成条件](samples/receipts-100-v2/README.md)
