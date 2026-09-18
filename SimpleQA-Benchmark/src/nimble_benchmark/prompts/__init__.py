"""Vendored canonical prompts.

Each ``.txt`` file in this package is the verbatim text of a canonical
prompt from an upstream evaluation framework. Storing them as resources
(rather than Python string literals) keeps:

* The provenance clean — a contributor can ``diff prompts/foo.txt`` against
  the cited upstream file and see byte-exact equivalence.
* The source files lint-clean — long lines, non-ASCII characters, etc. in
  the upstream text don't trip ruff's line-length or unicode checks
  inside ``.py`` files.

Sources:

* ``simpleqa_grader.txt`` -- openai/simple-evals ``simpleqa_eval.py``
  (Apache-2.0). See
  https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py
* ``umbrela_passage.txt`` -- castorini/umbrela
  ``prompt_templates/qrel_zeroshot_bing.yaml``. See
  https://github.com/castorini/umbrela/blob/main/src/umbrela/prompts/prompt_templates/qrel_zeroshot_bing.yaml
* ``umbrela_url_only.txt`` -- Project extension. UMBRELA has no
  URL-only template; this reuses the canonical 0-3 grade definitions
  for anchor consistency.
"""

from __future__ import annotations

from importlib import resources


def load_prompt(name: str) -> str:
    """Load ``prompts/{name}.txt`` as a string. Trims a single trailing newline."""
    text = resources.files(__package__).joinpath(f"{name}.txt").read_text(encoding="utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    return text
