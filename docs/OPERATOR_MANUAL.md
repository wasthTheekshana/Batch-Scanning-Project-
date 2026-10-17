# DOK Scanner: operator manual

For the people who scan, check and index documents every day. Administrators: see
`docs/ADMIN_GUIDE.md`. Installing: `docs/INSTALL_GUIDE.md`.

## 1. Signing in

1. Start **DOK Scanner** and sign in with your user name and password.
2. The first time an account signs in with the starting password (`admin`), the program makes you
   choose a new one before anything else. A password needs at least 8 characters and cannot be
   `admin` or your user name.
3. **Five wrong passwords lock the account for 15 minutes.** Wait, or ask an administrator to unlock it
   (Admin > Users > *Unlock account*).
4. **If you stop using the program for 15 minutes it locks itself** and asks for your password again.
   Three wrong tries (or *Sign out*) end your session. Nothing you were doing is lost.
5. Sign out with the button at the bottom of the left menu when you leave the desk.

What you can see depends on your role:

| Role | Can do |
|---|---|
| Operator | scan, import, see own batches, custody bundles, search |
| QC officer | review and decide documents, see all batches, reports, custody, search |
| Viewer | see all batches, reports (and export them), search |
| Administrator | everything, including users, projects and backup |

## 2. The screen

The menu on the left has one entry per area: **Scan, QC Review, Index, Search, Job Monitor, Reports,
Projects, Admin, Custody**. You only see what your role allows. On a small screen the menu shows icons
only; hold the mouse over an icon to see its name. The window remembers its size and place.

## 3. Scanning a batch (Scan)

1. Choose **New scan batch**.
2. **Select project.** The project decides how pages are separated, read and delivered. For bank
   applications choose *Bank - Auto Indexing (Applications)*.
3. **Select input source.**
   - *File import* for PDF, TIFF, JPG, PNG files already on the computer.
   - *Scanner: ...* for a connected scanner. For the Kodak S3060 choose the entry ending in
     `(TWAIN)` (it is pre-selected): it scans **both sides** of every sheet quickly. The `(WIA)` entry is
     slower and loses the back of the last sheet; the program warns you if you choose it.
   - Set resolution and colour (grayscale is right for most forms) and tick *two-sided* if the sheets have
     writing on both sides.
4. **Confirm & start.** The batch goes through the pipeline automatically: barcodes are read, pages are
   straightened, checked and read, documents are separated and delivered.
5. Put the sheets in the feeder face down, and load a barcode or cover sheet between documents when
   the project separates by barcode.

A hot folder (a network scanner dropping files into a folder) works without you; see the administrator.

**Job Monitor** shows what each batch is doing right now. A batch that needs you shows up in QC Review
or Index.

## 4. Checking quality (QC Review)

Pages are scored automatically. Documents that scored low, or had a problem, wait here. Select one in
the list; the page as scanned is on the left and as corrected on the right.

| Button | Use it when |
|---|---|
| **Accept -> export** | the document is fine; it is exported and delivered |
| **Reject** | it is unusable and will not be delivered |
| **Flag for rescan** | it must be scanned again |
| **Edit page...** | a page needs rotating, cropping, whitening or whiting out |
| **Split / merge...** | the system joined two documents or cut one in two |

### Splitting and merging

If two applications ended up in one document: select it, choose **Split / merge...**, click the page
where the **second** document starts, and choose *Start a new document at the selected page*. If one
application was cut in two: select one half, choose the other in *Merge with*, and click *Merge*.

The two new documents appear in the QC list for you to accept. The old one is kept (as *rejected*,
with a note that it was replaced) so nothing is lost. Index values stay with the first document; the
second starts empty and shows up in the Index tab. If the original had already been delivered, the dialog
tells you: that delivery stays as it was, and the corrected document is delivered when you accept it.
Only documents of the same batch can be merged.

## 5. Indexing (Index)

The Index tab lists documents and the values read from them (account number, name, NIC, e-mail,
mobile, branch and so on). The page is in the middle, the fields on the right.

- A field with a **yellow** background was read with low confidence: compare it with the page and fix it.
- A **red** border means the value looks wrong (for example a NIC that is not 9 digits and a letter or 12
  digits). You can still save it after confirming.
- **Auto-extract from page** reads the fields again. **Save index fields** stores your corrections; a
  document that was already delivered has its index file delivered again.
- *Page types*: each page of a document can be labelled (NIC, Application, ...). Fix a wrong label and
  choose *Save page types*; the bookmarks in the PDF are updated.
- The list can show *Needs indexing*, *All documents* or *Fields to check (low confidence)*.

### Handwriting

Handwritten fields are read by the program, then corrected using what is known about the field (a mobile
number has 10 digits, a branch is one of the bank's branches). It is good, not perfect: **always check
names, e-mail addresses and the last digits of long numbers against the page.** Neat capitals inside the
printed boxes read best; writing over the box lines, or very small writing, reads worst.

## 6. Finding documents (Search)

Type a customer ID, NIC, name or account number (every word must match), or set the filters (project,
status, delivery, dates, a particular field). *Open PDF*, *Show in folder*, *History* and *Correct index
fields* act on the selected result. **Download results (CSV)** saves the whole result list with its index
fields for Excel. An operator only finds documents of batches they scanned.

## 7. Boxes and files in custody (Custody)

A bundle is a physical box or file with a QR label.

1. Create it (*New bundle label*; optionally how many documents it should hold), then **Print label sheet...**
   (choose how many copies).
2. Put a label sheet at the front of the stack when you scan it. The program notices the label, records
   the *scanning* checkpoint, and links the documents that follow to the bundle.
3. Record the other steps by hand: *prep, qc, storage, return* (**Record checkpoint**), or report a
   problem with **Flag incident** (damaged, missing, compromised).
4. The *Reconciliation* tab shows, per bundle, how many documents were received, scanned and
   delivered, and flags a mismatch with the expected count. *Documents* lists them, *Timeline* is the
   full history, and **Export timeline** saves it as CSV or PDF.

## 8. When something goes wrong

| What you see | What to do |
|---|---|
| *Account locked ...* | wait 15 minutes or ask an administrator to unlock it |
| *The scanner is in use by another program* | close the other scanning program; if the scanner stays locked, switch it off and on |
| *The document feeder is empty* | load the sheets and start again |
| Backs of sheets missing | you chose the (WIA) scanner entry; use the (TWAIN) one |
| A batch finished with *no documents* | every page was a label, separator or blank; the message says which |
| QC Review shows many low scores | check the scanner glass and feeder rollers; rescan the worst pages |
| The program closed unexpectedly | restart it; unfinished work resumes. Tell the administrator: `logs\crash.log` (in the data folder) records what happened, without document content |
| A document is in the wrong project | tell the administrator; do not re-scan it into another project |

Never delete files in the program's data folder by hand.
