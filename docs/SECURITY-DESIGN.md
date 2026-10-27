# Ryoshu セキュリティ設計

## 1. 保護範囲と前提

この文書は、Ryoshu v1.4.0候補に関する設計と固定された実装パラメータを説明する。候補版は公開前で、実装・セキュリティ・実インストーラーの検証は継続中である。公開済みv1.3.4の概要と操作の案内は [README](../README.md) を参照する。

領収書の読み取りと科目候補の生成はローカルPC上で行われ、外部AIのアカウントやAPIキーは必要ない。ただし、暗号化だけで端末上のあらゆる危険を排除できるわけではない。同じWindows利用者として動作するマルウェア、ロック解除済みのWindowsセッションや開いた画面へのアクセス、ローカル管理者およびSYSTEMからのアクセスは保護範囲外である。セキュリティの完全性や法令適合を保証するものでもない。

過去の脆弱性テストは [v1.1.0 candidate・2026-10-22 の監査記録](testing/security/README.md) に残る。これはv1.3.4やv1.4.0候補に対する監査ではない。以下の候補仕様と、過去のテスト対象範囲は区別して読む必要がある。

## 2. 保護対象と方式の全体像

| 対象 | 方式・扱い |
| --- | --- |
| 帳簿・OCR本文・科目候補・仕訳・監査履歴・設定 | SQLCipher 4。通常のSQLiteでは読めず、WALも暗号化します。 |
| 保存済み原本・元PDF・ページキャッシュ・プレビュー | AES-256-GCM。帳簿IDと相対パスを認証対象にし、別ファイルとのすり替えを拒否します。 |
| 鍵 | 帳簿ごとのランダムな鍵をWindows利用者単位のDPAPIで保護し、DB・ファイル・移行状態へ別の鍵を導出します。 |
| 復元情報 | 復元用パスワードから固定設定のScryptで鍵を導出し、帳簿の鍵をAES-GCMで包みます。 |
| バックアップ | `.ryoshubak`。ランダムな鍵でアーカイブ全体をAES-256-GCM暗号化し、小さな鍵情報を復元用パスワードで保護します。 |
| CSV・利用者が書き出した原本 | 暗号化されない通常のファイルです。保存先と共有先の管理が必要です。 |

実装上の暗号データベースには `sqlcipher3==0.6.4`、ファイル暗号には `cryptography==50.0.2` のAESGCMを使う。以降では、表ではなく実装パラメータ、認証に含める情報、平文が残る局面、各処理の上限を示す。

## 3. 鍵の生成・導出・保持

帳簿のマスターキーは `secrets.token_bytes` で生成する32バイトのランダム値で、帳簿IDはUUID4のhex表現である。AES鍵はいずれも32バイトなので、鍵長は256ビットである。

マスターキーからはHKDFで用途別の鍵を導出する。ハッシュはSHA-256、出力長は32バイト、saltは指定せず、infoは `Ryoshu <purpose> v1` の形式である。用途は `database`、`files`、`state` の3種類で、データベース、保存ファイル、移行状態の整合性をそれぞれ独立した鍵で扱う。

Windowsでは `.ryoshu-key.dpapi` にマスターキーを保存し、`CryptProtectData`／`CryptUnprotectData`、flags `1`、説明文 `Ryoshu local ledger` のDPAPIで現在のWindows利用者単位に保護する。ファイルのマジック値は `RYOSHU-KEY\x01`、上限は16,384バイトである。鍵ファイル操作には `.ryoshu-key.lock` を使い、ロック待ちの上限は15秒である。Windows以外の平文鍵へのフォールバックはなく、動作を拒否する。ローミングプロファイル、管理者によるパスワードリセット、資格情報バックアップ時のDPAPIの挙動までは検証していない。

移行状態は `.ryoshu-vault.json` に保存し、HKDFの `state` 鍵によるHMAC-SHA256で整合性を確認する。ファイル上限は16,384バイトで、段階は `creating`、`exporting`、`exported`、`active` である。

## 4. 復元用パスワードと鍵のラップ

復元用パスワードはPythonの文字列長で12文字以上256文字以下とする。文字列以外、空白だけの値、コードポイント32未満の制御文字、DEL（127）を拒否し、KDFへの入力にはUTF-8で符号化する。

パスワードからの鍵導出はScryptで、Nは131072（`2**17`）、rは8、pは1、出力鍵長は32バイトである。saltは16バイト、ラップ用AES-GCMのnonceは12バイトである。エンベロープはバージョン1で、`version`、`salt`、`nonce`、`ciphertext` を持ち、暗号文は16バイト以上4,096バイト以下に制限する。ラップ後には復号確認も行う。

