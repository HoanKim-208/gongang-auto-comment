#!/usr/bin/env python3
"""
Hẹn giờ đăng Reel lên Facebook Page Gọn Gàng Shop qua Reels Publishing API.

Cách dùng: bỏ vào thư mục cho-dang/ một cặp file cùng tên
  <ten>.mp4   - video đã làm sạch metadata
  <ten>.json  - thông tin bài:
      {
        "ten": "Aula Nova 75 PRO (Reel bàn nhỏ)",
        "gio_dang": "2026-09-29T22:00:00",      # giờ VN
        "caption": "...",
        "tu_khoa": "cụm chữ độc nhất trong caption",   # cần nếu có comment
        "comment": "Nội dung comment điều hướng"        # bỏ trống = không comment
      }

Script sẽ:
  1. Tải video lên Facebook và hẹn giờ (bài hiện trong MBS -> Đã lên lịch).
  2. Tự tạo lich/<ten>.json cho comment, hẹn sau giờ đăng 5 phút.
  3. Chuyển file .json sang da-hen/ (kèm video_id), xoá file .mp4.
Lỗi thì ghi "loi" vào file .json, để nguyên trong cho-dang/ và thoát mã 1.
Hẹn được nhưng Facebook báo video xử lý lỗi -> vẫn chuyển sang da-hen/ (để
không tải trùng) nhưng workflow báo ĐỎ, xem trường "canh_bao".

Chặn trước khi tải: tu_khoa trùng một mục lịch đang chờ, hoặc đã có trong
bài cũ gần đây của Page (auto comment sẽ trúng nhầm bài cũ).

Biến môi trường: FB_PAGE_TOKEN, FB_PAGE_ID (giống dang_comment.py).
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from dang_comment import API, doc_lich, goi_api_tho, lay_token_page, tim_bai_theo_tu_khoa

GIO_VN = timezone(timedelta(hours=7))
GOC = os.path.dirname(os.path.abspath(__file__))
THU_MUC_CHO = os.path.join(GOC, "cho-dang")
THU_MUC_XONG = os.path.join(GOC, "da-hen")
THU_MUC_LICH = os.path.join(GOC, "lich")

# Khung giờ auto comment chạy (cron 20:00-23:55 giờ VN)
GIO_SOM_NHAT = 20
GIO_MUON_NHAT = 23  # comment hẹn +5 phút nên bài phải lên trước 23:50


class LoiBai(Exception):
    pass


def thoi_luong_video(duong_dan):
    try:
        kq = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", duong_dan],
            capture_output=True, text=True, timeout=30)
        return float(kq.stdout.strip())
    except Exception:
        return None


def kiem_tra(bai, duong_video, bay_gio):
    for truong in ("ten", "gio_dang", "caption"):
        if not str(bai.get(truong, "")).strip():
            raise LoiBai(f"thiếu trường '{truong}'")

    gio = datetime.fromisoformat(bai["gio_dang"]).replace(tzinfo=GIO_VN)
    if gio < bay_gio + timedelta(minutes=15):
        raise LoiBai(f"giờ đăng {bai['gio_dang']} phải sau hiện tại ít nhất 15 phút")
    if gio > bay_gio + timedelta(days=28):
        raise LoiBai("Facebook chỉ cho hẹn tối đa 29 ngày")

    if bai.get("comment"):
        if not str(bai.get("tu_khoa", "")).strip():
            raise LoiBai("có comment thì phải có 'tu_khoa'")
        if bai["tu_khoa"].lower() not in bai["caption"].lower():
            raise LoiBai("'tu_khoa' không nằm trong caption")
        phut = gio.hour * 60 + gio.minute
        if not (GIO_SOM_NHAT * 60 <= phut <= GIO_MUON_NHAT * 60):
            raise LoiBai("bài có comment phải đăng trong 20:00-23:00 (khung chạy auto comment)")

    dai = thoi_luong_video(duong_video)
    if dai is not None and not (3 <= dai <= 90):
        raise LoiBai(f"video dài {dai:.1f}s, Reel chỉ nhận 3-90 giây")
    return gio


def kiem_tu_khoa_trung(bai, page_id):
    """Chặn tu_khoa đụng mục lịch đang chờ hoặc đụng bài cũ của Page."""
    tk = bai["tu_khoa"].strip().lower()
    for _, _, ds_muc in doc_lich():
        for muc in ds_muc:
            if muc.get("trang_thai") != "cho":
                continue
            khac = str(muc.get("tu_khoa", "")).strip().lower()
            if khac and (khac in tk or tk in khac):
                raise LoiBai(f"'tu_khoa' đụng mục lịch đang chờ '{muc.get('ten')}' ('{muc.get('tu_khoa')}')")
    cu = tim_bai_theo_tu_khoa(page_id, tk)
    if cu:
        raise LoiBai(f"'tu_khoa' đã có trong bài cũ {cu.get('id')} ({cu.get('created_time', '')[:10]}) - chọn cụm khác")


def tai_video_len(page_id, token, duong_video):
    kq = goi_api_tho(f"{page_id}/video_reels", du_lieu={"upload_phase": "start"}, token=token)
    video_id = kq["video_id"]
    upload_url = kq.get("upload_url") or f"https://rupload.facebook.com/video-upload/{API.rsplit('/', 1)[-1]}/{video_id}"

    kich_thuoc = os.path.getsize(duong_video)
    with open(duong_video, "rb") as f:
        du_lieu = f.read()
    req = urllib.request.Request(upload_url, data=du_lieu, method="POST", headers={
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(kich_thuoc),
        "Content-Type": "application/octet-stream",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            kq_up = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise LoiBai(f"upload video lỗi {e.code}: {e.read().decode()[:300]}") from None
    if not kq_up.get("success"):
        raise LoiBai(f"upload video không thành công: {kq_up}")
    return video_id


def hen_gio(page_id, token, video_id, bai, gio):
    kq = goi_api_tho(f"{page_id}/video_reels", du_lieu={
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "SCHEDULED",
        "scheduled_publish_time": str(int(gio.timestamp())),
        "description": bai["caption"],
    }, token=token)
    if not kq.get("success", True):
        raise LoiBai(f"hẹn giờ không thành công: {kq}")


def cho_xu_ly(token, video_id, toi_da_giay=240):
    """Chờ Facebook nhận xong video. Trả về trạng thái cuối cùng đọc được."""
    trang_thai = {}
    het_gio = time.time() + toi_da_giay
    while time.time() < het_gio:
        kq = goi_api_tho(video_id, {"fields": "status"}, token=token)
        trang_thai = kq.get("status", {})
        vs = trang_thai.get("video_status")
        print(f"    trạng thái video: {vs}")
        if vs in ("error", "upload_failed", "expired"):
            raise LoiBai(f"Facebook báo video lỗi: {trang_thai}")
        if vs in ("ready", "upload_complete") or trang_thai.get("processing_phase", {}).get("status") == "complete":
            break
        time.sleep(15)
    return trang_thai


def them_comment(bai, gio, ten_file):
    os.makedirs(THU_MUC_LICH, exist_ok=True)
    muc = {
        "ten": bai["ten"],
        "tu_khoa": bai["tu_khoa"],
        "thoi_gian": (gio + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"),
        "noi_dung": bai["comment"],
        "trang_thai": "cho",
    }
    ghi_json(os.path.join(THU_MUC_LICH, "reel-" + ten_file), muc)


def ghi_json(duong, bai):
    with open(duong, "w", encoding="utf-8") as f:
        json.dump(bai, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    thieu = [b for b in ("FB_PAGE_TOKEN", "FB_PAGE_ID") if not os.environ.get(b)]
    if thieu:
        print(f"Thiếu biến môi trường: {', '.join(thieu)}")
        return 1

    if "--thu-quyen" in sys.argv:
        # Chỉ mở phiên upload để thử quyền pages_manage_posts, KHÔNG đăng gì.
        token = lay_token_page(os.environ["FB_PAGE_ID"])
        try:
            kq = goi_api_tho(f"{os.environ['FB_PAGE_ID']}/video_reels", du_lieu={"upload_phase": "start"}, token=token)
        except RuntimeError as e:
            print(f"[THỬ QUYỀN] HỎNG: {e}")
            return 1
        print(f"[THỬ QUYỀN] OK - token đăng được Reel (phiên thử {kq.get('video_id')}, không đăng gì)")
        return 0

    if not os.path.isdir(THU_MUC_CHO):
        print("Không có thư mục cho-dang/")
        return 0

    page_id = os.environ["FB_PAGE_ID"]
    bay_gio = datetime.now(GIO_VN)
    ds = sorted(f for f in os.listdir(THU_MUC_CHO) if f.endswith(".json"))
    if not ds:
        print("cho-dang/ trống, không có gì để làm")
        return 0

    token = lay_token_page(page_id)
    os.makedirs(THU_MUC_XONG, exist_ok=True)
    co_loi = False

    for ten_file in ds:
        duong_json = os.path.join(THU_MUC_CHO, ten_file)
        duong_video = duong_json[:-5] + ".mp4"
        with open(duong_json, encoding="utf-8") as f:
            bai = json.load(f)
        ten = bai.get("ten", ten_file)
        print(f"[BÀI] {ten}")

        if bai.get("video_id"):
            print("  đã hẹn từ trước (có video_id), bỏ qua")
            continue

        try:
            if not os.path.exists(duong_video):
                raise LoiBai(f"thiếu video {os.path.basename(duong_video)}")
            gio = kiem_tra(bai, duong_video, bay_gio)
            if bai.get("comment"):
                kiem_tu_khoa_trung(bai, page_id)
            print(f"  tải video lên ({os.path.getsize(duong_video) // 1024} KB)...")
            video_id = tai_video_len(page_id, token, duong_video)
            print(f"  video_id = {video_id}, hẹn giờ {bai['gio_dang']}...")
            hen_gio(page_id, token, video_id, bai, gio)
        except (LoiBai, RuntimeError, KeyError, ValueError) as e:
            bai["loi"] = f"{bay_gio:%Y-%m-%d %H:%M} {e}"
            ghi_json(duong_json, bai)
            print(f"[LỖI] {ten}: {e}")
            co_loi = True
            continue

        # Đã hẹn thành công trên Facebook -> từ đây KHÔNG được coi là lỗi nữa,
        # kẻo lần chạy sau tải trùng video.
        try:
            trang_thai = cho_xu_ly(token, video_id)
        except (LoiBai, RuntimeError) as e:
            trang_thai = {}
            bai["canh_bao"] = (f"đã hẹn nhưng Facebook báo video lỗi: {e} - "
                               "vào MBS > Đã lên lịch kiểm tra, cần thì xoá bài và đăng lại")
            print(f"[CẢNH BÁO] {ten}: {bai['canh_bao']}")
            co_loi = True

        bai.pop("loi", None)
        bai["video_id"] = video_id
        bai["hen_luc"] = bay_gio.strftime("%Y-%m-%d %H:%M")
        bai["trang_thai_video"] = trang_thai.get("video_status", "")
        if bai.get("comment"):
            them_comment(bai, gio, ten_file)
            print(f"  đã thêm comment vào lich.json lúc {(gio + timedelta(minutes=5)):%H:%M}")
        ghi_json(os.path.join(THU_MUC_XONG, ten_file), bai)
        os.remove(duong_json)
        os.remove(duong_video)
        print(f"[XONG] {ten} -> video {video_id}, lên lúc {bai['gio_dang']}")

    return 1 if co_loi else 0


if __name__ == "__main__":
    sys.exit(main())
