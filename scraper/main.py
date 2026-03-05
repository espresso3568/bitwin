#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三站標案抓取器 v1.3
===================

功能：
  - 抓取工研院、資策會、中研院標案資訊
  - 輸出 CSV（含歷史）和 JSON（給前端）
  - 自動清理超過 30 天的歷史檔案
  - 記錄爬取狀態（status.json）供前端顯示
  - 智慧重試：只重新抓取失敗的站點，每日最多 5 次
  - 自動化執行（GitHub Actions）

作者：AI Assistant
日期：2026/03/05
授權：MIT
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from io import StringIO
import json
import os
import sys
import time
import re
import glob

# ============================================================================
# 全域設定
# ============================================================================

TW_TZ = ZoneInfo('Asia/Taipei')
NOW_TW = datetime.now(TW_TZ)
TODAY = NOW_TW.strftime('%Y%m%d')
TODAY_CN = NOW_TW.strftime('%Y-%m-%d')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 3
KEEP_DAYS = 30
MAX_DAILY_ATTEMPTS = 5

# 站點定義
SOURCES = ['工研院', '資策會', '中研院']

STATUS_FILE = 'docs/status.json'


# ============================================================================
# 狀態管理
# ============================================================================

def load_status():
    """
    讀取今日的狀態檔。
    回傳格式：
    {
        "date": "2026-03-05",
        "attempt": 2,
        "sources": {
            "工研院": {"status": "ok", "count": 50, "time": "08:01:23", "error": ""},
            "資策會": {"status": "ok", "count": 10, "time": "08:01:25", "error": ""},
            "中研院": {"status": "fail", "count": 0, "time": "08:01:30", "error": "連線超時"}
        },
        "logs": [
            {"time": "08:00:05", "msg": "開始第 1 次執行"},
            ...
        ]
    }
    """
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                status = json.load(f)
            if status.get('date') == TODAY_CN:
                return status
        except (json.JSONDecodeError, KeyError):
            pass

    # 今天第一次執行，初始化
    return {
        'date': TODAY_CN,
        'attempt': 0,
        'sources': {
            src: {'status': 'pending', 'count': 0, 'time': '', 'error': ''}
            for src in SOURCES
        },
        'logs': []
    }


