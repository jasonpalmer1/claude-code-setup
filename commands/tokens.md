---
description: Review the token ledger and report spend trends
---

Read `<MEMORY_DIR>/token_ledger.md` and report:

1. Total estimated spend and total sessions logged.
2. Average cost per session, and the 3 most expensive sessions.
3. Cache hit-rate trend (low hit rates = cache being busted — flag it).
4. Model mix — how much spend is going to each tier. Per the token-economy rule, a high top-tier share is a signal to delegate more.
5. **Delegation ratio** — (mid-tier + cheap-tier dollars) / total, per week and trending. IMPORTANT: judge the cheap tier by work displaced, not dollar share — it's roughly an order of magnitude cheaper than the top tier, so report its spend alongside its top-tier-equivalent cost (cheap-tier $ × ~15). A "tiny" $2 of cheap-tier work can represent ≈$30 of top-tier work avoided. Dollar share alone makes the cheap tier look unused even when it's doing its job.
6. One concrete tuning suggestion based on what the data shows (e.g. "the top tier is 80% of spend — route more file-reading to a cheaper model"). Health targets: top-tier share trending DOWN toward roughly 50-60%, the cheap tier appearing in most multi-file sessions.

If the ledger doesn't exist yet, say so — it populates as sessions end (SessionEnd hook) or when `token-ledger.py` is run manually on a transcript.

Keep it short and decision-oriented. This is the measurement feedback loop for the token-economy rule.
