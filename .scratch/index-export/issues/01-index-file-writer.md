# 01 Index file writer (CSV, XML, JSON)
Status: resolved

Build `export/index_export.py`: record from a document row, render in three formats,
CSV injection and XML escaping, running `index.csv` (thread-safe). Tests first.

## Answer
Built and tested. See tests/test_index_export.py, tests/test_index_export_pipeline.py, tests/test_designer_doc_types.py (index settings).