def save_status(status):
    """寫入狀態檔"""
    os.makedirs('docs', exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def add_log(status, msg):
    """加入一筆 log"""
    time_str = NOW_TW.strftime('%H:%M:%S')
    status['logs'].append({'time': time_str, 'msg': msg})
    print(f"[LOG {time_str}] {msg}")


# ============================================================================
# 重試包裝器
# ============================================================================

def retry(func, retries=MAX_RETRIES, delay=RETRY_DELAY):
    """重試包裝器：失敗時自動重試指定次數。"""
    for attempt in range(retries + 1):
        result = func()
        if not result.empty:
            return result
        if attempt < retries:
            print(f"   [RETRY] 第 {attempt + 1} 次重試（等待 {delay} 秒）...")
            time.sleep(delay)
    return pd.DataFrame()


# ============================================================================
# 自動清理
# ============================================================================

def cleanup_old_files():
    """刪除超過 KEEP_DAYS 天的歷史檔案"""
    print(f"\n[CLEAN] 清理 {KEEP_DAYS} 天前的歷史檔案...")

    cutoff = NOW_TW - timedelta(days=KEEP_DAYS)
    cutoff_str = cutoff.strftime('%Y%m%d')
    deleted = 0

    for f in glob.glob('data/三站標案_*.csv'):
        match = re.search(r'(\d{8})', os.path.basename(f))
        if match and match.group(1) < cutoff_str:
            os.remove(f)
            print(f"   [DEL] {f}")
            deleted += 1

    for f in glob.glob('docs/data_*.json'):
        match = re.search(r'data_(\d{8})\.json', os.path.basename(f))
        if match and match.group(1) < cutoff_str:
            os.remove(f)
            print(f"   [DEL] {f}")
            deleted += 1

    print(f"   [OK] 已刪除 {deleted} 個檔案" if deleted else "   [OK] 無需清理")


# ============================================================================
# 日期清單產生器
# ============================================================================

def generate_dates_manifest():
    """掃描 docs/data_*.json，產生 docs/dates.json"""
    dates = set()

    for f in glob.glob('docs/data_*.json'):
        match = re.search(r'data_(\d{8})\.json', os.path.basename(f))
        if match:
            d = match.group(1)
            dates.add(f"{d[:4]}-{d[4:6]}-{d[6:8]}")

    dates.add(TODAY_CN)
    sorted_dates = sorted(dates, reverse=True)

    manifest = {'dates': sorted_dates}
    with open('docs/dates.json', 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"   [OK] 日期清單：{len(sorted_dates)} 個日期")


# ============================================================================
# 工研院爬蟲
# ============================================================================

def scrape_itri():
    """抓取工研院標案（JSON API）"""
    print("[GET] 抓取工研院...")

    url = "https://vendor.itri.org.tw/api/JsonRelayHandler.ashx"

    headers = HEADERS.copy()
    headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "RemoteUrl": "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocList",
        "Origin": "https://vendor.itri.org.tw",
        "Referer": "https://vendor.itri.org.tw/broadBqry2.aspx",
    })

    payload = {
        "BidDocStatus": "",
        "IseBid": "",
        "currentPage": 1,
        "PageSize": 100
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        bids = []
        for row in data.get('bddata', []):
            bid_info = row.get('BidInfo', {})
            end_date = row.get('EndDate', {})

            end_date_str = ''
            if isinstance(end_date, dict):
                year = end_date.get('Year', '')
                month = str(end_date.get('Month', '')).zfill(2)
                day = str(end_date.get('Day', '')).zfill(2)
                if year and month and day:
                    end_date_str = f"{year}/{month}/{day}"

            undertaker = bid_info.get('Undertaker')
            if isinstance(undertaker, dict):
                undertaker_name = undertaker.get('name', '')
            elif undertaker is not None:
                undertaker_name = str(undertaker)
            else:
                undertaker_name = ''

            bids.append({
                '來源': '工研院',
                '案號': bid_info.get('CNo', ''),
                '標題': bid_info.get('CName', ''),
                '採購方式': bid_info.get('BidMethod', ''),
                '是否電子標': bid_info.get('IseBid', ''),
                '公告日': row.get('LatestPublishdt', ''),
                '截止日': end_date_str,
                '狀態': row.get('BidDocStatus', ''),
                '承辦人': undertaker_name,
            })

        df = pd.DataFrame(bids)
        print(f"   [OK] 工研院：{len(df)} 筆")
        return df

    except requests.exceptions.Timeout:
        print(f"   [FAIL] 工研院：連線超時（>{TIMEOUT}秒）")
        return pd.DataFrame()
    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] 工研院：網路錯誤 - {e}")
        return pd.DataFrame()
    except (KeyError, ValueError) as e:
        print(f"   [FAIL] 工研院：資料解析錯誤 - {e}")
        return pd.DataFrame()


# ============================================================================
# 資策會爬蟲
# ============================================================================

def scrape_iii():
    """抓取資策會標案（HTML GridView 表格）"""
    print("[GET] 抓取資策會...")

    url = 'https://bid.iii.org.tw/bid/list/bid_new_list.aspx'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        table = soup.find('table', id='GridView1')
        if not table:
            print("   [FAIL] 資策會：找不到表格（可能網頁結構改變）")
            return pd.DataFrame()

        all_rows = table.find_all('tr')
        data_rows = []
        for row in all_rows[1:]:
            cols = row.find_all('td')
            if len(cols) >= 8:
                data_rows.append(row)
        rows = data_rows[:10]

        bids = []
        for row in rows:
            cols = row.find_all('td')
            link = cols[2].find('a')
            link_href = link.get('href', '') if link else ''
            if link_href and not link_href.startswith('http'):
                link_href = f"https://bid.iii.org.tw/bid/list/{link_href.lstrip('/')}"

            bids.append({
                '來源': '資策會',
                '案號': cols[0].text.strip(),
                '招標狀態': cols[1].text.strip(),
                '標題': cols[2].text.strip(),
                '標題連結': link_href,
                '採購類型': cols[3].text.strip(),
                '公佈日': cols[4].text.strip(),
                '投標日': cols[5].text.strip(),
                '開標日': cols[6].text.strip(),
                '更正日期': cols[7].text.strip(),
            })

        df = pd.DataFrame(bids)
        print(f"   [OK] 資策會：{len(df)} 筆")
        return df

    except requests.exceptions.Timeout:
        print(f"   [FAIL] 資策會：連線超時（>{TIMEOUT}秒）")
        return pd.DataFrame()
    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] 資策會：網路錯誤 - {e}")
        return pd.DataFrame()
    except Exception as e:
        print(f"   [FAIL] 資策會：解析錯誤 - {e}")
        return pd.DataFrame()


