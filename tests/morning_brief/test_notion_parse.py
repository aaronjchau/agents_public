"""Tests for the Notion property extractors."""

from datetime import date

from services.morning_brief import notion_parse


def test_title_text_found_by_type() -> None:
    row = {
        "properties": {
            "Whatever Name": {
                "type": "title",
                "title": [{"plain_text": "Two "}, {"plain_text": "Sum"}],
            },
            "Other": {"type": "rich_text", "rich_text": [{"plain_text": "ignore"}]},
        }
    }
    assert notion_parse.title_text(row) == "Two Sum"


def test_title_text_falls_back_to_text_content() -> None:
    row = {"properties": {"N": {"type": "title", "title": [{"text": {"content": "Hi"}}]}}}
    assert notion_parse.title_text(row) == "Hi"


def test_title_text_missing_returns_empty() -> None:
    assert notion_parse.title_text({"properties": {}}) == ""
    assert notion_parse.title_text({}) == ""


def test_number_ignores_bool_and_missing() -> None:
    props = {"N": {"type": "number", "number": 140}, "B": {"type": "checkbox", "checkbox": True}}
    assert notion_parse.number(props, "N") == 140
    assert notion_parse.number(props, "B") is None
    assert notion_parse.number(props, "Absent") is None


def test_status_and_select_names() -> None:
    props = {
        "Status": {"type": "status", "status": {"name": "Scheduled"}},
        "Type": {"type": "select", "select": {"name": "Morning"}},
        "Empty": {"type": "select", "select": None},
    }
    assert notion_parse.status_name(props, "Status") == "Scheduled"
    assert notion_parse.select_name(props, "Type") == "Morning"
    assert notion_parse.select_name(props, "Empty") is None
    assert notion_parse.status_name(props, "Absent") is None


def test_multi_select_and_first_multi_select() -> None:
    props = {
        "Category": {"type": "multi_select", "multi_select": [{"name": "SWE"}, {"name": "DSA"}]}
    }
    assert notion_parse.multi_select(props, "Category") == ("SWE", "DSA")
    row = {"properties": {"Topics": {"type": "multi_select", "multi_select": [{"name": "Array"}]}}}
    assert notion_parse.first_multi_select(row) == ("Array",)
    assert notion_parse.first_multi_select({"properties": {}}) == ()


def test_relation_ids() -> None:
    props = {"Project": {"type": "relation", "relation": [{"id": "p1"}, {"id": "p2"}, {}]}}
    assert notion_parse.relation_ids(props, "Project") == ("p1", "p2")
    assert notion_parse.relation_ids({}, "Project") == ()


def test_date_start_and_range() -> None:
    props = {
        "Time Block": {"type": "date", "date": {"start": "2026-06-04", "end": "2026-06-08"}},
        "Due": {"type": "date", "date": {"start": "2026-06-04T09:00:00.000-04:00"}},
        "Empty": {"type": "date", "date": None},
    }
    assert notion_parse.date_range(props, "Time Block") == (date(2026, 6, 4), date(2026, 6, 8))
    # Datetime strings are truncated to the date part.
    assert notion_parse.date_start(props, "Due") == date(2026, 6, 4)
    assert notion_parse.date_range(props, "Empty") == (None, None)
    assert notion_parse.date_start(props, "Absent") is None


def test_parse_iso_date_handles_garbage() -> None:
    assert notion_parse.parse_iso_date("2026-06-04") == date(2026, 6, 4)
    assert notion_parse.parse_iso_date("not-a-date") is None
    assert notion_parse.parse_iso_date(None) is None
    assert notion_parse.parse_iso_date("") is None
