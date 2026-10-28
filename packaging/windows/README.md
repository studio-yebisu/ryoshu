# Windowsセットアップのビルド

公開ソースZIPから、管理者権限が不要な利用者単位のセットアップEXEを作成します。Python・ライブラリ・OCR重み・LLM重み・llama.cppのバイナリは同梱せず、利用者が同意した初回準備で取得します。OllamaやLM Studioも同梱・導入しません。コード署名はありません。v1.4.0のビルド記録と受入記録は、Releaseの[`Ryoshu-1.4.0-windows-x64-setup.build.json`](https://github.com/studio-yebisu/ryoshu/releases/download/v1.4.0/Ryoshu-1.4.0-windows-x64-setup.build.json)と[`windows-installer-acceptance.json`](https://github.com/studio-yebisu/ryoshu/releases/download/v1.4.0/windows-installer-acceptance.json)で公開しています。

## ビルドツール

公式の[Inno Setup 7.1.0 x64](https://github.com/jrsoftware/issrc/releases/tag/is-7_1_0)を使います。ビルドスクリプトがコンパイラを自動ダウンロード・自動実行することはありません。

- 公式ファイル：`innosetup-7.1.0-x64.exe`
- SHA-256：`0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f`
- インストーラーのAuthenticode署名：検証時`Valid`、署名者`Pyrsys B.V.`
- `ISCC.exe`のSHA-256：`d06ebd38f38e3cee60a3c50cc45bd449d77e0bc6a5cabc607ea9886808e4de1a`。ビルド時にも照合します。
- [Inno Setupのライセンス](https://github.com/jrsoftware/issrc/blob/is-7_1_0/license.txt)

コンパイラ導入後に実行します。Node.jsは不要です。

```powershell
python scripts/build_windows_package.py --source-zip Ryoshu-1.4.1-source.zip --output-dir dist/windows-1.4.1 --iscc 'C:\path\to\ISCC.exe'
```

このコマンド例は公開済みv1.4.0の成果物名を使います。v1.4.0の[`.build.json`](https://github.com/studio-yebisu/ryoshu/releases/download/v1.4.0/Ryoshu-1.4.0-windows-x64-setup.build.json)もReleaseの公開ファイルです。

ZIP内のルートは`Ryoshu-<version>/`で、アプリの`__version__`と一致する必要があります。起動に使う`python-runtime.ps1`・`bootstrap-uv.ps1`・`launcher-process.ps1`も必須ファイルとして照合します。公開ソース以外の`.local`・DB・鍵・ログ・モデル・仮想環境、危険なパス、シンボリックリンクは拒否します。

出力は`Ryoshu-<version>-windows-x64-setup.exe`と`.build.json`です。後者には入力ZIP・コンパイラ・全同梱ファイル・出力EXEのSHA-256を記録します。同名の成果物は上書きしません。

## インストールの動作

- 対象はWindows 11 x64で、既定は`%LOCALAPPDATA%\Programs\StudioYebisu\Ryoshu`です。
- スタートメニューに「Ryoshu」「Ryoshuを終了」「Ryoshuの導入ガイド」を作成し、デスクトップは任意で選べます。
- OllamaやLM Studioの有無は起動条件にせず、既存の`start.ps1`へ進みます。llama.cpp実行環境とモデルは初回準備でRyoshu専用の保存先へ取得します。
- 暗号化帳簿は`%USERPROFILE%\StudioYebisu\ReceiptLedger`に分離し、帳簿の保存先へアプリを配置する操作を拒否します。
- 起動中の対象フォルダーは更新・削除しません。公開healthと非秘密の`runtime/server.json`の起動元（`source`）・PIDだけを参照し、帳簿・セッション・鍵・原本は開きません。起動元を確認できない場合も停止します。
- アンインストールでは配置したアプリとショートカットを削除します。帳簿・原本・復元情報・実行環境・AIモデルと利用者が追加したファイルは一括削除しません。

`packaging/windows/launch.ps1 -CheckOnly`は、通常の起動やセットアップを実行せず、帳簿へ接続せずにJSONを返します。Ryoshuが使う`%USERPROFILE%\StudioYebisu\ReceiptLedger\venv\Scripts\python.exe`を起動し、Python 3.12.15以降の3.12系・64bitと必要な暗号化ライブラリを確認します。検査の正本は[Get-RyoshuPythonStatus](../../scripts/python-runtime.ps1)です。Pythonの検査は`-I -B`で実行し、利用者のPython設定の影響と診断によるバイトコードの書き込みを避けます。

`python_prepared`は起動・互換性・ライブラリの検査結果で、実行ファイルの存在だけでは`true`になりません。`python_exists`・`python_runnable`・`python_compatible`・`python_version`・`python_reason`で状態を区別し、`source_complete`で補助スクリプトの同梱も確認します。OCR・llama.cpp実行環境・Qwenモデルの準備完了と推論品質はこの診断の対象外です。初回準備の完了は依存・OCRモデル・llama.cpp実行環境・Qwenモデルの取得成功後に別途記録します。

## 配布EXEの実インストール検証

既存のインストール登録がないWindows検証機で、空の専用出力先を指定します。既存の登録がある場合は試験を拒否します。

```powershell
python scripts/verify_windows_package.py --installer dist/windows-1.4.1/Ryoshu-1.4.1-windows-x64-setup.exe --source-zip Ryoshu-1.4.1-source.zip --qa-python C:\path\to\qa\python.exe --output-dir .local/windows-install-check
```

実行ごとに新しい出力先を使い、実行前から結果が存在するものとして扱わないでください。

インストール・全payloadのソースZIPとのバイト照合・3つのショートカット・Windows PowerShell起動・更新の起動元保護・別ポートのアプリ起動・アンインストールを検査します。新しい架空の暗号化帳簿と追加した利用者ファイルが削除・変更されないことも照合します。通常の帳簿は開かず、OCR・AIの新規推論も行いません。

結果の`verification.json`には絶対パスと起動トークンを含めません。失敗時にも、自分で作成した検証用アプリの削除と自分の検証サーバーの停止だけを行います。

別ポートの起動には`--qa-python`の準備済み検証用Pythonを使います。この検査はインストールされたコードの実行を確認するもので、スタートメニューからの通常起動や、Ryoshu専用Python・依存・llama.cpp実行環境・モデルを取得する初回準備の成否は別に確認します。`-CheckOnly`もOCRとモデル取得の完了を示しません。2件の架空入力を使った実際の初回準備・Indra OCR・ローカルモデル・CSV出力の受入検証は、`.github/workflows/hosted-windows-acceptance.yml`と`scripts/hosted_acceptance.py`で別途行い、v1.4.0の結果はReleaseの[`windows-installer-acceptance.json`](https://github.com/studio-yebisu/ryoshu/releases/download/v1.4.0/windows-installer-acceptance.json)で公開しています。受入環境は開発ツールと既存のシステムVC++実行環境を持つGitHub-hosted Windows Server 2025で、別途、既存のQAモデルがある開発者のWindows 11でローカル更新も確認しています。これは開発ツールやVC++実行環境が未導入の一般消費者向けWindows 11環境での検証や、架空100件の検証ではありません。過去の記録は[v1.3.4の実行範囲と検証待ち項目](../../docs/testing/v1.3.4/README.md)にあります。
