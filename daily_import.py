"""Recover uncached date-column footer sums in the legacy daily orders worksheet."""
import math
import re

from openpyxl.utils import column_index_from_string


_REF = r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})'
_SUM_RANGE = re.compile(rf'=\s*SUM\s*\(\s*{_REF}(?:\s*:\s*{_REF})?\s*\)', re.IGNORECASE)
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


def daily_footer_sum(cell, label, block_rows, first_row):
    """Sum a date column within this block, preserving physical row offsets."""
    column = cell.column - 1
    if not isinstance(label,str) or label.strip() not in _FOOTERS or not isinstance(cell.value,str):
        return NotImplemented
    match = _SUM_RANGE.fullmatch(cell.value.strip())
    if not match:
        return NotImplemented
    left,top,right,bottom = match.groups()
    if right is None:
        right,bottom = left,top
    left,right = sorted((column_index_from_string(left)-1, column_index_from_string(right)-1))
    top,bottom = sorted((int(top), int(bottom)))
    if left != right or left != column or top < first_row or bottom >= cell.row or bottom-first_row >= len(block_rows):
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
