"""Extraction (M2-1): OLX listing text -> structured `product` and `product_variant` rows.

- `schema`: the Pydantic models Claude Haiku fills (structured output).
- `prompt`: the system prompt and how a listing is put in front of the model.
- `extractor`: the LangChain call, with retries, concurrency and LangSmith metadata.
- `prices`, `search_text`: plain-Python steps after the model (no LLM math).
- `store`, `job`: the database side and the `martom extract` run.
"""
