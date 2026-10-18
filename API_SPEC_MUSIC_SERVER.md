# 🎵 ĐẶC TẢ YÊU CẦU API SERVER NHẠC CHO XIAOZHI-ESP32
*(Music Server API Specification for XiaoZhi AI)*

Tài liệu này mô tả chi tiết giao thức HTTP Request và Response mà thiết bị **XiaoZhi ESP32** sử dụng để tìm kiếm, stream nhạc MP3 và hiển thị lời bài hát (.LRC).

---

## 1. Luồng hoạt động (Workflow)

```
[ESP32] --(1) GET /stream_pcm?song=...&artist=...--> [Music Server]
[ESP32] <-------------(2) JSON response ------------- [Music Server]
          (chứa audio_url và lyric_url)

[ESP32] ------------(3) GET audio_url --------------> [Music Server / CDN]
[ESP32] <---------- Stream MP3 Audio chunks --------- [Music Server / CDN]

[ESP32] ------------(4) GET lyric_url --------------> [Music Server / CDN]
[ESP32] <----------- File text .LRC (UTF-8) --------- [Music Server / CDN]
```

---

## 2. API Tìm kiếm & Lấy luồng bài hát (`/stream_pcm`)

### 2.1. Request từ thiết bị ESP32

* **Method**: `GET`
* **Base URL**: `https://xiaozhi-youtube-music.onrender.com`
* **Path**: `/stream_pcm`
* **Query Parameters**:
  * `song` *(bắt buộc, string, URL-encoded)*: Tên bài hát người dùng yêu cầu (ví dụ: `song=Di+ve+nha` hoặc `song=See+Tinh`).
  * `artist` *(tùy chọn, string, URL-encoded)*: Tên ca sĩ hoặc nghệ sĩ nếu người dùng nhắc đến (ví dụ: `artist=Den+Vau`).

#### Headers do ESP32 gửi lên:
```http
GET /stream_pcm?song=Di+ve+nha&artist=Den+Vau HTTP/1.1
Host: xiaozhi-youtube-music.onrender.com
User-Agent: ESP32-Music-Player/1.0
Accept: application/json
X-MAC-Address: 24:DC:C3:XX:XX:XX
X-Chip-ID: 24DCC3XXXXXX
X-Timestamp: 1729158000
X-Dynamic-Key: 3A7F8B2C9D1E...
```

> **Lưu ý xác thực**: 
> Các header `X-*` là thông tin định danh phần cứng của ESP32. Nếu server của bạn hoạt động công khai hoặc bảo mật theo phương thức khác, server **có thể bỏ qua** các header này.

---

### 2.2. Response từ Server trả về

* **HTTP Status Code**: `200 OK`
* **Content-Type**: `application/json; charset=utf-8`

#### Trường hợp 1: Tìm thấy bài hát thành công (Khuyên dùng)
```json
{
  "title": "Đi Về Nhà",
  "artist": "Đen Vâu x JustaTee",
  "audio_url": "https://xiaozhi-youtube-music.onrender.com/stream/di-ve-nha.mp3",
  "lyric_url": "https://xiaozhi-youtube-music.onrender.com/lyrics/di-ve-nha.lrc"
}
```

#### Quy chuẩn các trường trong JSON:
| Trường | Kiểu dữ liệu | Bắt buộc | Mô tả |
| :--- | :--- | :---: | :--- |
| **`audio_url`** | `string` | **CÓ** | Đường dẫn đến file âm thanh **MP3**. Có thể là Full URL (`https://...`) hoặc Relative path bắt đầu bằng `/` (`/stream/...`). |
| **`lyric_url`** | `string` | Không | Link tới file lời bài hát chuẩn `.lrc`. Nếu bài hát không có lời, hãy để chuỗi rỗng `""` hoặc `null`. |
| **`title`** | `string` | Không | Tên bài hát hiển thị trên màn hình thiết bị và log hệ thống. |
| **`artist`** | `string` | Không | Tên ca sĩ/nghệ sĩ biểu diễn. |

