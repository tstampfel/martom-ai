"""martom: agentic RAG advisor for Željezarija Martom."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("martom")
except PackageNotFoundError:  # running from source without an installed package
    __version__ = "0.0.0+unknown"