このエンベロープでマスターキーを包み、`.ryoshu-recovery.json` に `ledger_id` と `envelope` を保存する。復元時のAADは `'Ryoshu recovery v1\0' || ledger_id` である。DPAPIの鍵が存在しない、または使えない場合、`recover()` はパスワードでマスターキーを取り出し、DPAPI鍵を保存し直す。パスワードはバックアップ作成前の `verify_password()` にも使い、バックアップではアーカイブ鍵とnonceを包む。パスワード変更には、ロック解除済みのWindows管理鍵が必要である。パスワードと生の鍵は帳簿、ブラウザーストレージ、ログに保存しない。

理論上のScryptメモリコストは `128 * r * N` で、1回の鍵導出につき134,217,728バイト（128 MiB）である。これはKDFの理論コストであり、アプリ全体の実測RAM使用量ではない。

パスワードを失い、かつDPAPI鍵も使えない場合に、実装内で帳簿を回復する経路はない。パスワードなしで復元用パスワードを再設定する経路もない。

## 5. データベースと保存ファイルの暗号パラメータ

暗号データベースのファイル名は `ledger.enc.sqlite3` である。HKDFの `database` 用途で導出した32バイトの鍵をraw hexのPRAGMA keyとして使い、SQLCipherのバージョンを必須条件として確認する。SQLiteの最低確認バージョンは3.53.4である。設定は `cipher_memory_security=ON`、`temp_store=MEMORY`、`trusted_schema=OFF`、`foreign_keys=ON`、`secure_delete=ON` である。`cipher_memory_security` を設定しても、OSのページング、スワップ、休止状態に残る復号データまで検証したわけではない。

保存ファイルはHKDFの `files` 用途で導出した32バイトの鍵によるAES-GCMで暗号化する。nonceは12バイト、認証タグは16バイト、レイアウトは `FILE_MAGIC || nonce || ciphertext+tag`、マジック値は `RYOSHU-FILE\x01` である。AADは正確に `FILE_MAGIC || ledger_id || 0x00 || ledgerルート以下のPOSIX形式相対パス` とする。シンボリックリンクやルート外のパスは拒否する。

対象は `receipts/<id>/original.*`、`imports/<sha>/source.pdf`、`preview-*.jpg` で、保存対象の平文上限は67,108,864バイト（64 MiB）である。これはアップロード入力の上限とは別である。

入力側では1ファイルを20,971,520バイト（20 MiB）、PDFを10ページ、一度に50ファイル、分割後を300ページ、リクエスト全体を67,108,864バイト（64 MiB）、画像を25,000,000ピクセルに制限する。受け付ける形式はJPEG、PNG、WEBP、PDFで、アニメーション画像は拒否する。

## 6. 一時的な平文と暗号化しない成果物

OCR中には `<data>/.ryoshu-work/ocr-*` の非公開ディレクトリへ、復号した原本コピー、画像の `normalized.png`、暗号化前の `preview-*.jpg` が一時的に存在する。作業領域のACLは `private_directory` で保護し、`.lease` のFileLockをtimeout 0で取得する。作業は1回のOCR処理に限定し、`finally` で削除する。削除は最大5回試行し、n回目の待機は `0.1*n` 秒である。起動時の `clean_work` はリースのない `ocr-*` を片付け、`backup-*` はリース確認なしで片付ける。

通常の書き込みは同じディレクトリ内の `mkstemp` で `.ryoshu-write-*` を作り、fsync後に `os.replace` で置き換える。`receipts` と `imports` に残った変換前の一時ファイルは変換時に削除する。SQLCipherの一時領域は `MEMORY` に設定する。

次のものはアプリによる暗号化の対象外である。

- CSV出力と、利用者が書き出した原本
- 帳簿IDと `format_version` だけを持ち、`user_version` が99のガード用 `ledger.sqlite3`
- `<DEFAULT_ROOT>/runtime` 以下のセットアップ・実行時ファイルと、`<DEFAULT_ROOT>` 直下の `venv`、`python`、`tools`、`cache`
- Indraキャッシュ、`<DEFAULT_ROOT>/llama` の実行環境、`<DEFAULT_ROOT>/models` のモデル
- 実行時のstdout／stderrログ

stdout／stderrログにどのような内容が記録されるかは監査していない。CSVと書き出した原本は通常の平文ファイルなので、保存先、共有先、バックアップ先は利用者が管理する。

## 7. 通信先とローカル制限

アプリは既定で `127.0.0.1` のTCPポート8766を使う。ポートは環境変数 `RECEIPT_LEDGER_PORT` で変更でき、通常の起動に使う `start.ps1` は8766を設定する。科目候補の作成では、Ryoshuが専用のllama.cpp `llama-server` を起動し、`127.0.0.1`だけで待ち受けさせる。接続には起動ごとに変わる認証キーを使い、Ryoshuの終了時には専用プロセスも終了する。OllamaやLM Studioへの接続は使わない。

