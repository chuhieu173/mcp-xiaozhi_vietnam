"""
Script kiểm tra nhanh các công cụ MCP mà không cần mạch phần cứng ESP32.
Chạy: python test_tools.py
"""
import json
import sys

def print_box(title):
    print("\n" + "=" * 60)
    print(f"   {title}")
    print("=" * 60)

def test_gold_price():
    print_box("TEST TRA CỨU GIÁ VÀNG")
    import gold_price
    print("1. Lấy giá vàng SJC:")
    res_sjc = gold_price.get_gold_price("sjc")
    print(json.dumps(res_sjc, indent=2, ensure_ascii=False))

    print("\n2. Tìm kiếm giá vàng theo từ khóa 'nhẫn':")
    res_search = gold_price.search_gold_price("nhẫn")
    print(json.dumps(res_search, indent=2, ensure_ascii=False))

def test_vnexpress():
    print_box("TEST TIN TỨC VNEXPRESS")
    import VnExpress
    print("1. Lấy 3 tin thời sự mới nhất:")
    news = VnExpress.get_vnexpress_news("thoi-su", limit=3)
    print(json.dumps(news, indent=2, ensure_ascii=False))

    articles = news.get("articles", [])
    if articles:
        first_url = articles[0]["url"]
        print(f"\n2. Thử đọc chi tiết bài báo đầu tiên: {first_url}")
        content = VnExpress.get_article_content(first_url)
        # In tóm tắt để đỡ dài console
        summary = {
            "title": content.get("title"),
            "author": content.get("author"),
            "publish_time": content.get("publish_time"),
            "content_snippet": content.get("content", "")[:300] + "..."
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))

def test_calculator():
    print_box("TEST CALCULATOR")
    import calculator
    expr = "15 * 85000 + math.sqrt(144)"
    print(f"Tính biểu thức: {expr}")
    res = calculator.calculator(expr)
    print(json.dumps(res, indent=2, ensure_ascii=False))

def main():
    # Hỗ trợ truyền tham số trực tiếp: python test_tools.py 1 hoặc python test_tools.py all
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower().strip()
        if arg in ["1", "gold", "vang"]:
            test_gold_price()
            return
        elif arg in ["2", "news", "vnexpress"]:
            test_vnexpress()
            return
        elif arg in ["3", "calc"]:
            test_calculator()
            return
        elif arg in ["4", "all"]:
            test_gold_price()
            test_vnexpress()
            test_calculator()
            return

    while True:
        print_box("MENU KIỂM TRA MCP TOOLS (KHÔNG CẦN ESP32)")
        print("1. Kiểm tra Công cụ Giá Vàng (gold_price.py)")
        print("2. Kiểm tra Công cụ VnExpress (VnExpress.py)")
        print("3. Kiểm tra Công cụ Máy Tính (calculator.py)")
        print("4. Chạy kiểm tra TẤT CẢ các công cụ")
        print("0. Thoát")
        
        choice = input("\nNhập lựa chọn của bạn (0-4) rồi bấm ENTER: ").strip()
        if choice == "1":
            test_gold_price()
        elif choice == "2":
            test_vnexpress()
        elif choice == "3":
            test_calculator()
        elif choice == "4":
            test_gold_price()
            test_vnexpress()
            test_calculator()
        elif choice == "0":
            print("Tạm biệt!")
            break
        else:
            print("Lựa chọn không hợp lệ, vui lòng chọn lại.")

if __name__ == "__main__":
    main()
