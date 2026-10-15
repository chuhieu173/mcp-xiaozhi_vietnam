"""
Script kiểm tra YouTube Music Server từ máy tính (không cần mạch ESP32).
Chạy:
    python test_music_server.py [URL_SERVER_RENDER] [TÊN_BÀI_HÁT]
Ví dụ:
    python test_music_server.py https://ten-app.onrender.com "lạc trôi"
"""

import sys
import json
import requests
import webbrowser

def test_server(server_url: str, song_name: str = "lạc trôi"):
    server_url = server_url.rstrip("/")
    print("=" * 65)
    print(f"[*] ĐANG KIỂM TRA MUSIC SERVER: {server_url}")
    print("=" * 65)

    # 1. Kiểm tra trang chủ / health check
    print("\n1. Kiểm tra kết nối máy chủ...")
    try:
        r_home = requests.get(f"{server_url}/", timeout=15)
        print(f"   [+] Phản hồi: {r_home.status_code} - {r_home.text[:120]}")
    except Exception as e:
        print(f"   [!] Lỗi kết nối: {e}")
        return

    # 2. Tìm kiếm bài hát
    print(f"\n2. Tìm kiếm bài hát: '{song_name}'...")
    search_url = f"{server_url}/api/search"
    try:
        r_search = requests.get(search_url, params={"q": song_name}, timeout=20)
        if r_search.status_code != 200:
            print(f"   [!] API search lỗi: HTTP {r_search.status_code} - {r_search.text}")
            return

        data = r_search.json()
        items = data.get("data", {}).get("items", [])
        if not items:
            print("   [!] Không tìm thấy bài hát nào.")
            return

        print(f"   [+] Tìm thấy {len(items)} bài hát:")
        for idx, item in enumerate(items, 1):
            print(f"       [{idx}] {item.get('title')} - ID: {item.get('encodeId')}")

        # Chọn bài đầu tiên để test phát nhạc
        first_song = items[0]
        song_id = first_song.get("encodeId")
        song_title = first_song.get("title")

    except Exception as e:
        print(f"   [!] Lỗi khi gọi API search: {e}")
        return

    # 3. Lấy link stream và nghe thử
    print(f"\n3. Kiểm tra link phát nhạc (Stream) cho bài: '{song_title}'...")
    stream_url = f"{server_url}/api/song/stream?id={song_id}"
    print(f"   URL stream: {stream_url}")

    try:
        # Không theo redirect tự động để kiểm tra mã 302
        r_stream = requests.get(stream_url, allow_redirects=False, timeout=20)
        print(f"   [+] Mã HTTP trả về: {r_stream.status_code}")
        
        if r_stream.status_code in [301, 302, 307, 308]:
            redirect_url = r_stream.headers.get("Location")
            print(f"   [+] Đã chuyển hướng 302 thành công tới luồng Audio của YouTube!")
            print(f"   -> Link âm thanh: {redirect_url[:100]}...")
            
            # Mở trình duyệt để nghe trực tiếp trên máy Mac
            print("\n4. Đang mở trình duyệt để phát nhạc trực tiếp ra loa máy tính...")
            webbrowser.open(stream_url)
            print("   [✓] Hãy kiểm tra tab trình duyệt vừa mở để nghe nhạc!")
        elif r_stream.status_code == 200:
            print("   [+] Server trả về luồng âm thanh trực tiếp (200 OK)!")
            webbrowser.open(stream_url)
        else:
            print(f"   [!] Không lấy được luồng âm thanh: {r_stream.status_code} - {r_stream.text}")

    except Exception as e:
        print(f"   [!] Lỗi khi test stream: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        server = sys.argv[1]
        song = sys.argv[2] if len(sys.argv) > 2 else "lạc trôi"
    else:
        server = input("Nhập URL server vừa deploy (ví dụ: https://my-app.onrender.com): ").strip()
        song = input("Nhập tên bài hát muốn nghe thử (mặc định 'lạc trôi'): ").strip() or "lạc trôi"

    if server:
        test_server(server, song)
    else:
        print("Vui lòng cung cấp URL server.")
