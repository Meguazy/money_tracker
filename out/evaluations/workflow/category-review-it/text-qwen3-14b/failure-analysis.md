# workflow/category-review-it — Qwen3 14B final failure analysis

Run `REV-20260909-150839-7a1a525f` on immutable dataset
`category-review-it-v1` 1.0.0 has no failed cases or blocking metrics. All 37
real-PostgreSQL scenarios pass; replay produces zero duplicate writes in 5/5
applicable cases. Warm scenario p95 is 267 ms. The scripted model port is part
of the declared configuration because every gated branch is deterministic.
