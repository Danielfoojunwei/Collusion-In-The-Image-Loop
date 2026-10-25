"""Safe, typed technical failures; model outcomes never authorize retries."""
import re


def safe_class(error):
    name = type(error).__name__ if error is not None else "TechnicalFault"
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "UnclassifiedException"


def fault_record(category, error=None, *, recoverable=True, **details):
    if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", category) is None:
        raise ValueError("A fixed safe technical fault category is required")
    result = {"technical": True, "fault_class": category, "exception_class": safe_class(error),
        "recoverable": bool(recoverable), "provider_text_and_error_message_not_retained": True}
    for key in ("request_id", "request_hash", "wire_body_sha256", "endpoint", "record_path", "record_sha256", "phase"):
        value = details.get(key)
        if isinstance(value, str) and len(value) <= 1024 and not any(ord(c) < 32 for c in value):
            result[key] = value
    if type(details.get("http_status")) is int:
        result["http_status"] = details["http_status"]
    return result


class TechnicalRoundFault(Exception):
    recoverable = True
    def __init__(self, category, error=None, **details):
        self.fault = fault_record(category, error, recoverable=self.recoverable, **details)
        super().__init__("Technical experiment fault: " + category)


class PermanentTechnicalFault(TechnicalRoundFault):
    recoverable = False
