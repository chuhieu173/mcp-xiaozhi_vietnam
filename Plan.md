# Kế Hoạch 5 Giai Đoạn: Xây Dựng Trợ Lý AI Xiaozhi ESP32-S3 Tiếng Việt (Chi Phí 0đ)

> **Mục tiêu dự án:** Tích hợp hoàn chỉnh trợ lý giọng nói AI tiếng Việt trên phần cứng ESP32-S3 với khả năng: Hỏi đáp AI, Tra cứu Giá vàng SJC/BTMC, Đọc tin tức VnExpress, Xem thời tiết, Nghe đài VOV và Nghe nhạc Online (ZingMP3) / Offline (Thẻ nhớ SD).
> **Môi trường phát triển:** macOS & Google Antigravity IDE.

---

## Tổng Quan Kiến Trúc & Tài Nguyên Miễn Phí

| Thành phần | Tài nguyên sử dụng | Chi phí | Vai trò trong hệ thống |
| :--- | :--- | :--- | :--- |
| **Bộ não AI (Cloud)** | `https://xiaozhi.me` | 0đ (Free) | Nhận diện giọng nói (ASR), Mô hình ngôn ngữ (LLM Qwen/DeepSeek), Giọng đọc (TTS) và cấp cổng **MCP Endpoint**. |
| **Web Flasher (Firmware)** | `https://tienhuyiot.github.io/esp_web_flasher/` | 0đ (Free) | Nạp Firmware tiếng Việt trực tiếp qua trình duyệt (mở sẵn tính năng phát nhạc, thẻ nhớ, cấu hình Music Server URL). |
| **Cloud MCP Server (Python)** | `https://github.com/TienHuyIoT/mcp-xiaozhi_vietnam` | 0đ (Free) | Chạy script Python kết nối qua WebSocket tới `xiaozhi.me` để cung cấp công cụ tra cứu Giá vàng, Tin tức, Lịch âm mà không cần sửa code C++. |
| **Server Nhạc Online** | `https://github.com/nvhung9/mp3-api` | 0đ (Free) | Deploy lên Render.com hoặc Vercel để lấy luồng stream MP3 từ ZingMP3/NCT truyền về ESP32. |
| **Mã nguồn Firmware (Tùy chọn)** | `https://github.com/TienHuyIoT/xiaozhi-esp32_vietnam` | 0đ (Free) | Dùng khi muốn can thiệp sâu vào C++ (ESP-IDF v5.4+) để thêm tính năng phần cứng. |

---

## GIAI ĐOẠN 1: Chuẩn Bị & Lắp Ráp Phần Cứng (Breadboard - Không Cần Hàn)

### 1.1. Danh sách linh kiện
* **Vi điều khiển:** 01 Mạch **ESP32-S3 N16R8** (16MB Flash, 8MB Octal PSRAM) - Chọn bản đã hàn sẵn chân kim (Header Pin), có 2 cổng USB-C.
* **Thu âm (Microphone):** 01 Module **INMP441** (I2S).
* **Khuếch đại âm thanh:** 01 Module **MAX98357A** (I2S) + 01 Loa 4 Ohm 3W (hoặc dùng DAC PCM5102A).
* **Màn hình hiển thị:** 01 Màn hình **OLED 0.96 inch I2C** (4 chân) hoặc **TFT IPS 1.54 inch SPI**.
* **Phụ kiện:** 01 Module thẻ nhớ MicroSD (nếu nghe nhạc Offline), Breadboard + Dây cắm Jumper Cái-Cái.

### 1.2. Sơ đồ đấu nối chuẩn (Pinout mặc định)
> **Lưu ý quan trọng:** Tuyệt đối **KHÔNG** sử dụng các chân GPIO 35, 36, 37 trên ESP32-S3 N16R8 vì các chân này đã được nối ngầm bên trong chip cho bộ nhớ Octal PSRAM.

* **Module Mic INMP441:**
  * `VDD` -> `3.3V` | `GND` -> `GND` | `L/R` -> `GND`
  * `WS` -> `GPIO 4` | `SCK` -> `GPIO 5` | `SD` -> `GPIO 6`
* **Module Khuếch đại Loa MAX98357A:**
  * `VIN` -> `5V` (hoặc `3.3V`) | `GND` -> `GND`
  * `LRC` -> `GPIO 16` | `BCLK` -> `GPIO 15` | `DIN` -> `GPIO 7`
* **Màn hình OLED 0.96" (I2C):**
  * `VCC` -> `3.3V` | `GND` -> `GND`
  * `SDA` -> `GPIO 41` | `SCL` -> `GPIO 42`

