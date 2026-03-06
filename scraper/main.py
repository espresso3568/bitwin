#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三站標案抓取器 v1.5
===================

功能：
  - 抓取工研院、資策會、中研院標案資訊
  - 全來源保留近 3 天資料，自動去重
  - 資策會點入詳情頁抓取預算金額
  - 工研院根據 HAR 修正 Data 欄位解析
  - 每小時自動執行，失敗站點自動重試
  - 輸出 CSV（含歷史）和 JSON（給前端）
  - 記錄爬取狀態（status.json）供前端顯示

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
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
}

TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 3
KEEP_DAYS = 30

# 中研院回溯天數
SINICA_LOOKBACK_DAYS = 3

SOURCES = ['工研院', '資策會', '中研院']
STATUS_FILE = 'docs/status.json'


# ============================================================================
# 狀態管理
# ============================================================================

def load_status():
    """讀取今日的狀態檔"""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                status = json.load(f)
            if status.get('date') == TODAY_CN:
                return status
        except (json.JSONDecodeError, KeyError):
            pass

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
    time_str = datetime.now(TW_TZ).strftime('%H:%M:%S')
    status['logs'].append({'time': time_str, 'msg': msg})
    print(f"[LOG {time_str}] {msg}")


# ============================================================================
# 重試包裝器
# ============================================================================

def retry(func, retries=MAX_RETRIES, delay=RETRY_DELAY):
    """重試包裝器"""
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
            deleted += 1

    for f in glob.glob('docs/data_*.json'):
        match = re.search(r'data_(\d{8})\.json', os.path.basename(f))
        if match and match.group(1) < cutoff_str:
            os.remove(f)
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


# ============================================================================
# 工研院爬蟲
# ============================================================================