# ============================================================================
# 中研院爬蟲
# ============================================================================

def scrape_sinica():
    """抓取中研院標案（HTML 表格，pandas 自動解析）"""
    print("[GET] 抓取中研院...")

    url = f'https://srp.sinica.edu.tw/InviteBids?searchPubTime={TODAY_CN}'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()

        dfs = pd.read_html(StringIO(resp.text))

        if dfs and len(dfs) > 0:
            df = dfs[0]
            df['來源'] = '中研院'
            df['查詢日期'] = TODAY_CN
            print(f"   [OK] 中研院：{len(df)} 筆")
            return df
        else:
            print("   [FAIL] 中研院：無資料（當天無新公告）")
            return pd.DataFrame()

    except requests.exceptions.Timeout:
        print(f"   [FAIL] 中研院：連線超時（>{TIMEOUT}秒）")
        return pd.DataFrame()
    except requests.exceptions.RequestException as e:
        print(f"   [FAIL] 中研院：網路錯誤 - {e}")
        return pd.DataFrame()
    except ValueError as e:
        print(f"   [FAIL] 中研院：表格解析錯誤 - {e}")
        return pd.DataFrame()


# ============================================================================
# 爬取單一站點（含狀態記錄）
# ============================================================================

SCRAPER_MAP = {
    '工研院': scrape_itri,
    '資策會': scrape_iii,
    '中研院': scrape_sinica,
}


def scrape_source(source_name, status):
    """
    爬取單一站點，記錄結果到 status。
    回傳 DataFrame（可能為空）。
    """
    scraper = SCRAPER_MAP[source_name]
    time_str = NOW_TW.strftime('%H:%M:%S')

    try:
        df = retry(scraper)
        if not df.empty:
            status['sources'][source_name] = {
                'status': 'ok',
                'count': len(df),
                'time': time_str,
                'error': ''
            }
            add_log(status, f"{source_name}：成功，{len(df)} 筆")
            return df
        else:
            status['sources'][source_name] = {
                'status': 'fail',
                'count': 0,
                'time': time_str,
                'error': '回傳 0 筆資料（網站可能異常或無公告）'
            }
            add_log(status, f"{source_name}：失敗，0 筆")
            return pd.DataFrame()

    except Exception as e:
        error_msg = str(e)[:200]
        status['sources'][source_name] = {
            'status': 'error',
            'count': 0,
            'time': time_str,
            'error': error_msg
        }
        add_log(status, f"{source_name}：錯誤 - {error_msg}")
        return pd.DataFrame()


# ============================================================================
# 主程式
# ============================================================================

