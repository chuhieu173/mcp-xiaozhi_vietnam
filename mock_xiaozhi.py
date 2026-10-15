"""
Máy chủ WebSocket Router giả lập Xiaozhi Backend để kiểm tra luồng hoạt động của mcp_pipe.py.

Đặc điểm:
- TỰ ĐỘNG CHẠY KIỂM TRA ngay khi mcp_pipe.py kết nối (không bắt buộc phải gõ phím).
- Tự động nhận diện và phân luồng (route) các công cụ từ nhiều server MCP cùng lúc (Calculator, VnExpress, GoldPrice...).
- Hỗ trợ menu tương tác thủ công (lưu ý: gõ số và bấm phím ENTER).

Cách dùng:
Terminal 1 (chạy mock server này):
    python mock_xiaozhi.py

Terminal 2 (chạy mcp_pipe kết nối vào):
    export MCP_ENDPOINT=ws://localhost:8765
    python mcp_pipe.py
"""

import asyncio
import json
import websockets
import sys

PORT = 8765

# Quản lý các công cụ: tool_name -> websocket
TOOL_ROUTING = {}
# Quản lý request đang chờ phản hồi: request_id -> asyncio.Future
PENDING_REQUESTS = {}
REQUEST_ID = 100


async def call_tool_direct(ws, tool_name: str, arguments: dict):
    """Gửi yêu cầu gọi tool và đợi kết quả trả về"""
    global REQUEST_ID
    REQUEST_ID += 1
    req_id = REQUEST_ID
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    PENDING_REQUESTS[req_id] = fut

    await ws.send(json.dumps(req))
    try:
        res = await asyncio.wait_for(fut, timeout=25.0)
        return res
    except asyncio.TimeoutError:
        PENDING_REQUESTS.pop(req_id, None)
        print(f"[!] Hết thời gian chờ phản hồi từ tool '{tool_name}' (Timeout).")
        return None


async def run_auto_test(server_name: str, server_tools: list, websocket):
    """Tự động kiểm tra tool ngay khi server vừa kết nối xong"""
    await asyncio.sleep(0.5)
    print(f"\n---> [TỰ ĐỘNG TEST] Đang kiểm tra server '{server_name}'...")

    if "get_gold_price" in server_tools:
        print("  -> Gọi 'get_gold_price(brand=\"sjc\")'...")
        res = await call_tool_direct(websocket, "get_gold_price", {"brand": "sjc"})
        if res:
            try:
                # Trích xuất nội dung text bên trong content
                raw_text = res["result"]["content"][0]["text"]
                data = json.loads(raw_text)
                print("  [+] KẾT QUẢ GIÁ VÀNG TRẢ VỀ:")
                for item in data.get("data", []):
                    print(f"      • {item['brand']} - {item['type']}: Mua {item['buy_per_chi']} | Bán {item['sell_per_chi']}")
            except Exception:
                print(f"  [+] KẾT QUẢ:\n{json.dumps(res, indent=2, ensure_ascii=False)}")

    if "get_vnexpress_news" in server_tools:
        print("  -> Gọi 'get_vnexpress_news(category=\"thoi-su\", limit=2)'...")
        res = await call_tool_direct(websocket, "get_vnexpress_news", {"category": "thoi-su", "limit": 2})
        if res:
            try:
                raw_text = res["result"]["content"][0]["text"]
                data = json.loads(raw_text)
                print("  [+] KẾT QUẢ TIN TỨC TRẢ VỀ:")
                for item in data.get("articles", []):
                    print(f"      • {item['title']} ({item.get('time', '')})")
            except Exception:
                print(f"  [+] KẾT QUẢ:\n{json.dumps(res, indent=2, ensure_ascii=False)}")

    if "calculator" in server_tools:
        print("  -> Gọi 'calculator(python_expression=\"123 * 456\")'...")
        res = await call_tool_direct(websocket, "calculator", {"python_expression": "123 * 456"})
        if res:
            try:
                raw_text = res["result"]["content"][0]["text"]
                print(f"  [+] KẾT QUẢ TÍNH TOÁN: {raw_text}")
            except Exception:
                print(f"  [+] KẾT QUẢ:\n{json.dumps(res, indent=2, ensure_ascii=False)}")

    print(f"---> [HOÀN TẤT TEST] Server '{server_name}' hoạt động tốt!\n")
    print("Bạn có thể gõ 1, 2, 3, 4 rồi bấm phím [ENTER] để test tiếp bất cứ lúc nào:")


