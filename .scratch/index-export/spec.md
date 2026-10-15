# Index export: send the index fields with every document

Status: in progress

## Problem
The pipeline fills index fields (customer id/barcode, NIC, account, dates...) but delivery
sends only the PDF. The fields live only in the database. A bank needs them next to each
PDF, and corrections made later in the Index tab must reach them too.

## Behaviour
- Project setting `export.index` = `{"enabled": bool, "format": "csv"|"xml"|"json", "running_csv": bool}`
  (default off, so existing projects are unchanged).
- When enabled, an index file is written next to every exported PDF (same base name, `.csv`
  / `.xml` / `.json`) and delivered through every channel the PDF goes to.
- Record: document id, project, batch, barcode, page count, document types, QC score, scan
  time, operator, PDF file name, PDF SHA-256, then every index field.
- `running_csv`: also append one row per document to `index.csv` in the output folder.
- When an operator saves corrected fields in the Index tab for an already exported document,
  the index file is rewritten and delivered again (audited).
- Safe values: CSV formula injection neutralised, XML escaped, JSON strict; no path leaves
  the project's output folder.
- Settings in the Workflow Designer's export group.

## Out of scope
Mapping to a particular document-management schema (needs the bank's field list).

## Tickets
`issues/01-..` to `issues/04-..`
