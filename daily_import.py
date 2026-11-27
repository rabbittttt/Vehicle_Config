"""Recover uncached range totals in the legacy daily orders worksheet."""
import math
import re

from openpyxl.utils import column_index_from_string


_REF = r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})'
_SUM_RANGE = re.compile(rf'=\s*SUM\s*\(\s*{_REF}(?:\s*:\s*{_REF})?\s*\)', re.IGNORECASE)
_DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_FOOTERS = ('合计', '总计')


def _at(row, col):
    return row[col] if col < len(row) else None


def _sum_quantities(values):
    total = None
    try:
        for value in values:
            if value is None:
                continue
            if type(value) not in (float, int) or value < 0 or not math.isfinite(value):
                return NotImplemented
            total = value if total is None else total + value
        return total if total is None or math.isfinite(total) else NotImplemented
    except OverflowError:
        return NotImplemented


def daily_sum_value(cell, cells, caches, headers, block_rows, first_row, current_row):
    """Resolve an uncached daily SUM or return NotImplemented for normal validation.

    block_rows starts at Excel row first_row and retains intervening blank rows.
    current_row uses physical column positions; resolve footer date columns before
    the horizontal total. None denotes a range with no reported quantities.
    """
    column = cell.column - 1
    if cell.data_type != 'f' or caches[column].value is not None or not isinstance(cell.value,str):
        return NotImplemented
    match = _SUM_RANGE.fullmatch(cell.value.strip())
    if not match:
        return NotImplemented
    left,top,right,bottom = match.groups()
    if right is None:
        right,bottom = left,top
    left,right = sorted((column_index_from_string(left)-1, column_index_from_string(right)-1))
    top,bottom = sorted((int(top), int(bottom)))
    field = _at(headers,column)
    if top == bottom == cell.row and field == '合计':
        if left <= column <= right or right >= len(headers):
            return NotImplemented
        for j in range(left,right+1):
            header = headers[j]
            if header is None and cells[j].value is None:
                continue
            if not isinstance(header,str) or not _DAY.fullmatch(header):
                return NotImplemented
        return _sum_quantities(_at(current_row,j) for j in range(left,right+1))

    label = cells[0].value
    if (not isinstance(label,str) or label.strip() not in _FOOTERS or left != right or left != column
            or top < first_row or bottom >= cell.row or bottom-first_row >= len(block_rows)):
        return NotImplemented
    if field != '合计' and not (isinstance(field,str) and _DAY.fullmatch(field)):
        return NotImplemented
    values = []
    for i in range(top-first_row,bottom-first_row+1):
        row = block_rows[i]
        if not row:
            continue
        model = row[0]
        if model in _FOOTERS or model == '代际名' or (not model and any(v is not None for v in row)):
            return NotImplemented
        values.append(_at(row,column))
    return _sum_quantities(values)
