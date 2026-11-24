"""Shared limits and decoding for one batch of uploaded workbooks."""
import base64
import io
import zipfile
from openpyxl import load_workbook

MAX_FILES = 10
MAX_ROWS = 10_000
MAX_TOTAL_ROWS = 20_000
MAX_BYTES = 8_000_000
MAX_EXPANDED = 40_000_000


class WorkbookBatch:
    def __init__(self, files):
        if not isinstance(files,list) or not 1 <= len(files) <= MAX_FILES:
            raise ValueError('一次请选择 1–10 个 Excel 文件')
        self.size = self.expanded = self.rows = 0

    def decode(self, file):
        if not isinstance(file,dict):
            raise ValueError('文件信息无效')
        name=file.get('name','导入文件.xlsx')
        if not isinstance(name,str) or not name.lower().endswith('.xlsx'):
            raise ValueError('仅支持 .xlsx 文件')
        raw=file.get('raw')
        if raw is None:
            try: raw=base64.b64decode(file.get('content',''),validate=True)
            except (ValueError,TypeError): raise ValueError('文件编码无效：'+name) from None
        if not isinstance(raw,bytes) or not raw:
            raise ValueError('文件内容为空或无效')
        self.size+=len(raw)
        if self.size>MAX_BYTES:raise ValueError('文件总大小超过 8MB')
        return name,raw

    def open(self,raw):
        if not isinstance(raw,bytes) or not raw or len(raw)>MAX_BYTES:
            raise ValueError('文件大小须在 1–8MB 内')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.expanded+=sum(entry.file_size for entry in archive.infolist())
        if self.expanded>MAX_EXPANDED:raise ValueError('Excel 解压后合计超过 40MB')
        return load_workbook(io.BytesIO(raw),read_only=True,data_only=False)

    def count_row(self,number):
        self.rows+=1
        if number>MAX_ROWS+1 or self.rows>MAX_TOTAL_ROWS:
            raise ValueError('单表最多 10000 行，每批最多 20000 行')


def header_values(rows):
    titles=[str(c.value).strip() if c.value is not None else '' for c in next(rows,())]
    while titles and not titles[-1]:titles.pop()
    return titles
