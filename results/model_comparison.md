# Model Comparison

| Model | Task | Passed | Score | Steps | Authored | Reused | Reuse Gain |
|---|---|:---:|---:|---:|:---:|:---:|---:|
| claude-sonnet-4.6 | stl_ep1_broken_validator | ✓ | 1.00 | 13 | ✓ | ✓ | — |
| claude-sonnet-4.6 | stl_ep2_batch_processing | ✓ | 1.00 | 8 | ✓ | ✓ | 38.5% |
| claude-sonnet-4.6 | stl_ep3_binary_variant | ✓ | 1.00 | 7 | ✓ | ✓ | 46.2% |
| claude-sonnet-4.6 | stl_ep4_unit_conversion | ✓ | 1.00 | 6 | ✗ | ✓ | 53.8% |
| claude-sonnet-4.6 | stl_ep5_large_batch | ✓ | 1.00 | 6 | ✓ | ✓ | 53.8% |
| claude-sonnet-4.6 | stl_ep6_repair | ✗ | 0.00 | 9 | ✓ | ✓ | 30.8% |
| **claude-sonnet-4.6 total** | **6 tasks** | **83%** | **0.83** | | | | |
| gpt-5.4 | stl_ep1_broken_validator | ✓ | 1.00 | 7 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep2_batch_processing | ✓ | 1.00 | 5 | ✓ | ✓ | 28.6% |
| gpt-5.4 | stl_ep3_binary_variant | ✓ | 1.00 | 8 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep4_unit_conversion | ✓ | 1.00 | 8 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep5_large_batch | ✓ | 1.00 | 5 | ✓ | ✓ | 28.6% |
| gpt-5.4 | stl_ep6_repair | ✓ | 1.00 | 8 | ✓ | ✓ | — |
| **gpt-5.4 total** | **6 tasks** | **100%** | **1.00** | | | | |
