"""Azure package init."""
__version__ = "1.0.0"


def load_model(path, device="cpu"):
    """Load a model from a path. Delegates to local_llm if available."""
    from .local_llm import LocalLLM
    llm = LocalLLM(model_path=path)
    return llm