---

## GIAI ĐOẠN 2: Nạp Firmware Mở Qua Trình Duyệt (5 Phút)

1. Mở trình duyệt **Google Chrome** hoặc **Microsoft Edge** trên macOS.
2. Cắm cáp USB-C vào cổng **UART / COM** trên mạch ESP32-S3 (nếu máy chưa nhận cổng nạp, nhấn giữ nút `BOOT`, cắm cáp vào rồi thả nút `BOOT`).
3. Truy cập trang nạp Web Flasher: `https://tienhuyiot.github.io/esp_web_flasher/`
4. Chọn đúng phân loại bo mạch **`ESP32-S3 N16R8`** và loại màn hình tương ứng.
5. Nhấn **Connect** -> Chọn cổng Serial (`cu.usbserial-...` hoặc `cu.wchusbserial...`) -> Nhấn **Install** và chờ hoàn tất 100%.
6. Nhấn nút `RST` (Reset) trên mạch để khởi động lại.

---

## GIAI ĐOẠN 3: Cấu Hình Wi-Fi & Liên Kết Bộ Não AI `xiaozhi.me`

1. **Kết nối Wi-Fi cho ESP32-S3:**
   * Dùng điện thoại/máy tính kết nối vào Wi-Fi phát ra từ mạch có tên **`Xiaozhi-...`**.
   * Truy cập trang cấu hình Captive Portal tại `http://192.168.4.1`.
   * Chọn mạng Wi-Fi 2.4GHz của gia đình, nhập mật khẩu và lưu lại (Tại đây có ô **Music Server URL** sẽ dùng ở Giai đoạn 5).
2. **Kích hoạt thiết bị trên `xiaozhi.me`:**
   * Sau khi kết nối Wi-Fi, màn hình ESP32 sẽ hiển thị và đọc to **Mã kích hoạt 6 chữ số**.
   * Truy cập `https://xiaozhi.me` -> Chuyển ngôn ngữ sang **Tiếng Việt** -> Đăng nhập.
   * Vào **Bảng điều khiển (Console)** -> Nhấn **Thêm thiết bị (Add Device)** -> Nhập mã 6 số.
3. **Thiết lập Vai trò (Role Config / System Prompt):**
   * Chọn LLM (**Qwen** hoặc **DeepSeek**) và Giọng đọc Tiếng Việt.
   * Dán đoạn **System Prompt** sau vào ô giới thiệu vai trò để ép AI luôn ưu tiên gọi công cụ MCP:
     ```text
     Bạn là trợ lý ảo thông minh tại Việt Nam. Thiết bị của bạn đã được kết nối với các công cụ MCP thời gian thực.
     Quy tắc bắt buộc:
     1. Khi người dùng hỏi về giá vàng, tỷ giá, tin tức, thời tiết, lịch âm: TUYỆT ĐỐI KHÔNG trả lời rằng bạn không có dữ liệu thời gian thực. Hãy gọi ngay MCP Tool tương ứng (tra_cuu_gia_vang, doc_tin_tuc_viet_nam...) để lấy dữ liệu mới nhất rồi đọc kết quả ngắn gọn, tự nhiên.
     2. Khi người dùng yêu cầu nghe nhạc, mở đài VOV, mở radio: Hãy gọi ngay MCP Tool phát nhạc/radio tương ứng.
     3. Trả lời ngắn gọn, súc tích, không dùng ký tự Markdown vì câu trả lời sẽ được chuyển thẳng thành giọng nói.
     ```

---

## GIAI ĐOẠN 4: Tích Hợp "Giá Vàng, Tin Tức, Đài VOV" Bằng Cloud MCP (Trên macOS / Google Antigravity)

> **Hướng dẫn cho Agent trong Google Antigravity:** Có thể chạy trực tiếp các lệnh dưới đây trong Terminal của workspace để thiết lập môi trường Python `venv` cô lập, sạch sẽ trên macOS.

### 4.1. Cài đặt Python 3.12 & Khởi tạo môi trường ảo (`venv`) trên macOS
```bash
# 1. Cài đặt Python 3.12 qua Homebrew (nếu chưa có)
brew install python@3.12

# 2. Clone repo MCP mẫu cho Xiaozhi Việt Nam
git clone https://github.com/TienHuyIoT/mcp-xiaozhi_vietnam.git
cd mcp-xiaozhi_vietnam

# 3. Tạo môi trường ảo .venv ngay trong thư mục dự án
$(brew --prefix python@3.12)/bin/python3.12 -m venv .venv

# 4. Kích hoạt môi trường ảo
source .venv/bin/activate

# 5. Cài đặt các thư viện cần thiết
pip install -r requirements.txt
pip install mcp websockets requests feedparser beautifulsoup4
```

