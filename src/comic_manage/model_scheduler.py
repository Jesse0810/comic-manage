"""Rate limiting and estimated input budgets for model requests."""
from collections import OrderedDict, deque
import re
from threading import Lock
from time import monotonic, sleep

from .sources import check_cancel

_TOKEN_SEGMENTS = re.compile(r"[A-Za-z0-9_]+|[\x00-\x7f]|[^\x00-\x7f]")
_RESERVATIONS = OrderedDict()
_LOCK = Lock()


def estimated_input_tokens(system: str, prompt: str) -> int:
    """Estimate words, punctuation and multibyte text, plus message framing."""
    return 16 + sum((len(value) + 2) // 3 if value.isascii() else (len(value.encode("utf-8", "replace")) + 1) // 2
                    for value in _TOKEN_SEGMENTS.findall(system + "\n" + prompt))


def check_input_budget(profile: dict, system: str, prompt: str) -> None:
    limit = profile.get("options", {}).get("input_tokens")
    if limit and estimated_input_tokens(system, prompt) > limit:
        from .model_transport import ModelTransportError
        raise ModelTransportError("估算输入 Token 超出此模型的限制，请缩短任务或提高上限。", "MODEL_INPUT_LIMIT")


def wait_for_model_slot(profile: dict, cancelled=None) -> None:
    rpm = profile.get("options", {}).get("rpm", 0)
    check_cancel(cancelled)
    if not rpm:
        return
    key = (profile["provider"], profile["base_url"], profile["model"])
    while True:
        check_cancel(cancelled)
        with _LOCK:
            timestamp = monotonic()
            history = _RESERVATIONS.setdefault(key, deque())
            _RESERVATIONS.move_to_end(key)
            while history and timestamp - history[0] >= 60:
                history.popleft()
            if len(history) < rpm:
                history.append(timestamp)
                # Retire expired profiles without forgetting buckets still throttling.
                for old_key in list(_RESERVATIONS)[:-64]:
                    oldest = _RESERVATIONS[old_key]
                    if not oldest or timestamp - oldest[-1] >= 60:
                        del _RESERVATIONS[old_key]
                return
            remaining = min(0.25, 60 - (timestamp - history[0]))
        if cancelled is not None:
            cancelled.wait(remaining)
        else:
            sleep(remaining)