#### Trường hợp 2: Không tìm thấy bài hát
Server có thể trả về:
* **Cách 1**: HTTP Status Code `404 Not Found`.
* **Cách 2**: HTTP Status Code `200 OK` với trường `audio_url` rỗng:
  ```json
  {
    "error": "Song not found",
    "audio_url": ""
  }
  ```
*(Khi `audio_url` rỗng hoặc không kết nối được, ESP32 sẽ tự động báo lỗi: "Failed to get the music resource, please try another song" và trả lại quyền hội thoại cho AI).*

---

## 3. Đặc tả luồng Audio Stream (`audio_url`)

Sau khi nhận được `audio_url`, tiến trình phát nhạc của ESP32 sẽ mở kết nối HTTP stream:

### 3.1. Request từ ESP32:
```http
GET {audio_url} HTTP/1.1
User-Agent: ESP32-Music-Player/1.0
Accept: */*
Range: bytes=0-
```

### 3.2. Yêu cầu định dạng âm thanh từ Server:
* **HTTP Status**: Hỗ trợ `200 OK` hoặc `206 Partial Content` (Range Request).
* **Content-Type**: `audio/mpeg` (hoặc `audio/mp3`, `application/octet-stream`).
* **Định dạng file bắt buộc**: Chuẩn **MP3** (MPEG-1 Audio Layer III). 
  * Bộ giải mã trên ESP32 sử dụng thư viện `libhelix-mp3`.
  * **Bitrate khuyên dùng**: `128 kbps` (hoặc `192 kbps` / `320 kbps`).
  * **Sample Rate**: `44100 Hz` hoặc `48000 Hz` (Mono hoặc Stereo).
  * ⚠️ *Không trả về định dạng `.m4a`, `.aac`, `.opus`, `.flac`, `.wav` vì ESP32 chưa tích hợp bộ giải mã cho các định dạng này.*

---

## 4. Đặc tả File Lời bài hát (`lyric_url`)

Nếu có trường `lyric_url`, ESP32 sẽ tải file text lời bài hát để đồng bộ thời gian thực lên màn hình OLED/LCD:

* **Method**: `GET`
* **Content-Type**: `text/plain; charset=utf-8`
* **Định dạng**: File text chuẩn **LRC**, mỗi dòng gồm nhãn thời gian `[mm:ss.xx]` theo sau là câu hát:

```text
[00:04.20]Đường về nhà là vào tim ta
[00:07.80]Dẫu nắng dung giăng hay mưa xối xả
[00:11.50]Bao nhiêu năm rồi làm việc bôn ba
[00:15.10]Bước chân mệt nhoài lại về bên mái nhà
[00:19.40]...
```

---

## 5. Ví dụ mã nguồn triển khai Server mẫu (Node.js / Express)

Dưới đây là đoạn code server mẫu để phía Backend có thể tham khảo nhanh:

```javascript
const express = require('express');
const app = express();

// 1. Endpoint tìm kiếm bài hát
app.get('/stream_pcm', (req, res) => {
    const songName = req.query.song;
    const artistName = req.query.artist || '';

    console.log(`[ESP32 Request] Song: "${songName}", Artist: "${artistName}"`);

    // TODO: Tìm kiếm bài hát trên YouTube / Database của bạn
    const foundSong = findSongFromYouTube(songName, artistName);

    if (!foundSong) {
        return res.status(200).json({
            title: songName,
            artist: artistName,
            audio_url: "",
            lyric_url: ""
        });
    }

    // Trả về thông tin cho ESP32
    return res.status(200).json({
        title: foundSong.title,
        artist: foundSong.artist,
        audio_url: `https://xiaozhi-youtube-music.onrender.com/stream/${foundSong.id}.mp3`,
        lyric_url: foundSong.lrcUrl || ""
    });
});

// 2. Endpoint stream file MP3 (Hỗ trợ Range Request)
app.get('/stream/:id.mp3', (req, res) => {
    const songId = req.params.id;
    // Pipe luồng audio MP3 tới client
    res.setHeader('Content-Type', 'audio/mpeg');
    getMp3AudioStream(songId).pipe(res);
});

app.listen(process.env.PORT || 5005, () => {
    console.log('Music Server is running...');
});
```