def scrape_itri():
    """抓取工研院標案（JSON API）- 根據 HAR 修正解析邏輯"""
    print("[GET] 抓取工研院...")

    base_url = "https://vendor.itri.org.tw/broadBqry2.aspx"
    api_url = "https://vendor.itri.org.tw/api/JsonRelayHandler.ashx"

    session = requests.Session()
    
    try:
        # 1. 先訪問首頁取得 Session Cookie
        session.get(base_url, headers=HEADERS, timeout=TIMEOUT)
        
        headers = {
            "User-Agent": HEADERS['User-Agent'],
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=UTF-8",
            "RemoteUrl": "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocList",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://vendor.itri.org.tw",
            "Referer": base_url,
        }

        payload = {
            "BidDocStatus": "P", # P = 公告中
            "IseBid": "",
            "currentPage": 1,
            "PageSize": 100
        }

        resp = session.post(api_url, headers=headers, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        
        raw_data = resp.json()
        bids = []
        
        # 資料封裝在 'Data' 字串欄位中
        bid_list = []
        if 'Data' in raw_data and isinstance(raw_data['Data'], str):
            try:
                bid_list = json.loads(raw_data['Data'])
            except:
                bid_list = []
        elif 'bddata' in raw_data:
            bid_list = raw_data['bddata']
        
        for row in bid_list:
            bid_info = row.get('BidInfo', row)
            case_no = bid_info.get('CNo', bid_info.get('cno', ''))
            
            # 日期處理
            end_date_str = ''
            end_date = row.get('EndDate', row.get('endDate', {}))
            if isinstance(end_date, dict):
                y = end_date.get('Year')
                m = str(end_date.get('Month', '')).zfill(2)
                d = str(end_date.get('Day', '')).zfill(2)
                if y and m and d: end_date_str = f"{y}/{m}/{d}"
            
            link_href = f"https://vendor.itri.org.tw/broadBdet.aspx?CNo={case_no}" if case_no else ''

            bids.append({
                '來源': '工研院',
                '案號': case_no,
                '標題': bid_info.get('CName', bid_info.get('cname', '')),
                '標題連結': link_href,
                '預算金額': str(bid_info.get('Budget', bid_info.get('BudgetAmount', ''))),
                '採購方式': bid_info.get('BidMethod', ''),
                '公告日': row.get('LatestPublishdt', row.get('latestPublishdt', '')),
                '截止日': end_date_str,
                '狀態': row.get('BidDocStatus', ''),
            })

        df = pd.DataFrame(bids)
        print(f"   [OK] 工研院：{len(df)} 筆")
        return df

    except Exception as e:
        print(f"   [FAIL] 工研院解析錯誤: {e}")
        return pd.DataFrame()


# ============================================================================
# 資策會爬蟲
# ============================================================================

def scrape_iii():
    """抓取資策會標案（HTML GridView 表格），並點入詳情頁抓取預算金額"""
    print("[GET] 抓取資策會...")

    url = 'https://bid.iii.org.tw/bid/list/bid_new_list.aspx'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        table = soup.find('table', id='GridView1')
        if not table:
            print("   [FAIL] 資策會：找不到表格")
            return pd.DataFrame()

        all_rows = table.find_all('tr')
        data_rows = []
        for row in all_rows[1:]:
            cols = row.find_all('td')
            if len(cols) >= 8:
                data_rows.append(row)
        
        # 為了效能與避免被封鎖，先取前 15 筆
        rows = data_rows[:15]

        bids = []
        for row in rows:
            cols = row.find_all('td')
            link = cols[2].find('a')
            link_href = link.get('href', '') if link else ''
            if link_href and not link_href.startswith('http'):
                link_href = f"https://bid.iii.org.tw/bid/list/{link_href.lstrip('/')}"

            case_no = cols[0].text.strip()
            title = cols[2].text.strip()
            
            # 點入詳情頁抓取預算金額
            budget = ''
            if link_href:
                try:
                    time.sleep(0.5)  # 禮貌性延遲
                    detail_resp = requests.get(link_href, headers=HEADERS, timeout=15)
                    if detail_resp.ok:
                        detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
                        full_text = detail_soup.get_text(separator=' ', strip=True)
                        # 搜尋模式：預算金額 [符號] [數字]
                        m = re.search(r'(?:預算金額|採購預算)\s*[:：\s]*\s*([\d,]+)', full_text)
                        if m:
                            budget = m.group(1).replace(',', '')
                        else:
                            # 備案：搜尋表格欄位
                            for cell in detail_soup.find_all(['td', 'th']):
                                if '預算金額' in cell.text:
                                    nxt = cell.find_next_sibling(['td', 'th'])
                                    if nxt:
                                        val = nxt.get_text().strip().replace(',', '')
                                        dig = re.search(r'(\d+)', val)
                                        if dig: budget = dig.group(1); break
                except Exception as e:
                    print(f"      [WARN] 資策會詳情頁抓取失敗 ({case_no}): {e}")

            bids.append({
                '來源': '資策會',
                '案號': case_no,
                '標題': title,
                '標題連結': link_href,
                '預算金額': budget,
                '採購類型': cols[3].text.strip(),
                '公佈日': cols[4].text.strip(),
                '投標日': cols[5].text.strip(),
                '開標日': cols[6].text.strip(),
                '狀態': cols[1].text.strip(),
            })

        df = pd.DataFrame(bids)
        print(f"   [OK] 資策會：{len(df)} 筆 (含預算金額)")
        return df

    except Exception as e:
        print(f"   [FAIL] 資策會：錯誤 - {e}")
        return pd.DataFrame()


# ============================================================================
# 中研院爬蟲（近 3 天）
# ============================================================================

def scrape_sinica():
    """抓取中研院標案（近 SINICA_LOOKBACK_DAYS 天）"""
    print(f"[GET] 抓取中研院（近 {SINICA_LOOKBACK_DAYS} 天）...")

    all_bids = []

    for i in range(SINICA_LOOKBACK_DAYS):
        date = (NOW_TW - timedelta(days=i)).strftime('%Y-%m-%d')
        url = f'https://srp.sinica.edu.tw/InviteBids?searchPubTime={date}'

        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            data_table = None
            for table in soup.find_all('table'):
                headers = table.find_all('th')
                if len(headers) >= 4:
                    header_text = ''.join(th.text for th in headers)
                    if '標' in header_text or '採購' in header_text:
                        data_table = table
                        break

            if not data_table: continue

            th_cells = data_table.find_all('th')
            col_names = [th.text.strip() for th in th_cells]

            rows = data_table.find_all('tr')[1:]
            bids = []
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 4: continue

                # 找超連結
                link_href = ''
                a_tag = row.find('a', href=True)
                if a_tag:
                    href = a_tag['href']
                    if href and not href.startswith('http'):
                        href = f"https://srp.sinica.edu.tw{href}"
                    link_href = href

                raw = {}
                for idx, col in enumerate(cols):
                    if idx < len(col_names): raw[col_names[idx]] = col.text.strip()

                bid = {'來源': '中研院', '標題連結': link_href, '查詢日期': date}
                for key, val in raw.items():
                    if '標號' in key or '案號' in key: bid['案號'] = val
                    elif '採購' in key and '名' in key: bid['標題'] = val
                    elif '預算' in key or '金額' in key:
                        try:
                            clean_val = val.replace(',', '').replace('$', '').strip()
                            bid['預算金額'] = float(clean_val) if clean_val else ''
                        except ValueError: bid['預算金額'] = val
                    elif '公告' in key or '公佈' in key: bid['公告日'] = val
                    elif '截止' in key or '截標' in key: bid['截止日'] = val

                bids.append(bid)

            if bids:
                bid_df = pd.DataFrame(bids)
                all_bids.append(bid_df)
                print(f"   [OK] 中研院 {date}：{len(bid_df)} 筆")

        except Exception as e:
            print(f"   [FAIL] 中研院 {date}：{e}")

    if all_bids:
        result = pd.concat(all_bids, ignore_index=True)
        result = result.fillna('')
        if '案號' in result.columns:
            result = result.drop_duplicates(subset=['案號'], keep='first')
        return result
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
    """爬取單一站點，記錄結果到 status。"""
    scraper = SCRAPER_MAP[source_name]
    time_str = datetime.now(TW_TZ).strftime('%H:%M:%S')

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
    主流程：
      1. 執行爬蟲抓取最新資料。
      2. 讀取現有的 latest.csv，與新抓取的資料合併。
      3. 根據 '案號' 去重（以新資料優先）。
      4. 根據 '公告日' 篩選，僅保留近 3 天的標案。
      5. 輸出更新後的 CSV 與 JSON。
    """
    print("\n" + "=" * 70)
    print("  三站標案抓取器 v1.5")
    print("=" * 70)
    print(f"  執行時間：{NOW_TW.strftime('%Y-%m-%d %H:%M:%S')} (台北時間)")
    print(f"  資料保留：近 3 天的所有標案")
    print("=" * 70 + "\n")

    os.makedirs('data', exist_ok=True)
    os.makedirs('docs', exist_ok=True)

    # 1. 讀取狀態與清理過期檔案
    status = load_status()
    attempt = status['attempt'] + 1
    status['attempt'] = attempt
    if attempt == 1:
        cleanup_old_files()

    # 2. 抓取新資料
    all_new_dfs = []
    for src in SOURCES:
        df = scrape_source(src, status)
        if not df.empty:
            all_new_dfs.append(df)

    if not all_new_dfs:
        print("\n[WARN] 警告：本次抓取無新資料，將使用現有資料進行維護。")
        new_bids = pd.DataFrame()
    else:
        new_bids = pd.concat(all_new_dfs, ignore_index=True)

    # 3. 讀取歷史資料 (latest.csv)
    latest_path = 'data/latest.csv'
    if os.path.exists(latest_path):
        try:
            old_bids = pd.read_csv(latest_path, dtype={'案號': str})
        except Exception:
            old_bids = pd.DataFrame()
    else:
        old_bids = pd.DataFrame()

    # 4. 合併與去重
    combined_bids = pd.concat([new_bids, old_bids], ignore_index=True)
    combined_bids = combined_bids.fillna('')

    if not combined_bids.empty:
        if '案號' in combined_bids.columns:
            combined_bids = combined_bids.drop_duplicates(subset=['案號'], keep='first')

        def parse_date(d):
            if not d: return None
            d = str(d).replace('/', '-')
            match_roc = re.match(r'^(\d{2,3})-(\d{2})-(\d{2})', d)
            if match_roc:
                y = int(match_roc.group(1)) + 1911
                return f"{y}-{match_roc.group(2)}-{match_roc.group(3)}"
            match_iso = re.match(r'^(\d{4})-(\d{2})-(\d{2})', d)
            if match_iso:
                return f"{match_iso.group(1)}-{match_iso.group(2)}-{match_iso.group(3)}"
            if len(d) == 8 and d.isdigit():
                return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            return None

        cutoff_date = (datetime.now(TW_TZ) - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        def is_recent(d):
            p = parse_date(d)
            if not p: return True
            try:
                dt = datetime.strptime(p, '%Y-%m-%d').replace(tzinfo=TW_TZ)
                return dt >= cutoff_date
            except: return True

        combined_bids['__is_recent'] = combined_bids['公告日'].apply(is_recent)
        final_bids = combined_bids[combined_bids['__is_recent']].drop(columns=['__is_recent'])
    else:
        final_bids = combined_bids

    print(f"\n[MERGE] 合併完成：總計 {len(final_bids)} 筆標案 (已去重並保留近 3 日)")

    # 6. 輸出結果
    final_bids.to_csv(latest_path, index=False, encoding='utf-8-sig')
    final_bids.to_csv(f'data/三站標案_{TODAY}.csv', index=False, encoding='utf-8-sig')

    json_data = {
        'update_time': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        'date': TODAY_CN,
        'total': len(final_bids),
        'sources': {
            source: len(final_bids[final_bids['來源'] == source])
            for source in final_bids['來源'].unique()
        },
        'data': final_bids.to_dict('records')
    }

    with open('docs/data.json', 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    
    with open(f'docs/data_{TODAY}.json', 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    generate_dates_manifest()
    save_status(status)

    print("\n" + "=" * 70)
    print(f"  [DONE] 執行完成！共更新 {len(final_bids)} 筆標案。")
    print("=" * 70 + "\n")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n\n[ERROR] 未預期錯誤：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