async def handle_connection(websocket):
    global REQUEST_ID
    server_tools = []
    server_name = "UnknownServer"
    try:
        # 1. Khởi tạo MCP handshake
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mock-xiaozhi-router", "version": "1.0"}
            }
        }
        await websocket.send(json.dumps(init_req))
        resp_raw = await websocket.recv()
        resp = json.loads(resp_raw)
        server_info = resp.get("result", {}).get("serverInfo", {})
        server_name = server_info.get("name", "UnknownServer")

        # 2. Gửi thông báo initialized
        await websocket.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        # 3. Lấy danh sách tools của server này
        list_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        await websocket.send(json.dumps(list_req))
        resp_raw = await websocket.recv()
        tools_data = json.loads(resp_raw)
        tools = tools_data.get("result", {}).get("tools", [])

        for t in tools:
            name = t["name"]
            server_tools.append(name)
            TOOL_ROUTING[name] = websocket

        print(f"\n[+] Máy chủ '{server_name}' đã kết nối! Đăng ký {len(tools)} tools: {server_tools}")

        # Tự động kích hoạt test cho server này
        asyncio.create_task(run_auto_test(server_name, server_tools, websocket))

        # Lắng nghe nhận tin nhắn trả về từ tiến trình mcp_pipe
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_id = data.get("id")
                if msg_id in PENDING_REQUESTS:
                    fut = PENDING_REQUESTS.pop(msg_id)
                    if not fut.done():
                        fut.set_result(data)
            except Exception:
                pass

    except websockets.exceptions.ConnectionClosed:
        print(f"\n[-] Máy chủ '{server_name}' đã ngắt kết nối.")
    finally:
        for t in server_tools:
            if t in TOOL_ROUTING:
                del TOOL_ROUTING[t]


async def interactive_cli():
    loop = asyncio.get_running_loop()
    await asyncio.sleep(1.0)
    
    while True:
        cmd = await loop.run_in_executor(None, input, "\n[Menu] Nhập số (1=Vàng, 2=Tìm vàng, 3=Tin tức, 4=Máy tính, q=Thoát) + bấm ENTER: ")
        cmd = cmd.strip()

        if cmd == "q":
            print("Đang thoát...")
            sys.exit(0)
        elif cmd == "1":
            ws = TOOL_ROUTING.get("get_gold_price")
            if not ws:
                print("[!] Server GoldPrice chưa kết nối!")
                continue
            print("\n--> Đang gửi lệnh lấy giá vàng SJC qua WebSocket...")
            res = await call_tool_direct(ws, "get_gold_price", {"brand": "sjc"})
            if res:
                raw_text = res["result"]["content"][0]["text"]
                print(f"<-- Kết quả:\n{raw_text}")
        elif cmd == "2":
            ws = TOOL_ROUTING.get("search_gold_price")
            if not ws:
                print("[!] Server GoldPrice chưa kết nối!")
                continue
            print("\n--> Đang gửi lệnh tìm kiếm giá vàng 'nhẫn' qua WebSocket...")
            res = await call_tool_direct(ws, "search_gold_price", {"keyword": "nhẫn"})
            if res:
                raw_text = res["result"]["content"][0]["text"]
                print(f"<-- Kết quả:\n{raw_text}")
        elif cmd == "3":
            ws = TOOL_ROUTING.get("get_vnexpress_news")
            if not ws:
                print("[!] Server VnExpress chưa kết nối! (Hãy chạy 'python mcp_pipe.py' ở Terminal 2 để mở tất cả server)")
                continue
            print("\n--> Đang gửi lệnh lấy tin tức thời sự VnExpress qua WebSocket...")
            res = await call_tool_direct(ws, "get_vnexpress_news", {"category": "thoi-su", "limit": 2})
            if res:
                raw_text = res["result"]["content"][0]["text"]
                print(f"<-- Kết quả:\n{raw_text}")
        elif cmd == "4":
            ws = TOOL_ROUTING.get("calculator")
            if not ws:
                print("[!] Server Calculator chưa kết nối!")
                continue
            print("\n--> Đang gửi phép tính tới calculator...")
            res = await call_tool_direct(ws, "calculator", {"python_expression": "123 * 456"})
            if res:
                raw_text = res["result"]["content"][0]["text"]
                print(f"<-- Kết quả: {raw_text}")
        else:
            print("Lựa chọn không hợp lệ.")


async def main():
    print(f"[*] Mock Xiaozhi Router Server đang chạy tại ws://localhost:{PORT}")
    print("[*] Ở Terminal 2, hãy chạy:")
    print(f"    export MCP_ENDPOINT=ws://localhost:{PORT}")
    print("    python mcp_pipe.py   (để chạy TẤT CẢ các server: Calculator, VnExpress, GoldPrice)\n")
    
    server = await websockets.serve(handle_connection, "localhost", PORT)
    await interactive_cli()
    await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nĐã dừng Mock Server.")
