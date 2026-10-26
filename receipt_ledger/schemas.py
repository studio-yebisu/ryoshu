import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = ""
    merchant: str = Field(default="", max_length=200)
    total: int | None = Field(default=None, ge=0, le=999_999_999, strict=True)
    description: str = Field(default="", max_length=1000)
    account: str = Field(default="", max_length=100)
    payment: Literal["", "owner", "cash", "bank", "card"] = ""
    business_percent: int | None = Field(default=None, ge=0, le=100, strict=True)
    purpose: str = Field(default="", max_length=1000)
    tax_rate: Literal["unknown", "10", "8", "mixed", "exempt"] = "unknown"
    tax_amount: int | None = Field(default=None, ge=0, le=999_999_999, strict=True)
    invoice_number: str = Field(default="", max_length=14)
    export_tax: Literal["", "outside", "non_taxable", "purchase10", "purchase8", "transition10", "transition8"] = ""

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        if value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("取引日はYYYY-MM-DDの形式で入力してください")
            date.fromisoformat(value)
        return value

    @field_validator("invoice_number")
    @classmethod
    def valid_invoice(cls, value):
        if value and not re.fullmatch(r"T\d{13}", value):
            raise ValueError("登録番号はTに続く13桁の数字で入力してください")
        return value


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    draft: Draft
    confirm: bool = False
    remember_account: bool = False


class VersionRequest(BaseModel):
    version: int = Field(ge=1)


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(default="",max_length=40)
    merchant: str = Field(default="",max_length=200)
    total: int | None = Field(default=None, ge=0, le=999_999_999)
    description: str = Field(default="",max_length=100)
    account: str = ""
    tax_rate: Literal["unknown", "10", "8", "mixed", "exempt"] = "unknown"
    tax_amount: int | None = Field(default=None, ge=0, le=999_999_999)
    invoice_number: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    reason: str = Field(default="",max_length=160)
    date_evidence: str = Field(default="",max_length=250)
    merchant_evidence: str = Field(default="",max_length=250)
    total_evidence: str = Field(default="",max_length=250)
    warnings: list[str] = Field(default_factory=list,max_length=5)


def confirmation_errors(draft: Draft) -> list[str]:
    errors = []
    for key, label in [("date", "取引日"), ("merchant", "支払先"), ("account", "勘定科目"), ("payment", "支払方法"), ("purpose", "業務での用途")]:
        if not getattr(draft, key).strip():
            errors.append(f"{label}を入力してください")
    if draft.total is None or draft.total <= 0:
        errors.append("合計金額は1円以上で入力してください。返金・取消は初版の対象外です")
    if draft.business_percent is None or draft.business_percent <= 0:
        errors.append("事業割合を1〜100%で入力してください。私用分のみなら対象外にできます")
    if draft.tax_amount is not None and draft.total is not None and draft.tax_amount > draft.total:
        errors.append("記載消費税が合計金額を超えています")
    if draft.date and date.fromisoformat(draft.date) > date.today():
        errors.append("未来の日付です。原本と照合してください")
    return errors
