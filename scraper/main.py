#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三站標案抓取器 v1.6
===================
修正工研院三層巢狀 JSON 解析邏輯。
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
SINICA_LOOKBACK_DAYS = 3

SOURCES = ['工研院', '資策會', '中研院']
STATUS_FILE = 'docs/status.json'

# ============================================================================
# 日期處理工具
# ============================================================================

def parse_date_to_iso(d):
    """將各種格式日期轉為 ISO (YYYY-MM-DD) 以便邏輯運算"""
    if not d: return None
    d = str(d).strip().replace('/', '-')
    # ITRI 8碼格式 20260306
    if len(d) == 8 and d.isdigit(): return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    # 民國格式 115-03-06
    match_roc = re.match(r'^(\d{2,3})-(\d{2})-(\d{2})', d)
    if match_roc:
        y = int(match_roc.group(1)) + 1911
        return f"{y}-{match_roc.group(2)}-{match_roc.group(3)}"
    # 西元格式 2026-03-06
    match_iso = re.match(r'^(\d{4})-(\d{2})-(\d{2})', d)
    if match_iso:
        return f"{match_iso.group(1)}-{match_iso.group(2)}-{match_iso.group(3)}"
    return None

def format_to_roc(d):
    """將日期統一轉換為民國格式 YYY/MM/DD (例如 115/03/06)"""
    iso = parse_date_to_iso(d)
    if not iso: return str(d) if d else ""
    try:
        y, m, d = iso.split('-')
        return f"{int(y)-1911}/{m}/{d}"
    except:
        return str(d)

# ============================================================================
# 狀態管理與輔助函式
# ============================================================================

def load_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                status = json.load(f)
            if status.get('date') == TODAY_CN: return status
        except: pass
    return {
        'date': TODAY_CN, 'attempt': 0,
        'sources': {src: {'status': 'pending', 'count': 0, 'time': '', 'error': ''} for src in SOURCES},
        'logs': []
    }

