# Evaluation protocol (pre-register BEFORE opening the sealed set)

Status: draft. Fill in and commit before anyone looks at sealed labels.

- **Labels:** 80–120 pairs, two independent labellers, report Cohen's κ.
- **Split:** by event (`frame/fewshot.py: split_for`, SHA-256 of the event key, 30% sealed). Few-shot never reads sealed.
- **Systems:** (a) title-similarity baseline (`HashingEmbedder` cosine, threshold set on dev), (b) LLM judge (Gemini, model ID: ____).
- **Positive:** relation ∉ {unrelated, unclear} and confidence ≥ medium.
- **Metrics:** precision, recall, F1, with 95% bootstrap CIs (5,000 resamples, seed 0). Recall@k for candidate generation.
- **Thresholds fixed on dev:** baseline cosine = ____ ; top-k = 8 ; date window = 7 days.
- **Report honestly:** every wrong relation a human caught, and every quote that failed verification.
