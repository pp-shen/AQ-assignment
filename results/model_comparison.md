# Model Comparison

| Model | Task | Passed | Score | Steps | Authored | Reused | Reuse Gain |
|---|---|:---:|---:|---:|:---:|:---:|---:|
| claude-sonnet-4.6 | stl_ep1_broken_validator | ✓ | 1.00 | 20 | ✓ | ✓ | — |
| claude-sonnet-4.6 | stl_ep2_batch_processing | ✓ | 1.00 | 8 | ✓ | ✓ | 60.0% |
| claude-sonnet-4.6 | stl_ep3_binary_variant | ✗ | 0.00 | 3 | ✗ | ✓ | 85.0% |
| claude-sonnet-4.6 | stl_ep4_unit_conversion | ✓ | 1.00 | 10 | ✓ | ✓ | 50.0% |
| claude-sonnet-4.6 | stl_ep5_large_batch | ✓ | 1.00 | 7 | ✓ | ✓ | 65.0% |
| claude-sonnet-4.6 | stl_ep6_repair | ✗ | 0.00 | 8 | ✓ | ✓ | 60.0% |
| **claude-sonnet-4.6 total** | **6 tasks** | **67%** | **0.67** | | | | |
| gpt-5.4 | stl_ep1_broken_validator | ✗ | 0.00 | 8 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep2_batch_processing | ✓ | 1.00 | 5 | ✓ | ✓ | 37.5% |
| gpt-5.4 | stl_ep3_binary_variant | ✓ | 1.00 | 10 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep4_unit_conversion | ✓ | 1.00 | 8 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep5_large_batch | ✓ | 1.00 | 4 | ✓ | ✓ | 50.0% |
| gpt-5.4 | stl_ep6_repair | ✓ | 1.00 | 16 | ✓ | ✓ | — |
| **gpt-5.4 total** | **6 tasks** | **83%** | **0.83** | | | | |
