# tests/test_document_rules.py — the pure rules of the document library: file kinds, names, safe search terms, the folder
# tree, and reading text out of files.
import io
import zipfile

import pytest

from app import document_rules as r


# ─── file kinds ────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,kind", [
    ("SOP.docx", "document"), ("old.DOC", "document"), ("plan.pdf", "pdf"), ("budget.xlsx", "spreadsheet"), ("data.csv", "spreadsheet"),
    ("deck.pptx", "presentation"), ("notes.txt", "text"), ("photo.JPG", "image"), ("scan.heic", "image"), ("logo.svg", "image"),
    ("voice.m4a", "audio"), ("clip.mp4", "video"), ("all.zip", "archive"), ("mail.eml", "email"), ("plant.dwg", "drawing"),
    ("noext", "file"), (".hidden", "file"), ("archive.unknown", "file"),
])
def test_file_kind_by_extension(name, kind):
    assert r.file_type_of(name) == kind


def test_everything_uploadable_has_a_kind_and_nothing_dangerous_does():
    for ext in r.HUB_UPLOAD_EXTS:
        assert r.file_type_of(f"x.{ext}") != "file"
    for ext in ("exe", "bat", "sh", "js", "html", "htm", "ps1", "msi", "apk", "php", "vbs", "jar", "dll"):
        assert ext not in r.HUB_UPLOAD_EXTS


def test_the_served_type_comes_from_the_extension_not_the_browser():
    assert r.content_type_for("notes.txt") == "text/plain"
    assert r.content_type_for("a.docx").endswith("wordprocessingml.document")
    assert r.content_type_for("p.pdf") == "application/pdf"
    assert r.content_type_for("mystery.zzz") == "application/octet-stream"
    assert "html" not in r.content_type_for("page.txt")


def test_hash_is_stable():
    assert r.content_hash(b"abc") == r.content_hash(b"abc") != r.content_hash(b"abd")


# ─── names ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_folder_names_are_tidied_and_checked():
    assert r.clean_folder_name("  Site   photos ") == "Site photos"
    assert r.clean_folder_name("SOPs / SWPs") == "SOPs / SWPs"
    for bad in ("", "   ", "..", "a\\b", "x" * 121, "bell\x07", None):
        with pytest.raises(ValueError):
            r.clean_folder_name(bad)


def test_display_names_fall_back_in_order():
    assert r.clean_display_name("  My  file.pdf ", "orig.pdf") == "My file.pdf"
    assert r.clean_display_name("", "orig.pdf") == "orig.pdf"
    assert r.clean_display_name("", "") == "Untitled"


def test_a_taken_name_gets_a_number_before_the_extension():
    assert r.unique_display_name("Report.pdf", []) == "Report.pdf"
    assert r.unique_display_name("Report.pdf", ["report.PDF"]) == "Report (2).pdf"
    assert r.unique_display_name("Report.pdf", ["Report.pdf", "Report (2).pdf"]) == "Report (3).pdf"
    assert r.unique_display_name("Notes", ["notes"]) == "Notes (2)"
    assert r.unique_display_name("v1.2 final", ["v1.2 final"]) == "v1.2 final (2)"


def test_tags_are_saved_the_same_way_however_typed():
    assert r.normalise_tags([" Safety ", "safety", "Pump  House", "", ",x,"]) == ["safety", "pump house", "x"]
    assert r.normalise_tags(None) == []
    with pytest.raises(ValueError):
        r.normalise_tags(["a" * 41])
    with pytest.raises(ValueError):
        r.normalise_tags([f"t{i}" for i in range(21)])


# ─── search ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_search_terms_split_on_spaces_and_keep_quoted_phrases():
    assert r.search_terms("  Pump   Maintenance ") == ["pump", "maintenance"]
    assert r.search_terms('"hot work" permit') == ["hot work", "permit"]
    assert r.search_terms("a a A") == ["a"]
    assert r.search_terms("") == [] and r.search_terms(None) == []
    assert len(r.search_terms(" ".join(f"w{i}" for i in range(20)))) == r.MAX_TERMS
    assert len(r.search_terms("x" * 500)[0]) == r.MAX_TERM_LENGTH


def test_a_search_cannot_break_out_of_its_filter():
    value = r.ilike_value('x),name.eq.secret,(y')
    # one quoted value: the comma and brackets sit inside the quotes, so they cannot end the filter or add one
    assert value.startswith('"%') and value.endswith('%"')
    assert value.count('"') == 2


def test_quotes_and_backslashes_are_escaped_for_the_filter():
    assert r.ilike_value('say "hi"') == '"%say \\"hi\\"%"'
    assert r.ilike_value("a\\b") == '"%a\\\\\\\\b%"'  # backslash: LIKE-escaped, then escaped again for the quoting


def test_percent_and_underscore_mean_themselves():
    assert r.ilike_value("100%") == '"%100\\\\%%"'
    assert r.ilike_value("a_b") == '"%a\\\\_b%"'


def test_csv_and_type_filters_ignore_junk():
    assert r.csv_values(" a, b ,,c") == ["a", "b", "c"]
    assert r.valid_types("pdf,bogus,image") == ["pdf", "image"]


