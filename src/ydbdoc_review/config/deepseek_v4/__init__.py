"""Pinned DeepSeek V4 Flash text tokenization (MIT upstream data)."""
import hashlib
from functools import lru_cache
from pathlib import Path

from ydbdoc_review.document import CapacityError

TOKENIZER_SHA256 = "8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf"


@lru_cache(maxsize=1)
def _tokenizer():
    # Pure tokenizer data only: no HF client, remote Python, or weight download.
    try:
        from tokenizers import Tokenizer
        data = Path(__file__).with_name("tokenizer.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != TOKENIZER_SHA256:
            raise ValueError("Tokenizer checksum mismatch")
        tokenizer = Tokenizer.from_str(data.decode("utf-8"))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return tokenizer
    except Exception:
        # Native tokenizer parse failures may use the base Exception class.
        raise CapacityError("Pinned DeepSeek V4 Flash tokenizer unavailable or corrupt") from None


def render_messages(messages, *, thinking):
    # Constrained specialization of upstream render_message/encode_messages.
    # This application sends one optional system message followed by one user.
    roles = [m.get("role") for m in messages]
    if roles not in (["user"], ["system", "user"]) or any(
        set(m) != {"role", "content"} or not isinstance(m["content"], str)
        for m in messages
    ):
        raise CapacityError("DeepSeek V4 tokenizer requires text system/user messages")
    system = messages[0]["content"] if len(messages) == 2 else ""
    return ("<\uff5cbegin▁of▁sentence\uff5c>" + system + "<\uff5cUser\uff5c>" + messages[-1]["content"]
            + "<\uff5cAssistant\uff5c>" + ("<think>" if thinking else "</think>"))


def count_messages(messages, reasoning_effort):
    if reasoning_effort not in (None, "none", "high"):
        raise CapacityError("No verified DeepSeek V4 encoding for reasoning_effort")
    modes = (False, True) if reasoning_effort is None else (reasoning_effort != "none",)
    tokenizer = _tokenizer()
    return max(len(tokenizer.encode(render_messages(messages, thinking=mode),
                                    add_special_tokens=False).ids) for mode in modes)


def count_output(text):
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)
