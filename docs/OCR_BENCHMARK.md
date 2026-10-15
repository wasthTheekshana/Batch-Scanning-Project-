# OCR benchmark on the education loan form (synthetic)

8 random customers x 5 scan conditions, seed 1. The forms hold TYPED text: this measures the reader and the alignment, not handwriting, so it is an upper bound for pen-filled forms.

Exact-match rate (the value is read exactly right):

| Field | clean 300 dpi | 200 dpi | shrunk, shifted, crooked | black and white | noisy scan | All |
|---|---|---|---|---|---|---|
| account_number | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| branch | 100.0% | 100.0% | 100.0% |  50.0% |  75.0% |  85.0% |
| applicant_name | 100.0% | 100.0% | 100.0% | 100.0% |  87.5% |  97.5% |
| date_of_birth | 100.0% |  87.5% | 100.0% |   0.0% | 100.0% |  77.5% |
| mobile | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| email | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| nic_number | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| passport_number | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| address | 100.0% | 100.0% | 100.0% | 100.0% |  37.5% |  87.5% |
| **All fields** | 100.0% |  98.6% | 100.0% |  83.3% |  88.9% | ** 94.2%** |

Character accuracy (1 - edit distance / length):

| Field | clean 300 dpi | 200 dpi | shrunk, shifted, crooked | black and white | noisy scan | All |
|---|---|---|---|---|---|---|
| account_number | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| branch | 100.0% | 100.0% | 100.0% |  92.9% |  89.9% |  96.6% |
| applicant_name | 100.0% | 100.0% | 100.0% | 100.0% |  95.6% |  99.1% |
| date_of_birth | 100.0% |  98.4% | 100.0% |  73.4% | 100.0% |  94.4% |
| mobile | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| email | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| nic_number | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| passport_number | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| address | 100.0% | 100.0% | 100.0% | 100.0% |  79.5% |  95.9% |
| **All fields** | 100.0% |  99.8% | 100.0% |  96.3% |  96.1% | ** 98.4%** |

(took 369 s)
