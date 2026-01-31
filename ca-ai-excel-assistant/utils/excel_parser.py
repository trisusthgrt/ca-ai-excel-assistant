"""
Excel parsing with pandas (Step 4).
Supports .xlsx; reads first sheet or combines all sheets into one DataFrame.
"""
from typing import Union

import pandas as pd


def parse_excel(file_path_or_buffer: Union[str, bytes, "pd.io.ExcelFile"]) -> pd.DataFrame:
    """
    Parse Excel file and return a single DataFrame.
    - file_path_or_buffer: path string, file-like (e.g. BytesIO), or bytes.
    - Uses first sheet only by default; if multiple sheets, concatenates them (same columns assumed).
    - Handles missing columns gracefully (NaN).
    """
    if file_path_or_buffer is None:
        return pd.DataFrame()

    try:
        # Read all sheets; if only one, use it; else concat
        xl = pd.ExcelFile(file_path_or_buffer)
        sheets = xl.sheet_names
        if not sheets:
            return pd.DataFrame()

        if len(sheets) == 1:
            return pd.read_excel(xl, sheet_name=sheets[0])
        # Multiple sheets: concat with same columns
        dfs = [pd.read_excel(xl, sheet_name=s) for s in sheets]
        return pd.concat(dfs, ignore_index=True)
    except Exception:
        return pd.DataFrame()
