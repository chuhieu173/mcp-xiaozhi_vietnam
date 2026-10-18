"""
Script kiểm tra Xiaozhi Music Server (SoundCloud + ZingMP3) từ máy tính.
Chạy:
    python test_music_server.py [URL_SERVER] [TÊN_BÀI_HÁT]
Ví dụ:
    python test_music_server.py http://127.0.0.1:8000 "lạc trôi"
"""

import sys
import json
import requests
import webbrowser
import time

def test_server(server_url: str, song_name: str = "lạc trôi"):
    server_url = server_url.rstrip("/")
    print("=" * 65)
    print(f"[*] ĐANG KIỂM TRA MUSIC SERVER: {server_url}")
    print("=" * 65)

    # 1. Kiểm tra trạng thái máy chủ
    print("\n1. Kiểm tra kết nối máy chủ...")
    try:
        r_home = requests.get(f"{server_url}/", timeout=10)
        print(f"   [+] Phản hồi HTTP {r_home.status_code}: {r_home.text[:120]}")
    except Exception as e:
        print(f"   [!] Lỗi kết nối: {e}")
        return

    # 2. Kiểm tra API chuẩn của ESP32: /stream_pcm
    print(f"\n2. Kiểm tra API ESP32 (/stream_pcm?song={song_name})...")
    pcm_url = f"{server_url}/stream_pcm"
    audio_url = ""
    try:
        t0 = time.time()
        r_pcm = requests.get(pcm_url, params={"song": song_name}, timeout=15)
        t_pcm = time.time() - t0
        print(f"   [+] Phản hồi HTTP {r_pcm.status_code} (thời gian: {t_pcm:.2f}s)")
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
        return

    # 3. Kiểm tra luồng Audio Stream (MP3 định dạng bắt buộc cho ESP32)
    if audio_url:
        print(f"\n3. Kiểm tra Audio Stream từ audio_url...")
        try:
            t0 = time.time()
            r_audio = requests.get(audio_url, stream=True, timeout=15, headers={"Range": "bytes=0-"})
            print(f"   [+] Mã HTTP: {r_audio.status_code}")
            content_type = r_audio.headers.get("content-type", "")
            accept_ranges = r_audio.headers.get("accept-ranges", "")
            print(f"   [+] Content-Type: {content_type}")
            print(f"   [+] Accept-Ranges: {accept_ranges}")
            
            # Đọc 32KB đầu tiên và đo thời gian đến byte đầu
            first_chunks = b""
            for chunk in r_audio.iter_content(chunk_size=8192):
                first_chunks += chunk
                if len(first_chunks) >= 32768:
                    break
            ttfb = time.time() - t0
            print(f"   [+] Thời gian nạp 32KB đầu tiên: {ttfb:.2f}s (Cực nhanh cho ESP32!)")

            is_mp3 = first_chunks.startswith(b"ID3") or (len(first_chunks) >= 2 and first_chunks[0] == 0xFF and (first_chunks[1] & 0xE0) == 0xE0)
            print(f"   [+] Chuẩn MP3 (libhelix-mp3 ESP32): {'ĐẠT CHUẨN' if is_mp3 else 'Khác: ' + str(first_chunks[:10])}")

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
        server = input("Nhập URL server (mặc định http://127.0.0.1:8000): ").strip() or "http://127.0.0.1:8000"
        song = input("Nhập tên bài hát muốn nghe thử (mặc định 'lạc trôi'): ").strip() or "lạc trôi"

    if server:
        test_server(server, song)
