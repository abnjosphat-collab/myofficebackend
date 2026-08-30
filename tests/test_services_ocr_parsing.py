# tests/test_services_ocr_parsing.py — _parse_service_text() extracts structured
# fields (date, supplier, invoice/order/requisition numbers, amount, etc.) from raw OCR
# text off a scanned invoice/service document, auto-filling the services form. Pure
# regex logic, zero tests, real risk of a wrong auto-fill on a document layout the
# patterns don't expect. Test fixtures are deliberately built to match the ACTUAL
# regex alternatives precisely (e.g. "Purchase Order Number:" for order_number, not a
# bare "PO Number:" — the pattern's "p.?o.?" fallback branch only fires when neither
# "purchase" nor "order" appear first, and doesn't consume a literal "Number" suffix
# the way the "order" branch's (?:no|number|#|nr)? group does; verified by tracing the
# pattern before writing the fixture, not assumed).

from app.routers.services import _parse_service_text


def test_extracts_date_ddmmyyyy_dayfirst():
    result = _parse_service_text("Date: 15/03/2024\nOther text here")
    assert result["date"] == "2024-03-15"


def test_extracts_date_iso_format():
    result = _parse_service_text("Service Date: 2024-03-15")
    assert result["date"] == "2024-03-15"


def test_date_falls_back_to_raw_text_when_unparseable():
    # 32/13/2024 is not a valid day/month under any interpretation - dateutil raises,
    # and the except branch must fall back to the raw matched text, not crash.
    result = _parse_service_text("Date: 32/13/2024")
    assert result["date"] == "32/13/2024"


def test_no_date_in_text_is_none():
    assert _parse_service_text("No dates anywhere in this document")["date"] is None


def test_extracts_description():
    result = _parse_service_text("Description: Replaced hydraulic pump seals")
    assert result["description"] == "Replaced hydraulic pump seals"


def test_extracts_supplier():
    result = _parse_service_text("Supplier: ACME Pumps Ltd")
    assert result["supplier"] == "ACME Pumps Ltd"


def test_extracts_contact_person_via_attn_abbreviation():
    result = _parse_service_text("Attn: John Doe")
    assert result["contact_person"] == "John Doe"


def test_extracts_requisition_number():
    result = _parse_service_text("Requisition Number: REQ-12345")
    assert result["requisition_number"] == "REQ-12345"


def test_extracts_invoice_number():
    result = _parse_service_text("Invoice Number: INV-9988")
    assert result["invoice_number"] == "INV-9988"


def test_extracts_purchase_order_number():
    result = _parse_service_text("Purchase Order Number: PO-999")
    assert result["order_number"] == "PO-999"


def test_extracts_grv_number():
    result = _parse_service_text("GRV Number: GRV-001")
    assert result["grv_number"] == "GRV-001"


def test_extracts_payment_reference():
    result = _parse_service_text("Payment Reference: PAY-777")
    assert result["payment_reference"] == "PAY-777"


def test_extracts_currency_prefixed_amount():
    result = _parse_service_text("Amount: R 1,250.00")
    assert result["amount"] == "R 1,250.00"


def test_extracts_category():
    result = _parse_service_text("Category: Plumbing")
    assert result["category"] == "Plumbing"


def test_field_matching_is_case_insensitive():
    result = _parse_service_text("SUPPLIER: Acme Corp")
    assert result["supplier"] == "Acme Corp"


def test_missing_fields_are_none_not_a_crash():
    result = _parse_service_text("Just some unrelated document text.")
    assert result["supplier"] is None
    assert result["invoice_number"] is None
    assert result["amount"] is None


def test_document_header_word_does_not_leak_into_the_next_field():
    # Real bug found while writing these tests: a document title/header containing a
    # field's label word on its own line (e.g. "SERVICE INVOICE" as a title) matched
    # invoice_number's pattern, then greedily consumed the newline as its separator and
    # captured the FOLLOWING LINE's unrelated leading token ("Date:") as the invoice
    # number. Fixed by restricting the label/value separator to same-line whitespace
    # only (was `\s` which matches newline, now `[ \t:]`) — see _parse_service_text's
    # own docstring for the full trace. This is the direct regression test for that.
    text = "SERVICE INVOICE\nDate: 10/01/2024\nInvoice Number: INV-4521\n"
    result = _parse_service_text(text)
    assert result["invoice_number"] == "INV-4521"
    assert result["date"] == "2024-01-10"


def test_extracts_multiple_fields_from_one_realistic_document():
    text = (
        "SERVICE INVOICE\n"
        "Date: 10/01/2024\n"
        "Supplier: Bearing Solutions Pty Ltd\n"
        "Description: Replaced conveyor belt bearings\n"
        "Invoice Number: INV-4521\n"
        "Amount: R 8,450.00\n"
    )
    result = _parse_service_text(text)
    assert result["date"] == "2024-01-10"
    assert result["supplier"] == "Bearing Solutions Pty Ltd"
    assert result["description"] == "Replaced conveyor belt bearings"
    assert result["invoice_number"] == "INV-4521"
    assert result["amount"] == "R 8,450.00"
