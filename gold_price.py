from mcp.server.fastmcp import FastMCP
import sys
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

# Thiết lập logging
logger = logging.getLogger('GoldPrice')

# Fix UTF-8 encoding cho Windows console
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

# Khởi tạo MCP Server với FastMCP
mcp = FastMCP("GoldPrice")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
}


def _parse_pnj_gold():
    """Lấy dữ liệu giá vàng từ PNJ (Bao gồm SJC 999.9, Nhẫn trơn PNJ, Vàng 24K, 18K, 14K...)"""
    url = "https://www.pnj.com.vn/blog/gia-vang/"
    response = requests.get(url, headers=HEADERS, timeout=12)
    response.raise_for_status()
    response.encoding = 'utf-8'

    soup = BeautifulSoup(response.content, 'html.parser')
    table = soup.find('table')
    if not table:
        return []

    items = []
    rows = table.find_all('tr')
    for row in rows[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
        if len(cols) >= 3:
            gold_name = cols[0]
            buy_raw = cols[1].replace(',', '').strip()
            sell_raw = cols[2].replace(',', '').strip()

            # Giá hiển thị đơn vị 1.000đ/chỉ
            # Ví dụ: 14,130 -> 14.130.000 VNĐ / chỉ = 141.300.000 VNĐ / lượng
            try:
                buy_val = float(buy_raw) * 1000 if buy_raw and buy_raw.replace('.', '').isdigit() else None
                sell_val = float(sell_raw) * 1000 if sell_raw and sell_raw.replace('.', '').isdigit() else None
            except ValueError:
                buy_val, sell_val = None, None

            items.append({
                "brand": "PNJ",
                "type": gold_name,
                "buy_per_chi": f"{buy_val:,.0f} đ/chỉ" if buy_val else cols[1],
                "sell_per_chi": f"{sell_val:,.0f} đ/chỉ" if sell_val else cols[2],
                "buy_per_luong": f"{buy_val * 10:,.0f} đ/lượng" if buy_val else "N/A",
                "sell_per_luong": f"{sell_val * 10:,.0f} đ/lượng" if sell_val else "N/A"
            })
    return items


def _parse_btmc_gold():
    """Lấy dữ liệu giá vàng từ Bảo Tín Minh Châu (VRTL, Vàng Rồng Thăng Long, SJC...)"""
    url = "https://btmc.vn/"
    response = requests.get(url, headers=HEADERS, timeout=12)
    response.raise_for_status()
    response.encoding = 'utf-8'

    soup = BeautifulSoup(response.content, 'html.parser')
    table = soup.find('table')
    if not table:
        return []

    items = []
    rows = table.find_all('tr')
    for row in rows[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
        valid_cols = [c for c in cols if c]
        if len(valid_cols) >= 3:
            name = " - ".join(valid_cols[:-2])
            buy_raw = valid_cols[-2].replace(',', '').strip()
            sell_raw = valid_cols[-1].replace(',', '').strip()

            try:
                buy_val = float(buy_raw) * 1000 if buy_raw and buy_raw.replace('.', '').isdigit() else None
                sell_val = float(sell_raw) * 1000 if sell_raw and sell_raw.replace('.', '').isdigit() else None
            except ValueError:
                buy_val, sell_val = None, None

            items.append({
                "brand": "Bảo Tín Minh Châu",
                "type": name,
                "buy_per_chi": f"{buy_val:,.0f} đ/chỉ" if buy_val else valid_cols[-2],
                "sell_per_chi": f"{sell_val:,.0f} đ/chỉ" if sell_val else valid_cols[-1],
                "buy_per_luong": f"{buy_val * 10:,.0f} đ/lượng" if buy_val else "N/A",
                "sell_per_luong": f"{sell_val * 10:,.0f} đ/lượng" if sell_val else "N/A"
            })
    return items


@mcp.tool()
def get_gold_price(brand: str = "all") -> dict:
    """
    Tra cứu bảng giá vàng mới nhất hôm nay tại Việt Nam.

    Các nguồn hỗ trợ:
    - all: Lấy tổng hợp từ tất cả các hệ thống (PNJ, SJC, Bảo Tín Minh Châu)
    - pnj: Lấy bảng giá hệ thống PNJ (Vàng miếng SJC 999.9, Nhẫn Trơn PNJ, 24K, 18K, 14K, 10K...)
    - btmc: Lấy bảng giá hệ thống Bảo Tín Minh Châu (Vàng Rồng Thăng Long, Nhẫn tròn trơn, SJC...)
    - sjc: Lấy riêng các loại vàng SJC từ PNJ và BTMC

    Đơn vị quy đổi:
    - 1 lượng (cây) = 10 chỉ = 37.5 gram
    - 1 chỉ = 3.75 gram
    """
    try:
        brand_lower = brand.lower().strip()
        all_items = []
        errors = []

        # Lấy từ PNJ (nếu brand là all, pnj, hoặc sjc)
        if brand_lower in ["all", "pnj", "sjc"]:
            try:
                pnj_items = _parse_pnj_gold()
                if brand_lower == "sjc":
                    pnj_items = [item for item in pnj_items if "sjc" in item["type"].lower()]
                all_items.extend(pnj_items)
            except Exception as e:
                logger.warning(f"Lỗi khi lấy giá vàng PNJ: {e}")
                errors.append(f"PNJ: {str(e)}")

        # Lấy từ BTMC (nếu brand là all, btmc, hoặc sjc)
        if brand_lower in ["all", "btmc", "sjc"]:
            try:
                btmc_items = _parse_btmc_gold()
                if brand_lower == "sjc":
                    btmc_items = [item for item in btmc_items if "sjc" in item["type"].lower()]
                all_items.extend(btmc_items)
            except Exception as e:
                logger.warning(f"Lỗi khi lấy giá vàng BTMC: {e}")
                errors.append(f"BTMC: {str(e)}")

        if not all_items and errors:
            return {
                "success": False,
                "error": f"Không thể lấy dữ liệu giá vàng: {'; '.join(errors)}"
            }

        return {
            "success": True,
            "query_brand": brand,
            "total_items": len(all_items),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": all_items,
            "note": "1 lượng (cây) = 10 chỉ. Giá được cập nhật từ website chính thức của các thương hiệu."
        }

    except Exception as e:
        logger.error(f"Lỗi tra cứu giá vàng: {e}")
        return {"success": False, "error": str(e)}


@mcp.tool()
def search_gold_price(keyword: str) -> dict:
    """
    Tìm kiếm thông tin giá vàng theo từ khóa cụ thể.

    Ví dụ từ khóa:
    - 'sjc': Tìm các loại vàng miếng SJC
    - 'nhẫn': Tìm vàng nhẫn tròn trơn các thương hiệu
    - '9999' hoặc '24k': Tìm vàng 24K (999.9)
    - '18k': Tìm vàng tây 18K
    - 'vàng cưới' hoặc 'nữ trang': Tìm vàng trang sức
    """
    try:
        # Lấy toàn bộ dữ liệu giá vàng
        gold_data = get_gold_price("all")
        if not gold_data.get("success"):
            return gold_data

        items = gold_data.get("data", [])
        kw_clean = keyword.lower().strip()

        matched = [
            item for item in items
            if kw_clean in item["type"].lower() or kw_clean in item["brand"].lower()
        ]

        return {
            "success": True,
            "keyword": keyword,
            "total_matched": len(matched),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": matched
        }

    except Exception as e:
        logger.error(f"Lỗi tìm kiếm giá vàng: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
