"""Compatibility imports for the translation-owned acquisition policy.

Production translation imports :mod:`ydbdoc_review.translation.acquisition`
directly. No acquisition behavior is owned by the shared LLM package.
"""

from ydbdoc_review.translation.acquisition import (
    AcquisitionAttempt,
    AcquisitionBlockedError,
    AcquisitionController,
    AcquisitionExhaustedError,
    AcquisitionProtocolError,
    AcquisitionResult,
)

__all__ = [
    "AcquisitionAttempt",
    "AcquisitionBlockedError",
    "AcquisitionController",
    "AcquisitionExhaustedError",
    "AcquisitionProtocolError",
    "AcquisitionResult",
]
