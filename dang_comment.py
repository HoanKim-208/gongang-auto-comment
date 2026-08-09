#!/usr/bin/env python3
"""
Tự động đăng comment điều hướng dưới bài mới của Facebook Page Gọn Gàng Shop.

Cách hoạt động:
- Đọc lich.json, tìm các mục đã tới giờ và chưa đăng.
- Tìm bài viết/Reel mới nhất của Page có caption chứa "tu_khoa" của mục đó.
- Kiểm tra chống trùng: bài đã có comment chứa gongangshop.vn thì bỏ qua.
- Đăng comment dưới danh nghĩa Page, rồi ghi lại trạng thái vào lich.json.

Biến môi trường cần có:
  FB_PAGE_TOKEN  - Token người dùng hệ thống (cất trong GitHub Secrets)
  FB_PAGE_ID     - ID của Page
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://graph.facebook.com/v21.0"
GIO_VN = timezone(timedelta(hours=7))
FILE_LICH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lich.json")

HAN_CHOT_GIO = 8

_token_page = None


def goi_api_tho(duong_dan, tham_so=None, du_lieu=None, token=None):
    tham_so = dict(tham_so or {})
    tham_so["access_token"] = token
    url = f"{API}/{duong_dan}?" + urllib.parse.urlencode(tham_so)
    body = urllib.parse.urlencode(du_lieu).encode() if du_lieu else None
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        chi_tiet = e.read().decode()[:500]
        raise RuntimeError(f"Facebook trả lỗi {e.code}: {chi_tiet}") from None


def lay_token_page(page_id):
    """Đổi token người dùng hệ thống sang Page access token."""
    global _token_page
    if _token_page:
        return _token_page

    goc = os.environ["FB_PAGE_TOKEN"]
    try:
        kq = goi_api_tho(page_id, {"fields": "access_token"}, token=goc)
        _token_page = kq.get("access_token") or goc
        print("Đã đổi sang Page access token" if kq.get("access_token")
              else "Token đưa vào đã là Page token, dùng trực tiếp")
    except RuntimeError as e:
        print(f"Không đổi được sang Page token ({e}); thử dùng token gốc")
        _token_page = goc

    return _token_page


def goi_api(duong_dan, tham_so=None, du_lieu=None):
    return goi_api_tho(duong_dan, tham_so, du_lieu,
                       token=lay_token_page(os.environ["FB_PAGE_ID"]))


def tim_bai_theo_tu_khoa(page_id, tu_khoa):
    """Tìm bài mới nhất của Page có caption chứa từ khoá.

    Quét cả /feed lẫn /video_reels vì Reel không phải lúc nào cũng ở /feed.
    """
    tu_khoa = tu_khoa.lower()
    ung_vien = []

    for duong_dan, truong in (
        (f"{page_id}/feed", "id,message,created_time"),
        (f"{page_id}/video_reels", "id,description,created_time"),
    ):
        try:
            kq = goi_api(duong_dan, {"fields": truong, "limit": 25})
        except RuntimeError as e:
            print(f"  (không đọc được {duong_dan}: {e})")
            continue

        data = kq.get("data", [])
        print(f"  {duong_dan}: lấy được {len(data)} mục")
        for bai in data:
            caption = bai.get("message") or bai.get("description") or ""
            if tu_khoa in caption.lower():
                ung_vien.append(bai)

    if not ung_vien:
        return None

    ung_vien.sort(key=lambda b: b.get("created_time") or "", reverse=True)
    return ung_vien[0]


def da_co_comment_link(post_id):
    kq = goi_api(f"{post_id}/comments", {"fields": "message", "limit": 50})
    for cmt in kq.get("data", []):
        if "gongangshop.vn" in (cmt.get("message") or "").lower():
            return True
    return False


def main():
    thieu = [b for b in ("FB_PAGE_TOKEN", "FB_PAGE_ID") if not os.environ.get(b)]
    if thieu:
        print(f"Thiếu biến môi trường: {', '.join(thieu)}")
        return 1

    page_id = os.environ["FB_PAGE_ID"]
    with open(FILE_LICH, encoding="utf-8") as f:
        lich = json.load(f)

    bay_gio = datetime.now(GIO_VN)
    co_thay_doi = False

    for muc in lich:
        if muc.get("trang_thai") != "cho":
            continue

        gio_hen = datetime.fromisoformat(muc["thoi_gian"]).replace(tzinfo=GIO_VN)
        if bay_gio < gio_hen:
            continue

        ten = muc.get("ten", muc["tu_khoa"])

        if bay_gio > gio_hen + timedelta(hours=HAN_CHOT_GIO):
            muc["trang_thai"] = "qua_han"
            muc["ghi_chu"] = f"Quá {HAN_CHOT_GIO} tiếng vẫn không tìm thấy bài"
            co_thay_doi = True
            print(f"[QUÁ HẠN] {ten}")
            continue

        try:
            bai = tim_bai_theo_tu_khoa(page_id, muc["tu_khoa"])
        except RuntimeError as e:
            print(f"[LỖI] {ten}: {e}")
            continue

        if not bai:
            print(f"[CHỜ] {ten}: chưa thấy bài chứa '{muc['tu_khoa']}', thử lại lần sau")
            continue

        try:
            if da_co_comment_link(bai["id"]):
                muc["trang_thai"] = "trung"
                muc["post_id"] = bai["id"]
                co_thay_doi = True
                print(f"[BỎ QUA] {ten}: bài đã có comment chứa link web")
                continue

            kq = goi_api(f"{bai['id']}/comments", du_lieu={"message": muc["noi_dung"]})
        except RuntimeError as e:
            print(f"[LỖI] {ten}: {e}")
            continue

        muc["trang_thai"] = "xong"
        muc["post_id"] = bai["id"]
        muc["comment_id"] = kq.get("id", "")
        muc["dang_luc"] = bay_gio.strftime("%Y-%m-%d %H:%M")
        co_thay_doi = True
        print(f"[XONG] {ten} -> bài {bai['id']}")

    if co_thay_doi:
        with open(FILE_LICH, "w", encoding="utf-8") as f:
            json.dump(lich, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("Đã cập nhật lich.json")
    else:
        print("Không có gì để làm")

    return 0


if __name__ == "__main__":
    sys.exit(main())
