"""
VietnamServices MCP Server - Dịch vụ tổng hợp Giá vàng và Tin tức cho Trợ lý ảo Xiaozhi
Thiết kế tối ưu cho mô hình ngôn ngữ (LLM) và giọng đọc tiếng Việt (TTS).
"""

from mcp.server.fastmcp import FastMCP
import sys
import logging
import requests
from bs4 import BeautifulSoup
import feedparser
from datetime import datetime
import re

logger = logging.getLogger("VietnamServices")

# Đảm bảo UTF-8
if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

mcp = FastMCP("VietnamServices")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


@mcp.tool()
def tra_cuu_gia_vang() -> str:
    """
    Gọi công cụ này khi người dùng hỏi về giá vàng SJC, vàng nhẫn 9999, vàng 24K hoặc tỷ giá hôm nay tại Việt Nam.
    Trả về câu văn tự nhiên, ngắn gọn phù hợp cho loa phát âm thanh (TTS).
    """
    results = []
    time_str = datetime.now().strftime("%H:%M ngày %d/%m/%Y")

    # 1. Thử lấy giá từ PNJ (Có SJC, Nhẫn trơn, Vàng nữ trang)
    try:
        url_pnj = "https://www.pnj.com.vn/blog/gia-vang/"
        resp = requests.get(url_pnj, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")[1:]
                for row in rows:
                    cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if len(cols) >= 3:
                        name, buy_raw, sell_raw = cols[0], cols[1].replace(",", ""), cols[2].replace(",", "")
                        if "sjc" in name.lower() or "nhẫn" in name.lower():
                            try:
                                buy_val = f"{float(buy_raw) * 1000:,.0f} đồng/chỉ"
                                sell_val = f"{float(sell_raw) * 1000:,.0f} đồng/chỉ"
                                results.append(f"- {name}: Mua vào {buy_val}, bán ra {sell_val}.")
                            except ValueError:
                                pass
    except Exception as e:
        logger.warning(f"Lỗi lấy giá vàng PNJ: {e}")

    # 2. Thử lấy giá từ Bảo Tín Minh Châu (API hoặc Web)
    try:
        url_btmc = "http://api.btmc.vn/api/BTMCAPI/getpricebtmc?key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v"
        resp = requests.get(url_btmc, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("DataList", {}).get("Data", [])
            for item in items:
                name = item.get("@n_1") or ""
                buy = item.get("@pb_1") or ""
                sell = item.get("@ps_1") or ""
                if "VÀNG RỒNG THĂNG LONG" in name.upper() or "SJC" in name.upper():
                    try:
                        buy_fmt = f"{float(buy):,.0f} đồng/lượng" if buy else "Chưa có"
                        sell_fmt = f"{float(sell):,.0f} đồng/lượng" if sell else "Chưa có"
                        results.append(f"- BTMC {name}: Mua vào {buy_fmt}, bán ra {sell_fmt}.")
                        if len(results) >= 4:
                            break
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning(f"Lỗi lấy giá vàng BTMC: {e}")

    if not results:
        return "Hiện tại hệ thống cập nhật giá vàng đang bận, bạn vui lòng hỏi lại sau ít phút nhé."

    # Tạo câu trả lời gọn gàng, súc tích để AI đọc to thành tiếng
    header = f"Giá vàng cập nhật lúc {time_str}:\n"
    content = "\n".join(results[:4])
    footer = "\nLưu ý: 1 lượng vàng bằng 10 chỉ."
    return header + content + footer


@mcp.tool()
def doc_tin_tuc_viet_nam(chu_de: str = "thoi-su") -> str:
    """
    Lấy 5 tin tức mới nhất từ báo VnExpress.
    Tham số chu_de nhận một trong các giá trị:
    - thoi-su: Tin thời sự nổi bật
    - the-gioi: Tin thế giới
    - kinh-doanh: Tin kinh tế, tài chính
    - the-thao: Tin thể thao, bóng đá
    - cong-nghe: Tin công nghệ, số hóa
    """
    rss_map = {
        "thoi-su": "https://vnexpress.net/rss/tin-moi-nhat.rss",
        "the-gioi": "https://vnexpress.net/rss/the-gioi.rss",
        "kinh-doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
        "the-thao": "https://vnexpress.net/rss/the-thao.rss",
        "cong-nghe": "https://vnexpress.net/rss/so-hoa.rss"
    }

    url = rss_map.get(chu_de.lower().strip(), rss_map["thoi-su"])
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return "Không lấy được dữ liệu tin tức lúc này, bạn vui lòng thử lại sau."

        articles = []
        for i, entry in enumerate(feed.entries[:5], 1):
            title = entry.title.strip()
            articles.append(f"Tin {i}: {title}.")

        return f"Điểm tin VnExpress chuyên mục {chu_de} mới nhất:\n" + "\n".join(articles)
    except Exception as e:
        logger.error(f"Lỗi đọc tin tức: {e}")
        return f"Không thể tải tin tức lúc này: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
