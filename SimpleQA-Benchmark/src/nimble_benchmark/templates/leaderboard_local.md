# Nimble Web API Leaderboard - $run_name

Generated: $generated_at
Synthesis model: `$synthesis_model`

$leaderboard_section## Sampler Configuration

Per-provider knobs read from `sampler_config_<name>.json` files in the run directory. Values shown as `(default)` mean the provider's own server picked the value.

$sampler_config_table

## Reproduce

```bash
$reproduce_command
$leaderboard_command
```

## Legal And Comms Checklist

- Confirm the run dataset, sample size, provider list, and grading method are appropriate for external claims.
- Review provider terms, attribution requirements, and any restrictions before sharing results.
- Have comms/legal approve public wording, caveats, and screenshots before publication.
- Publishing is human-gated; this generator never uploads, posts, or auto-publishes results.
- Treat `leaderboard.md` as a local draft artifact until a human explicitly approves distribution.
