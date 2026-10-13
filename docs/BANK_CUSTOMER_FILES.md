# Customer files: one PDF per customer, indexed by document type

For a bank that scans a customer's NIC, application form and other
documents together: one PDF per customer, split by a barcode, with each kind
of document indexed with its own fields.

## How it works

1. **A barcode marks the start of each customer.** It is either a *cover
   sheet* on top of the customer's papers or a *barcode label stuck on the
   first page* (left, right, anywhere on the page). When scanned, the barcode
   starts a new PDF and its value is stored as the document's `barcode`
   field. The PDF is named after it (for example `CIF-100234_0001.pdf`).
2. **Each page is recognised by its text.** The project lists document types
   (NIC, Application, Letter...). A page belongs to the first type whose
   pattern matches its text. A page that matches nothing (the back of an ID
   card, page 2 of a form) belongs to the type of the page before it.
3. **Each type has its own index fields.** Fields are read only from pages of
   their own type, then merged into the customer's index record
   (`nic_number`, `application_number`, `applicant_name`, ...).
4. **The PDF has bookmarks** for each stretch of one type (NIC, Application,
   Letter), so a reviewer can jump to the right pages.

## Setting it up

1. **Workflow Designer -> New from template -> "Bank - Customer file"**
   (built in; ships with NIC, Application and Letter types). Or use the
   project already created in your app.
2. **Separation.** Under *Separation strategy*, tick **barcode** and choose
   what a page with a barcode is:
   - *the first page of a document*: a barcode label stuck on a document page;
   - *a separate cover sheet*: a sheet that is only a separator (it is not
     scored by QC and not included in the PDF);
   - *automatic* (the template default): a cover sheet when the page has
     hardly any text besides the barcode, otherwise the first page.
   Optionally set **Only barcodes matching** (for example `^CIF-\d+$`) so other
   barcodes printed on documents (cheque numbers, product codes) never split
   a customer's file.
3. **Document types.** In *Document types*, **Add document type...** gives a
   name, the pattern that recognises its pages (capitals do not matter; use
   `|` for alternatives) and its fields. Field names must be unique across the
   project's types. Fields in the normal *Index zones* box apply to every page.
4. **Print the barcodes.** On the **Scan** tab click **Print cover sheets**.
   Enter a prefix, start number and count (or paste a list of IDs), choose
   cover sheets or labels (30 stickers per A4 sheet), and create the PDF.
   Barcodes are read in any orientation, so a page fed upside down or sideways
   still works.

## Writing patterns for your own documents

Use the real OCR text, not how the page looks (OCR can read columns in a
different order). Anchor on a label (`Application No:`) or a format (a
12-digit NIC number). Test each pattern on at least three real documents. See
`docs/INDEXING_SETUP.md` for the method.

## Limits

- A mixed pile with no barcode between customers cannot be split by customer.
- Fields come from the OCR text, so poor scans give poor values; the operator
  corrects them in the **Index** tab (which shows every type's fields).
- Type detection is by page text. A page with no recognisable text (a photo
  of a card) is typed as the page before it.
- Patterns in the "Bank - Customer file" template are generic starting points
  and must be tuned on the bank's real forms.