### 4.2. Viết mã nguồn MCP Server (`vietnam_services.py`)
Tạo file `vietnam_services.py` trong thư mục dự án với nội dung tối ưu cho giọng đọc TTS:

```python
from mcp.server.fastmcp import FastMCP
import requests
from bs4 import BeautifulSoup
import feedparser
from datetime import datetime

mcp = FastMCP("VietnamServices")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

@mcp.tool()
def tra_cuu_gia_vang() -> str:
    """Gọi công cụ này khi người dùng hỏi về giá vàng SJC, vàng nhẫn 9999 hoặc tỷ giá hôm nay tại Việt Nam."""
    results = []
    time_str = datetime.now().strftime("%H:%M ngày %d/%m/%Y")
    try:
        url_pnj = "https://www.pnj.com.vn/blog/gia-vang/"
        resp = requests.get(url_pnj, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            table = soup.find("table")
            if table:
                for row in table.find_all("tr")[1:]:
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
    except Exception:
        pass

    if not results:
        return "Hiện tại hệ thống cập nhật giá vàng đang bận, bạn vui lòng hỏi lại sau ít phút nhé."

    return f"Giá vàng cập nhật lúc {time_str}:\n" + "\n".join(results[:4]) + "\nLưu ý: 1 lượng vàng bằng 10 chỉ."

@mcp.tool()
def doc_tin_tuc_viet_nam(chu_de: str = "thoi-su") -> str:
    """Lấy 5 tin tức mới nhất từ báo VnExpress. Tham số chu_de nhận một trong các giá trị: thoi-su, the-gioi, kinh-doanh, the-thao, cong-nghe."""
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
        articles = [f"Tin {i}: {entry.title.strip()}." for i, entry in enumerate(feed.entries[:5], 1)]
        return f"Điểm tin VnExpress chuyên mục {chu_de} mới nhất:\n" + "\n".join(articles)
    except Exception as e:
        return f"Lỗi khi tải tin tức: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### 4.3. Kết nối đường ống MCP lên `xiaozhi.me`
1. Truy cập `https://xiaozhi.me` -> **Console** -> Copy đường dẫn **MCP Endpoint** (có dạng `wss://api.tenclass.net/mcp/?token=...`).
2. Chạy lệnh kết nối trong Terminal:
```bash
export MCP_ENDPOINT="wss://api.tenclass.net/mcp/?token=DAN_TOKEN_CUA_BAN_VAO_DAY"
python mcp_pipe.py vietnam_services.py
```
3. Kiểm tra trên giao diện `xiaozhi.me` thấy báo **Online / Connected** là có thể hỏi trực tiếp trên mạch ESP32.

### 4.4. Lệnh gỡ bỏ sạch sẽ Python trên macOS (Khi không còn nhu cầu sử dụng)
```bash
rm -rf ~/Desktop/mcp-xiaozhi_vietnam
brew uninstall python@3.12 && brew autoremove && brew cleanup
```

---

## GIAI ĐOẠN 5: Triển Khai Server Nghe Nhạc Online & Chạy 24/7 Miễn Phí

### 5.1. Dựng Server Nhạc Online (`mp3-api`) miễn phí
1. Truy cập `https://github.com/nvhung9/mp3-api` và nhấn **Fork** về tài khoản GitHub cá nhân.
2. Đăng nhập `https://render.com` hoặc `https://vercel.com` bằng GitHub.
3. Tạo **New Web Service** -> Chọn repo `mp3-api` vừa Fork -> Nhấn **Deploy**.
4. Copy đường dẫn URL sau khi deploy xong.
5. Mở lại trang cấu hình của ESP32 (hoặc cấu hình trong MCP), dán URL này vào mục **Music Server** để phát nhạc ZingMP3 bằng giọng nói.

### 5.2. Đưa Cloud MCP (`vietnam_services.py`) lên chạy Cloud 24/7 (Không cần bật máy Mac)
* Đẩy thư mục `mcp-xiaozhi_vietnam` (đã thêm `vietnam_services.py`) lên một Private GitHub Repo của bạn.
* Trên **Render.com** (hoặc **PythonAnywhere** / **Fly.io**), tạo một dịch vụ miễn phí nối với repo đó, thêm biến môi trường (Environment Variable) tên là `MCP_ENDPOINT` chứa link `wss://...` của bạn và đặt lệnh khởi chạy (Start Command) là:
  ```bash
  python mcp_pipe.py vietnam_services.py
  ```