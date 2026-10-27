"""Reason code -> user-visible text for runtime/model problems.

Wording is the accepted runtime-reason manuscript (Kimi, parent-approved),
inserted verbatim. Unknown codes get the accepted generic text and never
echo the code, error detail or receipt content.
"""
from .runtime import REASON_CODES

SLOT_PREFIX = "[COPY_SLOT:"

MESSAGES = {
    "pin_missing": "アプリ本体のAI設定情報が見つかりません。アプリ本体を修復・再導入してください。「初回セットアップ.cmd」ではこの情報を復元できません。",
    "pin_invalid": "アプリ本体のAI設定情報が正しくありません。アプリ本体を修復・再導入してください。初回セットアップやモデルの再取得だけでは直せません。",
    "runtime_missing": "AIの実行プログラムが見つかりません。「初回セットアップ.cmd」を実行して準備し直してください。",
    "runtime_integrity": "AIの実行プログラムが確認済みの内容と一致しないため、使用を中止しました。「初回セットアップ.cmd」で準備し直してください。",
    "model_missing": "AIモデルが見つかりません。「初回セットアップ.cmd」で準備してください。読み取れた本文があれば、手入力やCSV出力を続けられます。",
    "model_unverified": "AIモデルの確認情報がないか古いため、まだ使用できません。「初回セットアップ.cmd」でモデルを確認し直してください。",
    "model_integrity": "AIモデルが確認済みの内容と一致しないため、使用を中止しました。「初回セットアップ.cmd」で準備し直してください。",
    "disabled": "AIによる科目候補の作成は現在利用できません。読み取れた本文があれば手入力し、帳簿の確認やCSV出力を続けてください。",
    "start_failed": "AIの実行プログラムを起動できませんでした。Ryoshuを再起動して再読み取りし、繰り返す場合は「初回セットアップ.cmd」でAIファイルを確認してください。",
    "exited": "AIの処理が途中で終了しました。読み取りを再試行し、繰り返す場合はRyoshuを再起動してください。読み取れた本文があれば、手入力やCSV出力を続けられます。",
    "load_timeout": "AIの準備が時間内に完了しませんでした。Ryoshuを再起動して再読み取りし、繰り返す場合は「初回セットアップ.cmd」でAIファイルを確認してください。",
    "request_timeout": "AIによる科目候補の作成が時間内に完了しませんでした。読み取りを再試行するか、読み取れた本文から手入力してください。CSV出力は引き続き行えます。",
    "cancelled": "AIによる科目候補の作成を中止しました。必要に応じて読み取りを再試行するか、読み取れた本文から手入力してください。",
    "auth_or_owner_mismatch": "Ryoshu専用のAIとの接続を確認できなかったため、処理を中止しました。Ryoshuを再起動して再読み取りしてください。読み取れた本文があれば、手入力やCSV出力を続けられます。",
    "server_error": "AIの実行プログラムがエラーを返しました。読み取りを再試行し、繰り返す場合はRyoshuを再起動してください。",
    "crash_loop": "AIが繰り返し停止したため、この起動中はAIの自動再試行を停止します。Ryoshuを再起動し、繰り返す場合は「初回セットアップ.cmd」でAIファイルを確認してください。",
    "length_limit": "AIの応答が出力上限に達したため、科目候補を完成できませんでした。読み取りを再試行するか、読み取れた本文から手入力してください。",
    "invalid_structured_output": "AIの応答を科目候補の形式に変換できませんでした。読み取りを再試行するか、読み取れた本文から手入力してください。",
}

UNKNOWN = "AIによる科目候補の作成で問題が起きました。読み取りを再試行し、直らない場合はRyoshuを再起動してください。読み取れた本文があれば、手入力やCSV出力を続けられます。"

assert set(MESSAGES) == set(REASON_CODES)


def message(code):
    return MESSAGES.get(code, UNKNOWN) if isinstance(code, str) else UNKNOWN


def is_placeholder(text):
    return isinstance(text, str) and text.startswith(SLOT_PREFIX)