def main():
    """
    主流程（含智慧重試）：
      1. 讀取今日狀態，判斷是否需要執行
      2. 只抓取失敗/pending 的站點
      3. 合併所有資料（含先前成功的 + 本次新抓的）
      4. 輸出 CSV / JSON / 狀態檔
    """
    print("\n" + "=" * 70)
    print("  三站標案抓取器 v1.3")
    print("=" * 70)
    print(f"  執行時間：{NOW_TW.strftime('%Y-%m-%d %H:%M:%S')} (台北時間)")
    print(f"  保留天數：{KEEP_DAYS} 天")
    print("=" * 70 + "\n")

    os.makedirs('data', exist_ok=True)
    os.makedirs('docs', exist_ok=True)

    # ========================================================================
    # 步驟 1：讀取狀態，檢查是否需要執行
    # ========================================================================
    status = load_status()
    attempt = status['attempt'] + 1

    # 判斷哪些站需要重抓
    need_scrape = []
    for src in SOURCES:
        src_status = status['sources'][src]['status']
        if src_status != 'ok':
            need_scrape.append(src)

    if not need_scrape:
        print("[SKIP] 今日所有站點皆已成功，無需重新抓取")
        add_log(status, f"第 {attempt} 次執行：跳過，全部已成功")
        status['attempt'] = attempt
        save_status(status)
        return

    if attempt > MAX_DAILY_ATTEMPTS:
        print(f"[SKIP] 今日已執行 {attempt - 1} 次，超過上限 {MAX_DAILY_ATTEMPTS} 次")
        add_log(status, f"第 {attempt} 次執行：跳過，超過每日上限")
        save_status(status)
        return

    status['attempt'] = attempt
    add_log(status, f"第 {attempt} 次執行，待抓取：{', '.join(need_scrape)}")

    # ========================================================================
    # 步驟 2：清理過期檔案（只在第一次執行時）
    # ========================================================================
    if attempt == 1:
        cleanup_old_files()

    # ========================================================================
    # 步驟 3：抓取需要的站點
    # ========================================================================
    new_dfs = {}
    for src in need_scrape:
        df = scrape_source(src, status)
        if not df.empty:
            new_dfs[src] = df

    # ========================================================================
    # 步驟 4：合併資料（先前成功的 + 本次新抓的）
    # ========================================================================
    all_dfs = []

    # 載入先前已成功的站點資料（從現有的 data.json）
    existing_data_file = 'docs/data.json'
    if os.path.exists(existing_data_file):
        try:
            with open(existing_data_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            if existing.get('date') == TODAY_CN:
                existing_df = pd.DataFrame(existing.get('data', []))
                if not existing_df.empty:
                    for src in SOURCES:
                        if src not in need_scrape:
                            # 這個站先前已成功，保留舊資料
                            src_df = existing_df[existing_df['來源'] == src]
                            if not src_df.empty:
                                all_dfs.append(src_df)
        except (json.JSONDecodeError, KeyError):
            pass

    # 加入本次新抓取的
    for src, df in new_dfs.items():
        all_dfs.append(df)

    if not all_dfs:
        print("\n[WARN] 警告：無任何可用資料")
        save_status(status)
        # 不 exit(1)，讓 Actions 可以正常提交 status.json
        return

    print("\n[MERGE] 合併資料...")
    all_bids = pd.concat(all_dfs, ignore_index=True)
    all_bids = all_bids.fillna('')  # NaN → 空字串（避免 JSON 輸出 NaN）
    print(f"   [OK] 總計：{len(all_bids)} 筆")

    print("\n[STAT] 各站明細：")
    for source in all_bids['來源'].unique():
        count = len(all_bids[all_bids['來源'] == source])
        print(f"   - {source}：{count} 筆")

    # ========================================================================
    # 步驟 5：輸出 CSV
    # ========================================================================
    print("\n[SAVE] 存檔中...")

    csv_history = f'data/三站標案_{TODAY}.csv'
    all_bids.to_csv(csv_history, index=False, encoding='utf-8-sig')
    print(f"   [OK] CSV（歷史）: {csv_history}")

    csv_latest = 'data/latest.csv'
    all_bids.to_csv(csv_latest, index=False, encoding='utf-8-sig')
    print(f"   [OK] CSV（最新）: {csv_latest}")

    # ========================================================================
    # 步驟 6：輸出 JSON
    # ========================================================================
    json_data = {
        'update_time': NOW_TW.strftime('%Y-%m-%d %H:%M:%S'),
        'date': TODAY_CN,
        'total': len(all_bids),
        'sources': {
            source: len(all_bids[all_bids['來源'] == source])
            for source in all_bids['來源'].unique()
        },
        'data': all_bids.to_dict('records')
    }

    with open('docs/data.json', 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"   [OK] JSON（最新）: docs/data.json")

    daily_json = f'docs/data_{TODAY}.json'
    with open(daily_json, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"   [OK] JSON（每日）: {daily_json}")

    generate_dates_manifest()

    # ========================================================================
    # 步驟 7：儲存狀態
    # ========================================================================
    save_status(status)

    # 統計結果
    ok_count = sum(1 for s in status['sources'].values() if s['status'] == 'ok')
    fail_count = len(SOURCES) - ok_count

    print("\n" + "=" * 70)
    if fail_count == 0:
        print(f"  [DONE] 全部成功！（第 {attempt} 次執行）")
    else:
        failed = [s for s in SOURCES if status['sources'][s]['status'] != 'ok']
        print(f"  [DONE] 部分完成（{ok_count}/{len(SOURCES)}）")
        print(f"  [WARN] 失敗站點：{', '.join(failed)}（將於 1 小時後重試）")
    print("=" * 70 + "\n")


# ============================================================================
# 程式進入點
# ============================================================================

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[ABORT] 使用者中斷執行")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[ERROR] 未預期錯誤：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
