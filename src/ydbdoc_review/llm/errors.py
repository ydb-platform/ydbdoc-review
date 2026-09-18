"""TLS configuration errors; transport failures are defined in model.py."""

class LLMConfigError(ValueError):
    """Missing or invalid TLS configuration."""