def save_status(status):
    os.makedirs('docs', exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

def add_log(status, msg):
    time_str = datetime.now(TW_TZ).strftime('%H:%M:%S')
    status['logs'].append({'time': time_str, 'msg': msg})
    print(f"[LOG {time_str}] {msg}")

def retry(func, retries=MAX_RETRIES, delay=RETRY_DELAY):
    for attempt in range(retries + 1):
        result = func()
        if not result.empty: return result
        if attempt < retries:
            print(f"   [RETRY] 第 {attempt + 1} 次重試...")
            time.sleep(delay)
    return pd.DataFrame()

def cleanup_old_files():
    cutoff = NOW_TW - timedelta(days=KEEP_DAYS)
    cutoff_str = cutoff.strftime('%Y%m%d')
    for f in glob.glob('data/三站標案_*.csv') + glob.glob('docs/data_*.json'):
        m = re.search(r'(\d{8})', os.path.basename(f))
        if m and m.group(1) < cutoff_str: os.remove(f)

def generate_dates_manifest():
    dates = {TODAY_CN}
    for f in glob.glob('docs/data_*.json'):
        m = re.search(r'data_(\d{8})\.json', os.path.basename(f))
        if m: d = m.group(1); dates.add(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
    manifest = {'dates': sorted(list(dates), reverse=True)}
    with open('docs/dates.json', 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

# ============================================================================
# 工研院爬蟲 (v1.6 修正版)
# ============================================================================

def scrape_itri():
    print("[GET] 抓取工研院...")
    api_url = "https://vendor.itri.org.tw/api/JsonRelayHandler.ashx"
    
    headers = {
        "User-Agent": HEADERS['User-Agent'],
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "RemoteUrl": "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocList",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://vendor.itri.org.tw",
        "Referer": "https://vendor.itri.org.tw/broadBqry2.aspx",
    }

    payload = {"BidDocStatus": "", "IseBid": "", "currentPage": 1, "PageSize": 100}

    try:
        session = requests.Session()
        session.get("https://vendor.itri.org.tw/broadBqry2.aspx", timeout=10)
        resp = session.post(api_url, headers=headers, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        
        raw_json = resp.json()
        bids = []
        
        # 層層解析
        # 1. 取得 Data 字串並轉為物件
        if 'Data' not in raw_json: return pd.DataFrame()
        data_obj = json.loads(raw_json['Data'])
        
        # 2. 取得內層 Data 列表
        items = data_obj.get('Data', [])
        
        for item in items:
            # 3. 解析每項中的 bddata 字串
            if 'bddata' not in item: continue
            info = json.loads(item['bddata'])
            
            # 提取核心欄位
            bid_info = info.get('BidInfo', {})
            case_no = bid_info.get('CNo', '')
            seq = info.get('BidDocseqno', '')
            title = bid_info.get('CName', '')
            budget = str(bid_info.get('Budget', bid_info.get('BudgetAmount', '')))
            
            # 日期處理
            pub_date = info.get('LatestPublishdt', '')
            end_date_obj = info.get('EndDate', {})
            end_date = ""
            if isinstance(end_date_obj, dict):
                y, m, d = end_date_obj.get('Year'), end_date_obj.get('Month'), end_date_obj.get('Day')
                if y and m and d: end_date = f"{y}/{str(m).zfill(2)}/{str(d).zfill(2)}"

            bids.append({
                '來源': '工研院',
                '案號': case_no,
                '標題': title,
                '標題連結': f"https://vendor.itri.org.tw/broadBdetail2.aspx?seq={seq}" if seq else '',
                '預算金額': budget,
                '採購方式': bid_info.get('BidMethod', ''),
                '公告日': pub_date,
                '截止日': end_date,
                '狀態': info.get('BidDocStatus', ''),
            })

        df = pd.DataFrame(bids)
        print(f"   [OK] 工研院：{len(df)} 筆")
        return df
    except Exception as e:
        print(f"   [FAIL] 工研院錯誤: {e}")
        return pd.DataFrame()

# ============================================================================
# 資策會與中研院爬蟲 (保持穩定邏輯)
# ============================================================================

def scrape_iii():
    print("[GET] 抓取資策會...")
    url = 'https://bid.iii.org.tw/bid/list/bid_new_list.aspx'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', id='GridView1')
        if not table: return pd.DataFrame()
        
        rows = table.find_all('tr')[1:16] # 前 15 筆
        bids = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 8: continue
            link = cols[2].find('a')
            href = link.get('href', '') if link else ''
            if href and not href.startswith('http'): href = f"https://bid.iii.org.tw/bid/list/{href.lstrip('/')}"
            
            case_no, title = cols[0].text.strip(), cols[2].text.strip()
            budget = ''
            if href:
                try:
                    time.sleep(0.5)
                    d_resp = requests.get(href, headers=HEADERS, timeout=15)
                    if d_resp.ok:
                        txt = BeautifulSoup(d_resp.text, 'html.parser').get_text(separator=' ', strip=True)
                        m = re.search(r'(?:預算金額|採購預算)\s*[:：\s]*\s*([\d,]+)', txt)
                        if m: budget = m.group(1).replace(',', '')
                except: pass

            bids.append({
                '來源': '資策會', '案號': case_no, '標題': title, '標題連結': href,
                '預算金額': budget, '採購類型': cols[3].text.strip(), '公告日': cols[4].text.strip(),
                '截止日': cols[5].text.strip(), '狀態': cols[1].text.strip(),
            })
        return pd.DataFrame(bids)
    except: return pd.DataFrame()

def scrape_sinica():
    print(f"[GET] 抓取中研院...")
    all_dfs = []
    for i in range(SINICA_LOOKBACK_DAYS):
        date = (NOW_TW - timedelta(days=i)).strftime('%Y-%m-%d')
        try:
            r = requests.get(f'https://srp.sinica.edu.tw/InviteBids?searchPubTime={date}', headers=HEADERS, timeout=TIMEOUT)
            soup = BeautifulSoup(r.text, 'html.parser')
            table = None
            for t in soup.find_all('table'):
                if '標' in t.get_text() or '採購' in t.get_text(): table = t; break
            if not table: continue
            
            headers = [th.text.strip() for th in table.find_all('th')]
            rows = table.find_all('tr')[1:]
            bids = []
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 4: continue
                link = row.find('a', href=True)
                href = link['href'] if link else ''
                if href and not href.startswith('http'): href = f"https://srp.sinica.edu.tw{href}"
                
                raw = {headers[idx]: col.text.strip() for idx, col in enumerate(cols) if idx < len(headers)}
                bid = {'來源': '中研院', '標題連結': href, '公告日': date}
                for k, v in raw.items():
                    if '案號' in k or '標號' in k: bid['案號'] = v
                    elif '名稱' in k: bid['標題'] = v
                    elif '預算' in k: 
                        m = re.search(r'([\d,]+)', v.replace('$', ''))
                        bid['預算金額'] = m.group(1).replace(',', '') if m else v
                    elif '截止' in k: bid['截止日'] = v
                bids.append(bid)
            if bids: all_dfs.append(pd.DataFrame(bids))
        except: continue
    if not all_dfs: return pd.DataFrame()
    res = pd.concat(all_dfs).drop_duplicates(subset=['案號'], keep='first')
    return res

# ============================================================================
# 主流程
# ============================================================================

def main():
    print(f"\n{'='*70}\n  三站標案抓取器 v1.6\n{'='*70}")
    status = load_status()
    status['attempt'] += 1
    if status['attempt'] == 1: cleanup_old_files()

    all_data = []
    for src in SOURCES:
        df = retry(lambda s=src: {'工研院': scrape_itri, '資策會': scrape_iii, '中研院': scrape_sinica}[s]())
        if not df.empty:
            all_data.append(df)
            status['sources'][src] = {'status': 'ok', 'count': len(df), 'time': datetime.now(TW_TZ).strftime('%H:%M:%S'), 'error': ''}
            add_log(status, f"{src}：成功，{len(df)} 筆")
        else:
            status['sources'][src]['status'] = 'fail'
            add_log(status, f"{src}：無資料或失敗")

    # 合併與去重
    new_df = pd.concat(all_data) if all_data else pd.DataFrame()
    latest_path = 'data/latest.csv'
    old_df = pd.read_csv(latest_path, dtype={'案號': str}) if os.path.exists(latest_path) else pd.DataFrame()
    
    final_df = pd.concat([new_df, old_df]).drop_duplicates(subset=['案號'], keep='first').fillna('')
    
    # 篩選近 3 日
    def is_recent(d):
        try:
            d = str(d).replace('/', '-')
            if len(d) == 8 and d.isdigit(): d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            if re.match(r'^\d{2,3}-', d): 
                pts = d.split('-'); d = f"{int(pts[0])+1911}-{pts[1]}-{pts[2]}"
            dt = datetime.strptime(d[:10], '%Y-%m-%d').replace(tzinfo=TW_TZ)
            return dt >= (datetime.now(TW_TZ) - timedelta(days=2)).replace(hour=0, minute=0, second=0)
        except: return True

    if not final_df.empty:
        # 篩選近 3 日 (此時還是 ISO 格式或原始格式)
        final_df = final_df[final_df['公告日'].apply(is_recent)]
        
        # 存檔前：格式化日期為民國格式 (YYY/MM/DD)
        final_df['公告日'] = final_df['公告日'].apply(format_to_roc)
        final_df['截止日'] = final_df['截止日'].apply(format_to_roc)

    # 存檔
    final_df.to_csv(latest_path, index=False, encoding='utf-8-sig')
    final_df.to_csv(f'data/三站標案_{TODAY}.csv', index=False, encoding='utf-8-sig')
    
    res_json = {
        'update_time': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        'date': TODAY_CN, 'total': len(final_df),
        'sources': {s: len(final_df[final_df['來源']==s]) for s in final_df['來源'].unique()},
        'data': final_df.to_dict('records')
    }
    with open('docs/data.json', 'w', encoding='utf-8') as f: json.dump(res_json, f, ensure_ascii=False, indent=2)
    with open(f'docs/data_{TODAY}.json', 'w', encoding='utf-8') as f: json.dump(res_json, f, ensure_ascii=False, indent=2)

    generate_dates_manifest()
    save_status(status)
    print(f"\n[DONE] 執行完成，共 {len(final_df)} 筆。")

if __name__ == '__main__':
    main()
