# Setting up index fields for a project

How auto-indexing gets its fields, and how to configure it for a new project.
The example is the `logistic` project (Abans Logistics tax invoices).

## How it works

Each project has a list of **index zones**. A zone is one field (for example
`invoice_number`) plus the rule that finds its value on the page. When a batch
is scanned, the pipeline reads each page with OCR and fills those fields. The
Index tab shows them, and an operator can correct and save them.

Two extraction modes, chosen per project:

| Mode | How a field is found | Use when |
|---|---|---|
| **Full-page (label-anchored regex)** | The whole page is OCR'd and each field's regex is matched against the text. | The position of a field varies between documents (invoices from different customers). |
| **Zone-based (draw regions)** | You draw a rectangle per field on a sample page; only that region is OCR'd. | Fixed forms where every field is always in the same place. |

## Steps for a new project

1. **Create the project.** Projects tab (Workflow Designer) → **New from
   template…**, or **Clone…** an existing project. Pick it in the Project
   drop-down.
2. **Choose the extraction mode** in the *Index zones (auto-index fields)*
   section (**Extraction mode** drop-down).
3. **Add one zone per field.** Click **Add zone…**:
   - **Field name** — for example `invoice_number`. It becomes the label on
     the Index tab.
   - **Clean-up regex** — required in full-page mode. The part inside
     `( )` is the value that is saved.
   - **Load sample page…** — pick a real scan of this project.
   - **Test zone (run OCR)** — shows what the field would extract from that
     page.
4. Repeat for every field, then **Save** the project.
5. New scans of the project fill the fields automatically. For documents
   scanned earlier, open the Index tab, select the document, click
   **Auto-extract from page**, then **Save index fields**. Anything the
   patterns miss can be typed by hand.

Each document is indexed with the zones of **its own project** (the Index
tab switches to the selected document's project).

## Writing the regexes

Write them against the real OCR text, not against how the page looks. OCR
often reads a page in a different order from the one you see (a column of
labels, then the column of values).

1. Scan one real document and read its OCR text (the Test zone result shows
   it).
2. Anchor the pattern on something stable: a label
   (`TOTAL LKR\s+([\d,]+\.\d{2})`) or a code format
   (`(ABA[A-Z]{2}\d{8,})`).
3. Test it on at least three or four different documents of that type, not
   just one. A label-then-value pattern that works on one page can fail on
   the next.

Patterns used for the `logistic` project:

| Field | Pattern | Matches |
|---|---|---|
| invoice_number | `(ABA[A-Z]{2}\d{8,})` | the invoice code format |
| vendor_name | `(ABANS LOGISTICS \(PVT\)(?: LTD)?)` | the issuer name in the header |
| invoice_date | `(?:INVOICE NO.{0,80}?DATE\s*[:=.]*\s*\|:\s*)(\d{1,2} [A-Za-z]{3} \d{4})` | the date next to the invoice number, else the first `: dd Mon yyyy` |
| amount | `TOTAL (?:LKR\|USD)\s+([\d,]+\.\d{2})` | the number after the grand-total label |
| po_reference | `REF #\s*:?\s*([A-Z0-9]+(?: \d{3,})?)` | the code after "REF #" |

(The `\|` above is a literal `|` inside a table cell; type a plain `|` in
the Designer.)

## Splitting a batch into documents

Indexing is per document, so the separation settings matter. If several
single-page invoices are scanned in one batch with no separator sheets, they
are grouped into one document. For single-page documents set the project's
separation to **Fixed pages per document = 1**, which cuts every page into
its own document. Other options: blank-page separator sheets, barcode
separator sheets, or a zone trigger.

## Limits

- Regexes need a technical person to write them; an operator cannot.
- Each vendor or layout usually needs its own project or patterns. A pattern
  written for one company's invoices will not fit another's.
- OCR noise can still corrupt a value (for example a month read as "dan"
  instead of "Jan"). The operator corrects these in the Index tab.
- "Suggest regex" in the zone dialog only works in zone-based mode.
