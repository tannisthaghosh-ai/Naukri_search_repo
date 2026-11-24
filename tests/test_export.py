from io import BytesIO

from openpyxl import load_workbook

from app.export import CANDIDATE_HEADERS, build_candidate_workbook


def test_candidate_workbook_headers_rows_hyperlinks_and_info():
    first_url = "https://resdex.naukri.com/v3/preview?uniqId=candidate-1"
    second_url = "https://resdex.naukri.com/v3/preview?uniqId=candidate-2"
    workbook_bytes = build_candidate_workbook(
        {
            "query": "python developer",
            "location": "Kolkata",
            "results": [
                {
                    "name": "First Candidate",
                    "profile_url": first_url,
                    "experience": "4y 8m",
                    "current_company": "Example Corp",
                    "skills": ["Python", "FastAPI"],
                    "page": 1,
                    "raw_text": "must not be exported",
                    "uniq_id": "must not be exported",
                },
                {
                    "name": "=2+2",
                    "profile_url": second_url,
                    "experience": None,
                    "current_company": None,
                    "skills": None,
                    "page": 2,
                },
            ],
        }
    )
    workbook = load_workbook(BytesIO(workbook_bytes), data_only=False)
    sheet = workbook["Candidates"]

    assert tuple(cell.value for cell in sheet[1]) == CANDIDATE_HEADERS
    assert sheet.max_row == 3
    assert sheet["A2"].value == "First Candidate"
    assert sheet["A2"].hyperlink.target == first_url
    assert sheet["E2"].value == first_url
    assert sheet["E2"].hyperlink.target == first_url
    assert sheet["A2"].font.underline == "single"
    assert sheet["D2"].value == "Python, FastAPI"
    assert sheet["F2"].value == 1
    assert sheet["B3"].value is None
    assert sheet["C3"].value is None
    assert sheet["D3"].value is None
    assert sheet["A3"].value == "=2+2"
    assert sheet["A3"].data_type == "s"
    assert sheet["A1"].font.bold is True
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:F3"
    assert sheet["D2"].alignment.wrap_text is True

    info = workbook["Info"]
    info_values = {info.cell(row=row, column=1).value: info.cell(row=row, column=2).value
                   for row in range(2, info.max_row + 1)}
    assert info_values["Keyword"] == "python developer"
    assert info_values["Location"] == "Kolkata"
    assert info_values["Number of candidates"] == 2
    assert info_values["Export date/time"]
    assert all(
        "raw_text" not in str(cell.value) and "uniq_id" not in str(cell.value)
        for row in sheet.iter_rows()
        for cell in row
    )
    assert all(
        cell.value != "None"
        for row in workbook.worksheets
        for cells in row.iter_rows()
        for cell in cells
    )
    assert all(
        cell.data_type != "f"
        for row in workbook.worksheets
        for cells in row.iter_rows()
        for cell in cells
    )
