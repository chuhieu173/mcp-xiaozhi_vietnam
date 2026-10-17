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

    # 2. Kiểm tra API chuẩn của ESP32: /stream_pcm
    print(f"\n2. Kiểm tra API ESP32 (/stream_pcm?song={song_name})...")
    pcm_url = f"{server_url}/stream_pcm"
    audio_url = ""
    try:
        r_pcm = requests.get(pcm_url, params={"song": song_name}, timeout=20)
        print(f"   [+] Phản hồi: HTTP {r_pcm.status_code}")
        if r_pcm.status_code == 200:
            pcm_data = r_pcm.json()
            print(f"   [+] JSON trả về: {json.dumps(pcm_data, ensure_ascii=False, indent=2)}")
            audio_url = pcm_data.get("audio_url")
            lyric_url = pcm_data.get("lyric_url")
            print(f"   -> Tên bài: {pcm_data.get('title')} ({pcm_data.get('artist')})")
            print(f"   -> Audio URL: {audio_url}")
            print(f"   -> Lyric URL: {lyric_url}")
        else:
            print(f"   [!] API /stream_pcm lỗi: HTTP {r_pcm.status_code} - {r_pcm.text}")
    except Exception as e:
        print(f"   [!] Lỗi khi gọi /stream_pcm: {e}")

    # 3. Kiểm tra luồng Audio Stream (MP3 định dạng bắt buộc cho ESP32)
    if audio_url:
        print(f"\n3. Kiểm tra Audio Stream từ audio_url...")
        try:
            r_audio = requests.get(audio_url, stream=True, timeout=25)
            print(f"   [+] Mã HTTP: {r_audio.status_code}")
            content_type = r_audio.headers.get("content-type", "")
            print(f"   [+] Content-Type: {content_type}")
            
            # Đọc 128 byte đầu tiên để kiểm tra Magic Byte MP3
            first_chunk = next(r_audio.iter_content(chunk_size=128), b"")
            is_mp3 = first_chunk.startswith(b"ID3") or (len(first_chunk) >= 2 and first_chunk[0] == 0xFF and (first_chunk[1] & 0xE0) == 0xE0)
            print(f"   [+] Chuẩn MP3 (libhelix-mp3 ESP32): {'ĐẠT CHUẨN (ID3/MPEG Frame)' if is_mp3 else 'Khác: ' + str(first_chunk[:10])}")

            print("\n4. Đang mở trình duyệt để phát thử nhạc...")
            webbrowser.open(audio_url)
            print("   [✓] Đã gửi URL phát nhạc sang trình duyệt!")
        except Exception as e:
            print(f"   [!] Lỗi kiểm tra Audio Stream: {e}")

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
