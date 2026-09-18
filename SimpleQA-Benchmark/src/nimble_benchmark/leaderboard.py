"""Generate a publishable, human-gated leaderboard from analyzed run artifacts.

This module is a thin orchestrator: it stitches the data-prep layer
(:mod:`nimble_benchmark.leaderboard_data`) and the render layer
(:mod:`nimble_benchmark.leaderboard_render`) together with the templates in
``templates/``. Public API (``generate_leaderboard``,
``generate_public_docs_mdx``) is preserved for backwards compatibility.

Templating uses :class:`string.Template` (``$var``) rather than
:meth:`str.format` (``{var}``) so MDX/JSX brace syntax in the public template
(`{/* ... */}`, ``<Component prop={value} />``) is left alone.

The published output is named ``web-api-leaderboard.mdx``. Whatever publishes
it must keep the docs-site navigation in lockstep; an external link to an
older slug should redirect at the Mintlify layer.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from string import Template

from nimble_benchmark.leaderboard_data import (
    LeaderboardData,
    load_snapshot,
    prepare_leaderboard_data,
    save_snapshot,
)
from nimble_benchmark.leaderboard_render import (
    render_leaderboard_section,
    render_sampler_config_table,
)

# Re-export the public API so legacy imports keep working.
__all__ = [
    "PUBLIC_MDX_FILENAME",
    "generate_leaderboard",
    "generate_public_docs_mdx",
    "render_local_markdown",
    "render_public_mdx",
    "load_snapshot",
    "save_snapshot",
    "prepare_leaderboard_data",
]

_TEMPLATES_DIR = Path(__file__).with_name("templates")

# Published page filename. Whatever publishes it must update the docs-site
# navigation in lockstep; external links to an older slug should be
# redirected at the Mintlify layer.
PUBLIC_MDX_FILENAME = "web-api-leaderboard.mdx"


def generate_leaderboard(run_dir: Path, output_path: Path | None = None) -> Path:
    """Render a local leaderboard markdown file for an analyzed eval run."""
    data = prepare_leaderboard_data(Path(run_dir))
    destination = Path(output_path) if output_path else Path(data.run_dir) / "leaderboard.md"
    destination.write_text(render_local_markdown(data), encoding="utf-8")
    return destination


def generate_public_docs_mdx(run_dir: Path, output_path: Path) -> Path:
    """Render a Mintlify-ready MDX leaderboard for a public documentation site.

    Takes only the structured data from the run directory (split analyzed
    CSVs, run.json, sampler_config_*.json) and produces an MDX file suitable
    for direct overwrite into a docs site. No hand-curated narrative.
    """
    data = prepare_leaderboard_data(Path(run_dir))
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_public_mdx(data), encoding="utf-8")
    return destination


# --- pure renderers (used by the iteration env) ---------------------------


def render_local_markdown(data: LeaderboardData) -> str:
    """Render the local Markdown leaderboard from already-prepared data."""
    template = _read_template("leaderboard_local.md")
    return template.substitute(
        run_name=data.run_name,
        generated_at=data.generated_at,
        synthesis_model=data.synthesis_model,
        leaderboard_section=_with_trailing_blank(_render_leaderboard_section(data)),
        sampler_config_table=render_sampler_config_table(data.sampler_configs),
        reproduce_command=data.reproduce_command or data.leaderboard_command,
        leaderboard_command=data.leaderboard_command,
    )


def render_public_mdx(data: LeaderboardData) -> str:
    """Render the Mintlify MDX leaderboard from already-prepared data.

    The public template is intentionally minimal: frontmatter and the headline
    table -- nothing else. Source artifacts, sampler configuration, the
    reproduce command, the prose methodology block, and the run-provenance
    footer are deliberately omitted from the public surface; they're available
    in the local ``leaderboard.md`` and the raw run directory artifacts for
    anyone investigating a row. Keeping them out of the public page keeps
    it focused on the competitor comparison itself.
    """
    template = _read_template("leaderboard_public.mdx")
    # No run-provenance substitutions: the ``<Warning>`` footer that used to
    # surface ``run_name`` / ``total_rows`` / ``synthesis_model`` /
    # ``generated_at`` was removed from the template so the public page ends
    # at the data table. The same provenance still lives in
    # ``leaderboard.md`` (heading prelude) and ``run.json`` for anyone tracing
    # a published number back to its run.
    return template.substitute(leaderboard_section=_with_trailing_blank(_render_leaderboard_section(data)))


def _render_leaderboard_section(data: LeaderboardData) -> str:
    """The single table section shared by the local Markdown and public MDX.

    The heading stays "Search APIs" even though the native-LLM answer lanes
    render in it: the page is the web-search scoreboard, and those lanes are
    on it precisely to be compared against the search APIs.
    """
    return render_leaderboard_section(
        heading="Search APIs",
        rows=data.headline_rows,
        baseline_system=data.baseline_system,
        alpha=data.significance_alpha,
        significance_heading="Per-system significance",
        # Only used to price the depth-configurable ``nimble_search`` lane at
        # the depth it actually ran at; every other lane's tier is pinned.
        sampler_configs=data.sampler_configs,
    )


def _read_template(name: str) -> Template:
    return Template((_TEMPLATES_DIR / name).read_text(encoding="utf-8"))


def _with_trailing_blank(section: str) -> str:
    """The local template glues the section placeholder straight against the
    next heading, so a non-empty rendered section needs a trailing blank line
    to separate them. An empty section stays empty so the surrounding heading
    hierarchy stays flush against whatever precedes it.
    """
    if not section:
        return ""
    return section if section.endswith("\n\n") else section + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m nimble_benchmark.leaderboard")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Where to write the local Markdown leaderboard. Defaults to <run_dir>/leaderboard.md.",
    )
    parser.add_argument(
        "--public-docs-target",
        type=Path,
        default=None,
        help=(
            "When set, also write a Mintlify-flavored MDX leaderboard to this path. "
            "Use this to refresh a docs site's `web-api-leaderboard.mdx` page from a run."
        ),
    )
    args = parser.parse_args()
    print(generate_leaderboard(args.run_dir, args.output))
    if args.public_docs_target is not None:
        print(generate_public_docs_mdx(args.run_dir, args.public_docs_target))


if __name__ == "__main__":
    main()
