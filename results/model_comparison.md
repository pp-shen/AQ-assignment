# Model Comparison

| Model | Task | Passed | Score | Steps | Authored | Reused | Reuse Gain |
|---|---|:---:|---:|---:|:---:|:---:|---:|
| claude-sonnet-4.6 | stl_ep1_broken_validator | ✓ | 1.00 | 16 | ✓ | ✓ | — |
| claude-sonnet-4.6 | stl_ep2_batch_processing | ✓ | 1.00 | 10 | ✓ | ✓ | 37.5% |
| claude-sonnet-4.6 | stl_ep3_binary_variant | ✓ | 1.00 | 9 | ✓ | ✓ | 43.8% |
| **claude-sonnet-4.6 total** | **3 tasks** | **100%** | **1.00** | | | | |
| gpt-5.4 | stl_ep1_broken_validator | ✓ | 1.00 | 7 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep2_batch_processing | ✗ | 0.55 | 4 | ✓ | ✓ | 42.9% |
| gpt-5.4 | stl_ep3_binary_variant | ✓ | 1.00 | 9 | ✓ | ✓ | — |
| **gpt-5.4 total** | **3 tasks** | **67%** | **0.85** | | | | |