def test_sorting():
    assert r.sort_spec("name", None) == ("name", False)
    assert r.sort_spec("size", None) == ("file_size", True)
    assert r.sort_spec("name", "desc") == ("name", True)
    assert r.sort_spec("nonsense", "up") == ("created_at", True)


# ─── the folder tree ───────────────────────────────────────────────────────────────────────────────────────────────

TREE = [
    {"id": "a", "parent_id": None, "name": "A"},
    {"id": "b", "parent_id": "a", "name": "B"},
    {"id": "c", "parent_id": "b", "name": "C"},
    {"id": "d", "parent_id": "a", "name": "D"},
    {"id": "e", "parent_id": None, "name": "E"},
]


def test_descendants_include_the_folder_and_all_below():
    assert sorted(r.descendant_ids(TREE, "a")) == ["a", "b", "c", "d"]
    assert r.descendant_ids(TREE, "c") == ["c"]


def test_a_loop_in_the_data_does_not_hang():
    loop = [{"id": "x", "parent_id": "y", "name": "X"}, {"id": "y", "parent_id": "x", "name": "Y"}]
    assert sorted(r.descendant_ids(loop, "x")) == ["x", "y"]
    assert len(r.ancestry(loop, "x")) <= r.MAX_FOLDER_DEPTH * 4 + 1
    assert r.subtree_height(loop, "x") >= 1


def test_path_and_ancestry():
    assert [f["id"] for f in r.ancestry(TREE, "c")] == ["a", "b", "c"]
    assert r.folder_path(TREE, "c") == "A > B > C"
    assert r.folder_path(TREE, None) == "" and r.folder_path(TREE, "nope") == ""


def test_a_folder_cannot_move_into_itself_or_below():
    with pytest.raises(ValueError, match="into itself"):
        r.check_placement(TREE, "a", "c")
    with pytest.raises(ValueError, match="into itself"):
        r.check_placement(TREE, "a", "a")
    r.check_placement(TREE, "c", "e")  # fine
    r.check_placement(TREE, "a", None)  # to the top level is always fine


def test_depth_is_limited():
    deep = [{"id": str(i), "parent_id": str(i - 1) if i else None, "name": f"L{i}"} for i in range(r.MAX_FOLDER_DEPTH)]
    with pytest.raises(ValueError, match="levels deep"):
        r.check_placement(deep, None, str(r.MAX_FOLDER_DEPTH - 1))
    r.check_placement(deep, None, str(r.MAX_FOLDER_DEPTH - 2))
    # moving a whole branch counts the branch's own height
    with pytest.raises(ValueError, match="levels deep"):
        r.check_placement(TREE + [{"id": "z", "parent_id": None, "name": "Z"}], "a", "z") if False else r.check_placement(
            deep + [{"id": "m", "parent_id": None, "name": "M"}, {"id": "n", "parent_id": "m", "name": "N"}], "m", str(r.MAX_FOLDER_DEPTH - 1))


def test_names_are_unique_within_a_parent_ignoring_case():
    assert r.name_taken(TREE, "a", "b") is True
    assert r.name_taken(TREE, "a", "B", ignore_id="b") is False
    assert r.name_taken(TREE, "e", "B") is False
    assert r.name_taken(TREE, None, "a") is True


# ─── reading text ──────────────────────────────────────────────────────────────────────────────────────────────────

def _docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:p><w:r><w:t>second &amp; last</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def test_text_is_read_from_word_files():
    text = r.extract_text(_docx("Hot work permit"), "docx")
    assert "Hot work permit" in text and "second & last" in text and "<" not in text


def test_text_is_read_from_plain_files_and_capped():
    assert r.extract_text(b"line one\n\nline   two", "txt") == "line one line two"
    assert len(r.extract_text(b"word " * 100000, "txt")) == r.MAX_TEXT_CHARS
    assert r.extract_text(b"a b c", "txt", limit=3) == "a b"


def test_text_is_read_from_spreadsheets():
    import openpyxl
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Pumps"
    sheet.append(["Tag", "Duty"])
    sheet.append(["P-101", "Dewatering"])
    buf = io.BytesIO()
    book.save(buf)
    text = r.extract_text(buf.getvalue(), "xlsx")
    assert "Pumps" in text and "P-101" in text and "Dewatering" in text


def test_text_is_read_from_pdfs():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Ventilation shaft inspection")
    text = r.extract_text(doc.tobytes(), "pdf")
    assert "Ventilation shaft inspection" in text


def test_text_is_read_from_slides():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/slides/slide2.xml", "<a:p><a:t>Second slide</a:t></a:p>")
        z.writestr("ppt/slides/slide1.xml", "<a:p><a:t>First slide</a:t></a:p>")
    assert r.extract_text(buf.getvalue(), "pptx") == "First slide Second slide"


@pytest.mark.parametrize("ext", ["pdf", "docx", "xlsx", "pptx"])
def test_a_damaged_file_gives_no_text_and_no_error(ext):
    assert r.extract_text(b"this is not a real file", ext) == ""


def test_kinds_without_a_reader_give_no_text():
    assert r.extract_text(b"\xff\xd8\xff", "jpg") == "" and r.extract_text(b"x", "doc") == ""
