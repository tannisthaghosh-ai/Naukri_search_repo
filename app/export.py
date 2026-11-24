"""Generate Excel workbooks for Resdex search jobs."""
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlparse

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

XLSX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
CANDIDATE_HEADERS = (
    "Name",
    "Experience",
    "Current Company",
    "Skills",
    "Profile Link",
    "Page",
)
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="1F4E78")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_LINK_FONT = Font(color="0563C1", underline="single")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _set_text(cell, value: Any) -> None:
    cell.value = _text(value)
    if cell.data_type == "f":
        cell.data_type = "s"


def _skills_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return ", ".join(_text(skill) for skill in value if skill is not None)
    return _text(value)


def _profile_link(value: Any) -> str:
    raw_url = _text(value).strip()
    if not raw_url:
        return ""
    url = urljoin("https://resdex.naukri.com", raw_url)
    if urlparse(url).scheme.lower() not in {"http", "https"}:
        return ""
    return url


def _style_header(sheet) -> None:
    for cell in sheet[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center")


def build_candidate_workbook(job: Mapping[str, Any]) -> bytes:
    results = job.get("results", [])
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        results = []

    workbook = Workbook()
    candidates = workbook.active
    candidates.title = "Candidates"
    candidates.append(CANDIDATE_HEADERS)
    _style_header(candidates)

    for candidate in results:
        if not isinstance(candidate, Mapping):
            continue

        row = candidates.max_row + 1
        profile_url = _profile_link(candidate.get("profile_url"))
        name_cell = candidates.cell(row=row, column=1)
        _set_text(name_cell, candidate.get("name"))
        if profile_url:
            name_cell.hyperlink = profile_url
            name_cell.font = _LINK_FONT

        _set_text(candidates.cell(row=row, column=2), candidate.get("experience"))
        _set_text(candidates.cell(row=row, column=3), candidate.get("current_company"))
        skills_cell = candidates.cell(row=row, column=4)
        _set_text(skills_cell, _skills_text(candidate.get("skills")))
        skills_cell.alignment = Alignment(wrap_text=True, vertical="top")

        profile_cell = candidates.cell(row=row, column=5)
        _set_text(profile_cell, profile_url)
        if profile_url:
            profile_cell.hyperlink = profile_url
            profile_cell.font = _LINK_FONT

        page = candidate.get("page")
        if isinstance(page, int) and not isinstance(page, bool):
            candidates.cell(row=row, column=6, value=page)
        else:
            _set_text(candidates.cell(row=row, column=6), page)

    candidates.freeze_panes = "A2"
    candidates.auto_filter.ref = candidates.dimensions
    for column, width in {
        "A": 28,
        "B": 16,
        "C": 30,
        "D": 60,
        "E": 54,
        "F": 10,
    }.items():
        candidates.column_dimensions[column].width = width

    info = workbook.create_sheet("Info")
    info.append(("Field", "Value"))
    _style_header(info)
    for label, value in (
        ("Keyword", job.get("query")),
        ("Location", job.get("location")),
        ("Number of candidates", len(results)),
        (
            "Export date/time",
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        ),
    ):
        row = info.max_row + 1
        _set_text(info.cell(row=row, column=1), label)
        if label == "Number of candidates":
            info.cell(row=row, column=2, value=value)
        else:
            _set_text(info.cell(row=row, column=2), value)
    info.column_dimensions["A"].width = 24
    info.column_dimensions["B"].width = 48
    info.freeze_panes = "A2"

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
