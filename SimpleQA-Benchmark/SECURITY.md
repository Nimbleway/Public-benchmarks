# Security Policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub's private
vulnerability reporting: open the
[Security tab](https://github.com/Nimbleway/Public-benchmarks/security)
of this repository and choose **Report a vulnerability**. That opens a private
advisory visible only to you and the maintainers.

Do not open a public issue for a security report, and do not include live
credentials in one.

Please include what you can: affected file or lane, the version or commit, steps
to reproduce, and what an attacker could do with it. We aim to acknowledge a
report within a few business days.

## Scope

This project is an offline benchmark harness. It has no server, no user
accounts, and stores no data outside the local `runs/` directory. The security
surface worth reporting is roughly:

- **Credential handling** — anything that causes an API key from `.env` to be
  logged, written into a `runs/` artifact, or sent to a provider it does not
  belong to.
- **Untrusted input handling** — provider responses and dataset rows are
  untrusted text. Anything that turns them into code execution, path traversal,
  or a write outside the run directory.
- **Dependency vulnerabilities** with a plausible path to exploitation here.

## Out of scope

- Vulnerabilities in the third-party search or LLM APIs this harness measures.
  Report those to the vendor.
- Benchmark results you disagree with. Those are correctness or methodology
  issues — open a normal issue, and see
  [CONTRIBUTING.md](CONTRIBUTING.md) for the provenance rules.
- The bundled SimpleQA dataset, which is upstream content from
  [`openai/simple-evals`](https://github.com/openai/simple-evals).

## If you leaked a key

If you accidentally committed an API key to a fork or a PR branch, revoke it at
the provider first, then rewrite the history. Revocation is the fix; deleting
the commit is not.
