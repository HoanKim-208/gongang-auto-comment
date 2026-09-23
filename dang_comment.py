#!/usr/bin/env python3
"""
Tự động đăng comment điều hướng dưới bài mới của Facebook Page Gọn Gàng Shop.

Cách hoạt động:
- Đọc lich.json (kiểu cũ, 1 file nhiều mục) và mọi file lich/*.json
  (kiểu mới, mỗi bài 1 file - thêm bài chỉ là upload 1 file mới, không phải
  ghi đè file chung nên không đè mất trạng thái máy vừa ghi).
- Tìm các mục đã tới giờ và chưa đăng.
- Tìm bài viết/Reel mới nhất của Page có caption chứa "tu_khoa" của mục đó.
- Chống trùng: bài đã có comment của chính Page trùng nội dung (hoặc chứa
  gongangshop.vn) thì không đăng nữa.
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
GOC = os.path.dirname(os.path.abspath(__file__))
FILE_LICH = os.path.join(GOC, "lich.json")
THU_MUC_LICH = os.path.join(GOC, "lich")

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


def _gon(chu):
    return " ".join((chu or "").split()).lower()


def tim_comment_da_co(post_id, page_id, noi_dung):
    """Trả về (loai, comment_id) nếu bài đã có comment của shop, ngược lại None.

    loai = "cua_minh": Page đã đăng đúng nội dung này (lượt trước đăng xong
           nhưng chưa kịp ghi trạng thái) -> coi như xong.
    loai = "link":    đã có comment khác chứa link web -> đánh "trung".
    """
    kq = goi_api(f"{post_id}/comments", {"fields": "message,from", "limit": 100})
    muc_tieu = _gon(noi_dung)
    for cmt in kq.get("data", []):
        tin = cmt.get("message") or ""
        cua_page = (cmt.get("from") or {}).get("id") == str(page_id)
        if cua_page and _gon(tin) == muc_tieu:
            return "cua_minh", cmt.get("id", "")
        if "gongangshop.vn" in tin.lower():
            return "link", cmt.get("id", "")
    return None


def doc_lich():
    """Trả về danh sách (duong_file, du_lieu, danh_sach_muc).

    lich.json là 1 mảng; mỗi file lich/*.json là 1 mục (object) hoặc 1 mảng.
    """
    nguon = []
    if os.path.exists(FILE_LICH):
        with open(FILE_LICH, encoding="utf-8") as f:
            du_lieu = json.load(f)
        nguon.append((FILE_LICH, du_lieu, du_lieu))
    if os.path.isdir(THU_MUC_LICH):
        for ten in sorted(os.listdir(THU_MUC_LICH)):
            if not ten.endswith(".json"):
                continue
            duong = os.path.join(THU_MUC_LICH, ten)
            try:
                with open(duong, encoding="utf-8") as f:
                    du_lieu = json.load(f)
            except ValueError as e:
                print(f"[LỖI] {ten} không phải JSON hợp lệ: {e}")
                continue
            nguon.append((duong, du_lieu, du_lieu if isinstance(du_lieu, list) else [du_lieu]))
    return nguon


def ghi_file(duong, du_lieu):
    with open(duong, "w", encoding="utf-8") as f:
        json.dump(du_lieu, f, ensure_ascii=False, indent=2)
        f.write("\n")


def xu_ly_muc(muc, page_id, bay_gio):
    """Xử lý 1 mục lịch. Trả về True nếu mục có thay đổi cần ghi lại."""
    if muc.get("trang_thai") != "cho":
        return False

    gio_hen = datetime.fromisoformat(muc["thoi_gian"]).replace(tzinfo=GIO_VN)
    if bay_gio < gio_hen:
        return False

    ten = muc.get("ten", muc["tu_khoa"])

    if bay_gio > gio_hen + timedelta(hours=HAN_CHOT_GIO):
        muc["trang_thai"] = "qua_han"
        muc["ghi_chu"] = f"Quá {HAN_CHOT_GIO} tiếng vẫn không tìm thấy bài"
        print(f"[QUÁ HẠN] {ten}")
        return True

    try:
        bai = tim_bai_theo_tu_khoa(page_id, muc["tu_khoa"])
    except RuntimeError as e:
        print(f"[LỖI] {ten}: {e}")
        return False

    if not bai:
        print(f"[CHỜ] {ten}: chưa thấy bài chứa '{muc['tu_khoa']}', thử lại lần sau")
        return False

    try:
        da_co = tim_comment_da_co(bai["id"], page_id, muc["noi_dung"])
        if da_co and da_co[0] == "cua_minh":
            muc.update(trang_thai="xong", post_id=bai["id"], comment_id=da_co[1],
                       ghi_chu="comment đã có sẵn từ lượt trước, không đăng lại")
            print(f"[ĐÃ CÓ] {ten}: comment đã lên từ lượt trước, chỉ ghi lại trạng thái")
            return True
        if da_co:
            muc.update(trang_thai="trung", post_id=bai["id"])
            print(f"[BỎ QUA] {ten}: bài đã có comment chứa link web")
            return True

        kq = goi_api(f"{bai['id']}/comments", du_lieu={"message": muc["noi_dung"]})
    except RuntimeError as e:
        print(f"[LỖI] {ten}: {e}")
        return False

    muc.update(trang_thai="xong", post_id=bai["id"], comment_id=kq.get("id", ""),
               dang_luc=bay_gio.strftime("%Y-%m-%d %H:%M"))
    print(f"[XONG] {ten} -> bài {bai['id']}")
    return True


def main():
    thieu = [b for b in ("FB_PAGE_TOKEN", "FB_PAGE_ID") if not os.environ.get(b)]
    if thieu:
        print(f"Thiếu biến môi trường: {', '.join(thieu)}")
        return 1

    page_id = os.environ["FB_PAGE_ID"]
    bay_gio = datetime.now(GIO_VN)
    so_file_doi = 0

    for duong, du_lieu, ds_muc in doc_lich():
        co_thay_doi = False
        for muc in ds_muc:
            if xu_ly_muc(muc, page_id, bay_gio):
                co_thay_doi = True
        if co_thay_doi:
            ghi_file(duong, du_lieu)
            so_file_doi += 1

    print(f"Đã cập nhật {so_file_doi} file lịch" if so_file_doi else "Không có gì để làm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
