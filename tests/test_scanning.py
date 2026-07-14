import cv2
import pytest
from PIL import Image

from scanning.base import ScannerUnavailable
from scanning.file_import import import_files
from scanning.folder_watcher import FolderWatchSource
from scanning.twain_source import TWAINSource
from scanning.wia_source import WIASource
from tests.conftest import make_text_page


# ---- file import -----------------------------------------------------------

def test_import_single_images(tmp_path):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.jpg"
    cv2.imwrite(str(p1), make_text_page())
    cv2.imwrite(str(p2), make_text_page())
    jobs = import_files([p1, p2], "b1", "proj", tmp_path / "staging")
    assert [j.page_no for j in jobs] == [1, 2]
    assert all(j.source == "file" for j in jobs)
    assert all(j.image_path.endswith(".png") for j in jobs)


def test_import_explodes_multipage_tiff(tmp_path):
    frames = [Image.fromarray(make_text_page()) for _ in range(3)]
    tiff = tmp_path / "multi.tif"
    frames[0].save(tiff, save_all=True, append_images=frames[1:])
    jobs = import_files([tiff], "b1", "proj", tmp_path / "staging")
    assert len(jobs) == 3
    assert [j.page_no for j in jobs] == [1, 2, 3]


def test_import_explodes_pdf(tmp_path):
    pytest.importorskip("fitz", reason="PyMuPDF not installed")
    import img2pdf
    pages = []
    for i in range(2):
        p = tmp_path / f"src{i}.png"
        cv2.imwrite(str(p), make_text_page())
        pages.append(str(p))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(img2pdf.convert(pages))
    jobs = import_files([pdf], "b1", "proj", tmp_path / "staging")
    assert len(jobs) == 2
    assert all(j.image_path.endswith(".png") for j in jobs)
    img = cv2.imread(jobs[0].image_path)
    assert img is not None and img.shape[0] > 100


def test_import_skips_unsupported(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    assert import_files([bad], "b1", "proj", tmp_path / "staging") == []


# ---- hot folder --------------------------------------------------------------

def test_folder_watcher_detects_stable_file(tmp_path):
    seen = []
    watcher = FolderWatchSource(tmp_path / "hot", seen.append,
                                poll_seconds=0.05)
    watcher.watch_dir.mkdir(parents=True)
    pre_existing = watcher.watch_dir / "old.png"
    pre_existing.write_bytes(b"x" * 100)
    watcher._known = {p: p.stat().st_size for p in watcher._iter_files()}

    new_file = watcher.watch_dir / "scan.png"
    new_file.write_bytes(b"y" * 50)
    assert watcher.poll_once() == []          # first sighting → pending
    assert watcher.poll_once() == [new_file]  # stable → dispatched
    assert seen == [new_file]
    assert watcher.poll_once() == []          # not re-dispatched


def test_folder_watcher_waits_for_growing_file(tmp_path):
    seen = []
    watcher = FolderWatchSource(tmp_path / "hot", seen.append,
                                poll_seconds=0.05)
    watcher.watch_dir.mkdir(parents=True)
    f = watcher.watch_dir / "big.tif"
    f.write_bytes(b"a" * 10)
    watcher.poll_once()               # pending @10
    f.write_bytes(b"a" * 999)          # still copying
    assert watcher.poll_once() == []   # size changed → still pending
    assert watcher.poll_once() == [f]  # stable now
    assert seen == [f]


def test_folder_watcher_ignores_unsupported_extension(tmp_path):
    watcher = FolderWatchSource(tmp_path / "hot", lambda p: None,
                                poll_seconds=0.05)
    watcher.watch_dir.mkdir(parents=True)
    (watcher.watch_dir / "readme.txt").write_text("x")
    assert watcher.poll_once() == []
    assert watcher.poll_once() == []


# ---- hardware sources degrade gracefully ---------------------------------------

@pytest.mark.parametrize("source_cls", [TWAINSource, WIASource])
def test_sources_degrade_without_hardware(source_cls):
    src = source_cls()
    devices = src.list_devices()
    assert isinstance(devices, list)  # never raises, [] without drivers
    if not devices:
        with pytest.raises(ScannerUnavailable):
            src.connect("Nonexistent Scanner 9000")
    with pytest.raises(ScannerUnavailable):
        source_cls().acquire(300, "grayscale", False)  # not connected
    src.disconnect()  # never raises