アプリのサーバーをimportする前に `sys.addaudithook` を設定し、Pythonの `socket.connect`、`socket.sendto`、`socket.sendmsg`、`socket.getaddrinfo`、`socket.gethostbyname`、`socket.gethostbyaddr` の監査イベントを検査する。許可するホストは `127.0.0.1`、`::1`、`localhost` である。この仕組みはアプリプロセス内のPythonソケット監査イベントに対する制限であり、ネイティブ拡張や子プロセスの全通信を遮断するとは確認していない。セットアップのダウンロードはフックのない別プロセスで実行する。

Hugging Face向けには `HF_HOME=<DEFAULT_ROOT>/cache/huggingface`、`HF_HUB_DISABLE_TELEMETRY=1`、`HF_HUB_DISABLE_IMPLICIT_TOKEN=1` を設定し、領収書処理中は `HF_HUB_OFFLINE=1` を設定する。アプリがフラグを設定することは確認できるが、Indra OCRが全資産にこのフラグをどう適用するかまでは検証していない。

専用のllama-serverにはオフラインオプションを指定し、ループバックだけを使う。これはRyoshuが固定する起動設定であり、OSのファイアウォール規則による通信遮断ではない。Python側の監査フックをネイティブAIプロセスの強制遮断として扱わない。

初回セットアップではuv、Python、依存ライブラリ、OCR重み、llama.cpp実行環境、Qwenモデルを取得するためネット接続が必要で、再セットアップや更新でも取得処理が発生し得る。したがって、セットアップを含む運用全体が常にオフラインというわけではない。

## 8. ローカル画面の認証

セッション秘密は32バイトで、`.ryoshu-session.dpapi` にDPAPIで保護して保存する。起動時のbootstrapは `timestamp.nonce32hex.HMAC-SHA256` の形式で、有効期間は120秒、一度だけ使用できる。URLのクエリ文字列ではなくフラグメント `#open=` に載せる。

その後のbearer tokenは `token_urlsafe(32)` で生成し、サーバー側にはSHA-256ハッシュを保存する。トークンは操作に応じて期限が延びる12時間相当の43,200秒で、最大32個、使用済みbootstrapの記録は最大128個である。

## 9. Windowsのアクセス制御

帳簿側のACLは `D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;<current user SID>)` を使う。SYSTEM、BUILTIN\Administrators、現在の利用者にフルアクセスを与え、継承を保護した上でオブジェクトとコンテナへの継承を設定する。

ドライブルート、シンボリックリンク、ジャンクション、管理対象の子にあるハードリンクファイル（`nlink>1`）、Windows以外の環境は拒否する。AdministratorsとSYSTEMへのアクセス許可は設計上の権限でもあり、これらの主体を敵とする保護ではない点に注意する。

## 10. バックアップの構造・内容・上限

バックアップは拡張子 `.ryoshubak`、マジック値 `RYOSHU-BACKUP\x01` の独自形式である。ヘッダーは `MAGIC || u32be(len) || JSON{version:1,key:wrap_secret(key||nonce)}` で、JSON部分は1バイト以上4,096バイト以下、ヘッダー全体をAADとして認証する。

アーカイブ鍵は32バイト、nonceは12バイトのランダム値で、本体はAES-GCMで暗号化し、認証タグは16バイトである。パスワードで包む値は鍵とnonceをつないだ44バイトで、ScryptとAES-GCMを使い、AADは `MAGIC` である。アーカイブはtarのUSTAR_FORMAT、モード0o600で、平文のtarをディスクへ書かない。

内容は順に `manifest.json`、`data/ledger.enc.sqlite3`、`data/receipts/<id>/original<ext>`、`data/imports/<sha256>/source.pdf` である。バックアップ内のDBは、別に生成した32バイトの `backup_key` で暗号化したSQLCipherスナップショットで、その鍵は暗号化ストリーム内の `manifest.database_key` に入れる。manifestの項目は `app`、`format`、`version`、`created_at`、`receipts`、`database_key`、`missing_files` である。原本は追加前にSHA-256を確認し、missingを許可する場合は名前を `missing_files` に記録する。書き込み後は全体を復号して検証する。作業領域は `<data>/.ryoshu-backups/backup-*` である。

manifest、DB、原本など、アーカイブ内のエントリを展開したサイズの累計上限は21,474,836,480バイト（20 GiB、`20*1024**3`）、エントリ数は100,000件である。読み取りストリームの上限は `MAX_ARCHIVE + MAX_ENTRIES*1024 + 10240` バイト、原本1件は20,971,520バイト（20 MiB）、manifestは1,048,576バイト（1 MiB）、DBエントリは最低4,096バイトである。ブラウザーのフォールバックダウンロードには256 MiBの上限がある。

## 11. 復元時の検証と鍵の付け替え

