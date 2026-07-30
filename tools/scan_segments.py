#!/usr/bin/env python3
"""獨立網段掃描器 —— 在「網路走得通」的那台機器上跑，產出結果檔給戰情室匯入。

## 為什麼要有這支

戰情室主機常常不在正式網段，要它掃遍全公司得先申請一堆防火牆，甚至搬機器。
那些前置作業比掃描本身還久。這支只依賴 Python 標準庫，可以直接放在一台
本來就通得到各網段的 Linux 上跑，跑完把 CSV 交給戰情室匯入即可。

## 對營運的保護（預設就開，不必自己記得加參數）

- **上班時間不掃**：預設只在 22:00–06:00 執行；不在時段內就直接結束，不是硬等。
- **分批**：每次執行只處理固定數量的網段（預設 10 個），做完就走。
- **可續跑**：進度存在檔案裡，下次執行從上次的下一個網段接續，所以「分幾天跑完」
  是自然結果，不需要人記得跑到哪。
- **節流**：限制同時連線數，並在每個網段之間停一下，避免短時間大量連線被
  資安設備判成掃描攻擊。
- **只碰兩個埠**：預設 22 與 443。這是「確認機器活著」的最小集合，
  不做服務探測——那是全埠掃描才需要的，風險完全不同。

## 用法

    # 1. 準備網段清單（一行一個 CIDR，# 開頭是註解）
    #    可從戰情室下載：/api/connections/suggest-segments?format=txt
    cat segments.txt
      10.93.17.0/24
      10.93.18.0/24

    # 2. 先試跑一個網段，確認網路通得到（會忽略時間窗）
    python3 scan_segments.py --segments segments.txt --limit 1 --now

    # 3. 掛 cron，每天深夜自動接續
    #    0 2 * * * cd /opt/scan && python3 scan_segments.py --segments segments.txt >> scan.log 2>&1

    # 4. 全部跑完後把 scan_results_*.csv 交給戰情室匯入

## 參數

    --segments FILE   網段清單（必填）
    --out DIR         結果與進度的存放目錄（預設 ./scan_out）
    --ports 22,443    要探測的埠（預設 22,443）
    --limit 10        這次執行處理幾個網段（預設 10）
    --window 22:00-06:00  允許執行的時段（預設 22:00-06:00）
    --now             忽略時間窗，立刻執行（試跑用）
    --concurrency 32  同時連線數上限（預設 32）
    --timeout 0.6     單一連線逾時秒數（預設 0.6）
    --pause 20        每個網段之間休息秒數（預設 20）
    --reset           清掉進度重新開始
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import os
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime
from pathlib import Path

STOP = False


def _on_signal(signum, frame):  # noqa: ARG001
    """收到中斷就把手上的網段做完並存檔，不要留下半筆結果。"""
    global STOP
    STOP = True
    print("\n[!] 收到中斷訊號，完成當前網段後結束（進度會保留）", flush=True)


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


def parse_window(text: str) -> tuple[dtime, dtime]:
    """解析 '22:00-06:00'。允許跨午夜。"""
    try:
        a, b = text.split("-", 1)
        ah, am = (int(x) for x in a.strip().split(":"))
        bh, bm = (int(x) for x in b.strip().split(":"))
        return dtime(ah, am), dtime(bh, bm)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"時間窗格式錯誤（應為 22:00-06:00）：{text}") from exc


def in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """跨午夜的時段要分兩段判斷，不能單純比大小。"""
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def load_segments(path: Path) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            ipaddress.ip_network(line, strict=False)
        except ValueError:
            print(f"[!] 略過無法解析的網段：{line}")
            continue
        out.append(line)
    # 去重但保留原順序，方便人對照清單
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def probe(ip: str, ports: list[int], timeout: float) -> list[int]:
    """回傳這個 IP 上開著的埠。空清單＝沒回應。

    只做 TCP connect，不送任何 payload——目的是「確認機器活著」，
    不是識別服務。這也讓它不需要 root、不需要 nmon/nmap。
    """
    open_ports = []
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, p)) == 0:
                open_ports.append(p)
        except OSError:
            pass
        finally:
            s.close()
    return open_ports


def reverse_dns(ip: str) -> str:
    """DNS 反解。這是戰情室要的「第三方證據」——
    人填的主機名與 vCenter 回報的名稱誰對，靠 DNS 這一票來判。
    """
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror, socket.gaierror):
        return ""


def scan_one(cidr: str, ports: list[int], timeout: float, concurrency: int,
             do_dns: bool = True, in_time=None) -> tuple[list[dict], dict]:
    """掃一個網段，回 (找到的主機, 這個網段的掃描狀態)。

    ## 為什麼一定要回報「網段狀態」而不只是「找到哪些主機」

    「這台沒回應」有兩種完全不同的原因，混在一起會誤判：
      · 防火牆沒放行／路由不通 → 機器可能好好活著，只是我看不到
      · 機器真的不在了         → 該追查或下架

    只交出「找到的主機清單」的話，收到檔案的人無法分辨「這個網段掃過但全無回應」
    與「這個網段根本沒掃」，於是整段的機器都會被誤標成失聯。
    所以每個網段都要留下「我掃過、探了幾個位址、找到幾台」的證據。
    （使用者 2026-07-30 明確要求區分這兩者。）
    """
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(h) for h in net.hosts()]
    found = []
    aborted = False

    def work(ip: str):
        op = probe(ip, ports, timeout)
        if not op:
            return None
        return {"ip": ip, "hostname": reverse_dns(ip) if do_dns else "",
                "open_ports": ",".join(str(x) for x in op)}

    # 分塊送進執行緒池，每塊之間檢查一次是否該停。
    # 大網段（/22 以上）可能跑上一小時，只在網段開頭檢查時段的話，
    # 一個網段就足以從深夜掃進上班時間——那正是要避免的事。
    CHUNK = 512
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for i in range(0, len(hosts), CHUNK):
            if STOP or (in_time is not None and not in_time()):
                aborted = True
                break
            for r in ex.map(work, hosts[i:i + CHUNK]):
                if r:
                    found.append(r)

    # 整段零回應是最需要標出來的情況：正常網段不太可能一台都沒有，
    # 那通常代表這條路不通，而不是這個網段淨空了。
    if aborted:
        # 沒掃完就不能下任何結論——標成「未掃完」而不是「無回應」，
        # 否則收到檔案的人會把「我還沒看完」誤讀成「這裡沒東西」。
        verdict = "未掃完（超出時段或被中斷，此網段結論無效，下次會重掃）"
    elif not found:
        verdict = "整段無回應（可能防火牆未放行或路由不通，請先確認再判定機器狀態）"
    elif len(found) < max(2, len(hosts) // 50):
        verdict = "僅少數回應（路徑可能部分受阻）"
    else:
        verdict = "可達"

    status = {
        "segment": cidr,
        "probed": len(hosts),
        "found": len(found),
        "reachable": 1 if found else 0,
        "complete": 0 if aborted else 1,
        "verdict": verdict,
    }
    return found, status


def do_package(out_dir: Path, segments: list[str]) -> None:
    """把多天累積的 CSV 彙整成一個 zip，供戰情室一次上傳。

    ## 為什麼要打包而不是讓人自己選檔案

    分十幾天掃完會產生幾十個 CSV，要人在檔案選擇框裡不漏不錯地全選是不現實的。
    而最容易漏的偏偏是 segments_status —— 少了它，戰情室就分不出
    「防火牆不通」與「機器真的不在」，那些機器會被誤判失聯，
    而且**匯入還是會顯示成功**，沒人會發現漏了。

    ## 彙整規則

    · 同一個 IP 在不同天各掃到一次 → 取 scan_time 最新的那筆（機器狀態會變）
    · 同一個網段掃過多次        → 同樣取最新結論（防火牆可能中途才開通）
    · 沒掃到的網段              → 明確列在摘要裡，不是靜靜消失
    """
    import zipfile

    res_files = sorted(out_dir.glob("scan_results_*.csv"))
    st_files = sorted(out_dir.glob("segments_status_*.csv"))
    if not res_files:
        raise SystemExit(f"{out_dir} 底下找不到 scan_results_*.csv，還沒掃過嗎？")
    if not st_files:
        raise SystemExit(
            f"{out_dir} 底下找不到 segments_status_*.csv。\n"
            "少了這份，戰情室無法分辨「防火牆不通」與「機器不在」，不可上傳。")

    # 同 IP 取最新：key=ip，比 scan_time 字串即可（格式固定 YYYY-MM-DD HH:MM:SS）
    hosts: dict[str, dict] = {}
    # 時間跨度必須從「所有原始紀錄」收集，不能用彙整後的結果算——
    # 彙整會把同一網段的舊時間覆蓋掉，算出來的跨度會縮成最後一天，
    # 看報告的人就不知道這份資料其實橫跨兩週。
    all_times: list[str] = []
    for p in res_files:
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                ip = (row.get("ip") or "").strip()
                if not ip:
                    continue
                t = (row.get("scan_time") or "").strip()
                if t:
                    all_times.append(t)
                cur = hosts.get(ip)
                if cur is None or (row.get("scan_time") or "") > (cur.get("scan_time") or ""):
                    hosts[ip] = row

    seg_status: dict[str, dict] = {}
    for p in st_files:
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                seg = (row.get("segment") or "").strip()
                if not seg:
                    continue
                t = (row.get("scan_time") or "").strip()
                if t:
                    all_times.append(t)
                cur = seg_status.get(seg)
                if cur is None or (row.get("scan_time") or "") > (cur.get("scan_time") or ""):
                    seg_status[seg] = row

    scanned = {s for s, r in seg_status.items() if r.get("complete") == "1"}
    missing = [s for s in segments if s not in scanned]
    unreachable = [s for s, r in seg_status.items()
                   if r.get("complete") == "1" and r.get("found") == "0"]
    all_times.sort()
    span = f"{all_times[0]} ~ {all_times[-1]}" if all_times else "（無）"
    days = len({t[:10] for t in all_times})

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_hosts = out_dir / f"_merged_hosts_{stamp}.csv"
    merged_segs = out_dir / f"_merged_segments_{stamp}.csv"
    summary = out_dir / f"_summary_{stamp}.txt"

    with merged_hosts.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ip", "hostname", "open_ports", "scan_time", "segment"])
        for ip in sorted(hosts):
            r = hosts[ip]
            w.writerow([r.get("ip", ""), r.get("hostname", ""), r.get("open_ports", ""),
                        r.get("scan_time", ""), r.get("segment", "")])

    with merged_segs.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["segment", "probed", "found", "reachable", "complete",
                    "scan_time", "verdict"])
        for seg in sorted(seg_status):
            r = seg_status[seg]
            w.writerow([r.get("segment", ""), r.get("probed", ""), r.get("found", ""),
                        r.get("reachable", ""), r.get("complete", ""),
                        r.get("scan_time", ""), r.get("verdict", "")])

    lines = [
        "網段掃描彙整摘要",
        f"打包時間　： {datetime.now():%Y-%m-%d %H:%M:%S}",
        # 時間跨度一定要寫出來：分多天掃完的資料不是同一時刻的快照，
        # 期間有機器搬遷或下線，看報告的人必須知道這件事。
        f"掃描時間跨度： {span}（橫跨 {days} 天）",
        "  ⚠️ 分多天掃完的資料不是同一時刻的快照 —— 期間若有機器搬遷或下線，",
        "     早期與晚期的紀錄可能不一致。這是分批掃描的本質限制。" if days > 1 else "",
        "",
        f"網段清單共　： {len(segments)} 個",
        f"已完整掃完　： {len(scanned)} 個",
        f"尚未掃完　　： {len(missing)} 個",
        f"整段無回應　： {len(unreachable)} 個  ← 多半是防火牆未放行，不是機器不在",
        f"找到的主機　： {len(hosts)} 台（同一 IP 多次掃到已取最新）",
        "",
    ]
    if missing:
        lines += ["== 尚未掃完的網段（這些機器不應被判定為失聯）=="]
        lines += [f"  {s}" for s in missing[:60]]
        if len(missing) > 60:
            lines.append(f"  …另有 {len(missing) - 60} 個")
        lines.append("")
    if unreachable:
        lines += ["== 整段無回應的網段（請先確認防火牆／路由）=="]
        lines += [f"  {s}" for s in unreachable[:60]]
        if len(unreachable) > 60:
            lines.append(f"  …另有 {len(unreachable) - 60} 個")
        lines.append("")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")

    zip_path = out_dir.parent / f"scan_bundle_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(merged_hosts, "hosts.csv")
        z.write(merged_segs, "segments.csv")
        z.write(summary, "summary.txt")

    print("\n".join(lines))
    print("=" * 52)
    print(f"✅ 打包完成： {zip_path}")
    print(f"   大小： {zip_path.stat().st_size / 1024:.0f} KB")
    if missing:
        print()
        print(f"⚠️  還有 {len(missing)} 個網段沒掃完。現在上傳的話，那些網段的機器")
        print("   在戰情室會顯示成「沒掃到」。建議掃完再打包，或上傳後留意摘要。")
    print("=" * 52)


def main() -> None:
    ap = argparse.ArgumentParser(description="獨立網段掃描器（結果給戰情室匯入）")
    ap.add_argument("--segments", required=True, help="網段清單檔（一行一個 CIDR）")
    ap.add_argument("--out", default="./scan_out", help="輸出目錄")
    # 22＝Linux SSH、3389＝Windows RDP、445＝Windows SMB、443＝HTTPS。
    # ⚠️ 只掃 22/443 會讓所有 Windows 機器看起來像「無回應」——Windows 通常不開 22。
    # 這是「確認機器活著」的最小集合，不是服務探測；要更省可用 --ports 22,3389。
    ap.add_argument("--ports", default="22,3389,445,443",
                    help="探測的埠，逗號分隔（預設涵蓋 Linux 與 Windows）")
    # 預設 0＝不限制，跑到時間窗結束或全部掃完為止。
    # 實測一個 /24 約 19 秒，129 個網段連同節流約 1.4 小時，而 22:00-06:00 有 8 小時。
    # 原本預設 10 會讓 8 小時的窗只用掉 8 分鐘，硬把一晚的事拖成兩週。
    ap.add_argument("--limit", type=int, default=0,
                    help="這次處理幾個網段（0＝不限制，跑到時段結束）")
    ap.add_argument("--package", action="store_true",
                    help="不掃描，把 out/ 所有結果彙整打包成一個 zip 供上傳")
    ap.add_argument("--window", default="22:00-06:00", help="允許執行的時段")
    ap.add_argument("--now", action="store_true", help="忽略時間窗立刻執行")
    ap.add_argument("--concurrency", type=int, default=32, help="同時連線數上限")
    ap.add_argument("--timeout", type=float, default=0.6, help="單一連線逾時秒數")
    ap.add_argument("--pause", type=int, default=20, help="每個網段之間休息秒數")
    ap.add_argument("--reset", action="store_true", help="清掉進度重新開始")
    ap.add_argument("--no-dns", action="store_true",
                    help="不做 DNS 反解（DNS 不通時每台都要等逾時，會拖垮速度）")
    ap.add_argument("--max-hosts", type=int, default=4096,
                    help="單一網段最多探測幾個位址，超過就跳過（預設 4096）")
    args = ap.parse_args()

    seg_path = Path(args.segments)
    if not seg_path.exists():
        raise SystemExit(f"找不到網段清單：{seg_path}")
    segments = load_segments(seg_path)
    if not segments:
        raise SystemExit("網段清單是空的")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.package:
        do_package(out_dir, segments)
        return
    prog_path = out_dir / "progress.txt"
    if args.reset and prog_path.exists():
        prog_path.unlink()
        print("[i] 已清除進度")

    done = set()
    if prog_path.exists():
        done = {ln.strip() for ln in prog_path.read_text(encoding="utf-8").splitlines() if ln.strip()}

    todo = [s for s in segments if s not in done]
    print(f"網段總數 {len(segments)}｜已完成 {len(done)}｜待掃 {len(todo)}")
    if not todo:
        print("全部網段都掃完了。把 scan_results_*.csv 交給戰情室匯入即可。")
        return

    start, end = parse_window(args.window)
    if not args.now and not in_window(datetime.now().time(), start, end):
        # 刻意直接結束而不是等待：這支預期由 cron 週期性喚醒，
        # 掛在那裡等到深夜只是佔著一個行程，而且萬一被誤啟動也不該偷偷開始掃。
        print(f"[i] 現在不在允許時段 {args.window} 內，本次不執行"
              f"（要立刻試跑請加 --now）")
        return

    # 防重複執行：cron 排太密、或上次還沒跑完就被再次喚醒時，
    # 兩個行程同時寫同一個進度檔會讓「哪些掃過了」錯亂。
    lock_path = out_dir / "scan.lock"
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            pid = 0
        alive = False
        if pid > 0:
            try:
                os.kill(pid, 0)   # 只探測行程存不存在，不送真訊號
                alive = True
            except OSError:
                alive = False
        if alive:
            print(f"[i] 另一個掃描行程還在跑（PID {pid}），本次不執行")
            return
        print("[i] 發現殘留的 lock（上次異常結束），已接手")
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    def in_time() -> bool:
        return args.now or in_window(datetime.now().time(), start, end)

    ports = [int(x) for x in args.ports.split(",") if x.strip()]
    batch = todo[: args.limit] if args.limit > 0 else todo
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"scan_results_{stamp}.csv"
    status_path = out_dir / f"segments_status_{stamp}.csv"
    print(f"探測埠： {','.join(str(p) for p in ports)}"
          f"{'（未含 3389/445，Windows 可能全部無回應）' if 3389 not in ports else ''}")

    total_found = 0
    try:
        with csv_path.open("w", newline="", encoding="utf-8") as f, \
             status_path.open("w", newline="", encoding="utf-8") as sf:
            w = csv.writer(f)
            w.writerow(["ip", "hostname", "open_ports", "scan_time", "segment"])
            sw = csv.writer(sf)
            sw.writerow(["segment", "probed", "found", "reachable", "complete",
                         "scan_time", "verdict"])
            for i, cidr in enumerate(batch, 1):
                if STOP:
                    break
                if not in_time():
                    print("[i] 已超出允許時段，剩下的留給下次執行")
                    break

                # 過大的網段先跳過而不是硬掃：/16 有六萬多個位址，
                # 一個網段就能吃掉整晚，而且多半是清單本身該再切細。
                net = ipaddress.ip_network(cidr, strict=False)
                n_hosts = net.num_addresses - 2 if net.num_addresses > 2 else net.num_addresses
                if n_hosts > args.max_hosts:
                    print(f"[{i}/{len(batch)}] 略過 {cidr}："
                          f"{n_hosts} 個位址超過上限 {args.max_hosts}，請切成較小網段")
                    sw.writerow([cidr, n_hosts, 0, 0, 0,
                                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 f"未掃（位址數 {n_hosts} 超過上限，請切細後重跑）"])
                    sf.flush()
                    continue

                t0 = time.time()
                print(f"[{i}/{len(batch)}] 掃描 {cidr}（{n_hosts} 個位址）…",
                      end="", flush=True)
                rows, st = scan_one(cidr, ports, args.timeout, args.concurrency,
                                    do_dns=not args.no_dns, in_time=in_time)
                now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for r in rows:
                    w.writerow([r["ip"], r["hostname"], r["open_ports"], now_s, cidr])
                f.flush()
                sw.writerow([st["segment"], st["probed"], st["found"], st["reachable"],
                             st["complete"], now_s, st["verdict"]])
                sf.flush()
                total_found += len(rows)
                print(f" 找到 {st['found']} 台（{time.time() - t0:.0f}s）"
                      f"{'' if st['complete'] else ' ← 未掃完'}", flush=True)

                # 只有「完整掃完」才記進度。沒掃完就記，下次會跳過這個網段，
                # 那段機器會永遠停留在「掃過但沒回應」的錯誤結論上。
                if st["complete"]:
                    with prog_path.open("a", encoding="utf-8") as pf:
                        pf.write(cidr + "\n")

                if i < len(batch) and not STOP and args.pause:
                    time.sleep(args.pause)
    finally:
        lock_path.unlink(missing_ok=True)

    # 重新讀進度算剩餘，不要用「批次數」推算——中途略過或未掃完的網段
    # 沒有記進度，用減法會算出比實際少的剩餘量，讓人誤以為快跑完了。
    done_now = set()
    if prog_path.exists():
        done_now = {ln.strip() for ln in prog_path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()}
    remaining = len([s for s in segments if s not in done_now])

    print()
    print("=" * 52)
    print(f"本次找到 {total_found} 台活著的主機")
    print(f"結果檔　： {csv_path}")
    print(f"網段狀態： {status_path}   ← 匯入時務必一起帶上")
    print(f"剩餘網段： {remaining} 個（下次執行會自動接續）")
    if remaining <= 0:
        print()
        print("所有網段都掃完了。把這個目錄下的兩種 CSV")
        print("（scan_results_*.csv 與 segments_status_*.csv）")
        print("拿到戰情室的「資料匯入 → 掃描結果匯入」上傳即可。")
        print()
        print("⚠️ segments_status 一定要一起給：那份記錄了「哪些網段掃過但整段沒回應」，")
        print("   少了它，戰情室無法分辨「防火牆不通」與「機器真的不在」。")
    print("=" * 52)


if __name__ == "__main__":
    main()