復元先は存在しない新しい場所に限り、既存帳簿を上書きしない。最初にGCM全体を認証してから内容を解析し、採用前にもう一度認証する二段階の処理を行う。復元にはバックアップの復元用パスワードが必要で、同じパスワードが新しい復元先の復元用パスワードになる。

tarメンバーは `REGTYPE` と `AREGTYPE` だけを許可する。PAX／GNUの長い名前、sparse、重複名、予期しない名前、順序違い、末尾の非ゼロデータ、`user_version` が3より大きいDB、サイズやSHA-256の不一致を拒否する。空き容量は `member.size*2 + 128 MiB` を基準に確認する。

復元先では `LedgerKeys.setup(password)` により新しい鍵を作り、DBをバックアップ用 `backup_key` から新しいHKDF鍵へrekeyし、原本も再暗号化する。処理中のDB `.ryoshu-restore.sqlite3` は `backup_key` で暗号化された状態である。作業中は `RESTORE_IN_PROGRESS.txt`、失敗時は `RESTORE_FAILED.txt` を使う。起動時には、`<DEFAULT_ROOT>/restored` 以下でこれらのマーカーがある帳簿について、`data/` 内のリースのない `ocr-*` を片付け、`backup-*` 作業ディレクトリはリース確認なしで片付ける。復元先の帳簿フォルダ自体は削除しない。

## 12. 旧形式からの移行

旧い平文DB `ledger.sqlite3` からの移行では、`sqlcipher_export` で暗号化済みのpending DBを作り、フィンガープリント、`quick_check`、`foreign_key_check` を比較する。空き容量は `legacy_size*3 + 128 MiB` を確認し、旧DBの `user_version` は2以下を対象とする。

移行後のガードDBは `user_version` 99とし、`secure_delete` を有効にしてVACUUMする。`backups/before-encryption-<uuid>.sqlite3` は、ファイル名の接頭辞が `before-encryption` でも、内容は移行後の暗号化DBのコピーである。旧来の保存ファイルは `files_version` 1として一度だけ変換する。

## 13. セットアップ時の供給元と整合性

セットアップは、uv 0.13.5、Python 3.12.15、依存ライブラリ、Indra OCRの重み、llama.cpp実行環境、Qwenモデルを準備する。uvは `https://github.com/astral-sh/uv/releases/download/0.13.5/uv-x86_64-pc-windows-msvc.zip` から取得し、ZIPのSHA-256 `a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a`、EXEのSHA-256 `f45d1cb9d9140ca6d3f4005fdfc0ebeccf849fd04d1dc85804155cb923f1597b`、EXE長31,899,952バイトを確認する。Pythonは `uv python install --no-bin --no-registry`、依存関係は `uv pip install --require-hashes -r requirements.lock.txt` で導入する。

OCR重みは `scripts/prepare_ocr.py` で `<DEFAULT_ROOT>/cache/huggingface` に準備する。llama.cppは公式ggml-org releaseのb11700 Windows CPU x64 ZIP（19,593,035バイト、commit `23f7aabc16c72f6f707dba227251c35d7bd1ed96`）を使い、Qwenは `Qwen/Qwen3-4B-GGUF` のrevision `bc640142c66e1fdd12af0bd68f40445458f3869b` にある `Qwen3-4B-Q4_K_M.gguf`（2,497,280,256バイト）を使う。モデルの固定SHA-256は `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` である。

llama.cppのZIP・展開対象・モデルは、固定したサイズとSHA-256が一致するまで実行・導入に使わない。アーカイブの危険な項目と保存先のリンクを拒否し、中断したダウンロードは次のセットアップで再開する。起動時にも、専用AIプロセスを実行する前に実行環境とモデルの全SHA-256を1回確認する。不一致時は再セットアップで検証済みのファイルを準備し、以前の実行環境・帳簿・原本を保持する設計である。

ダウンロード前には `-AcceptModelLicense` または既存記録がない限り `y/N` のライセンス確認を行う。記録値は `Indra modified OpenRAIL-M`、ライセンスURLは `https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE` である。llama.cppのMITライセンスは実行環境の隣に、モデルのApache-2.0ライセンスはモデルの隣に配置する。

## 14. 関連する検証記録

現在の実装パラメータの確認と、過去の攻撃ケースに対する網羅性は同じ意味ではない。[v1.1.0 candidate・2026-10-22 のセキュリティテスト記録](testing/security/README.md) は過去の対象版と時点の記録として参照する。v1.3.4やv1.4.0候補の監査結果、新たな100ケースの推論テストを示すものではない。v1.4.0候補については、ネイティブ実行環境の全ハッシュ・起動元と認証・ループバック・実推論・セキュリティ回帰の検証が完了するまで、設計上の拒否条件を検証済みの防御結果として扱わない。
