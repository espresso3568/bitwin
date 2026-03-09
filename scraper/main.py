#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三站標案抓取器 v2.9
===================
1. 資策會：強化從詳情頁提取「預算金額」與「截止日」(支援至民國115年...格式)。
2. 工研院：穩定解析 EndDate 物件。
3. 全系統：統一民國日期格式 YYY/MM/DD。
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
}

TIMEOUT = 30
KEEP_DAYS = 30
SINICA_LOOKBACK_DAYS = 3

SOURCES = ['工研院', '資策會', '中研院']
STATUS_FILE = 'docs/status.json'

# ============================================================================
# 日期處理工具
# ============================================================================

def format_to_roc(d):
    if not d or d == '-' or str(d).strip() == '': return "-"
    d_str = str(d).strip().replace('/', '-')
    
    # 移除「民國」前綴以便解析
    d_str = d_str.replace('民國', '').replace('年', '-').replace('月', '-').replace('日', '')
    
    # 處理 20260306
    if len(d_str) == 8 and d_str.isdigit():
        return f"{int(d_str[:4])-1911}/{d_str[4:6]}/{d_str[6:8]}"
    
    # 處理 115-03-06 或 2026-03-06
    match = re.match(r'^(\d{2,4})-(\d{1,2})-(\d{1,2})', d_str)
    if match:
        y, m, day = int(match.group(1)), match.group(2).zfill(2), match.group(3).zfill(2)
        if y > 1900: y -= 1911
        return f"{y}/{m}/{day}"
    
    return str(d)

# ============================================================================
# 狀態管理
# ============================================================================

def load_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                status = json.load(f)
            if status.get('date') == TODAY_CN: return status
        except: pass
    return {'date': TODAY_CN, 'attempt': 0, 'sources': {src: {'status': 'pending', 'count': 0, 'time': '', 'error': ''} for src in SOURCES}, 'logs': []}

def save_status(status):
    os.makedirs('docs', exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f: json.dump(status, f, ensure_ascii=False, indent=2)

def add_log(status, msg):
    time_str = datetime.now(TW_TZ).strftime('%H:%M:%S')
    status['logs'].append({'time': time_str, 'msg': msg})
    print(f"[LOG {time_str}] {msg}")

# ============================================================================
# 資策會爬蟲 (v2.9 強化版)
# ============================================================================

def scrape_iii():
    print("[GET] 抓取資策會...")
    url = 'https://bid.iii.org.tw/bid/list/bid_new_list.aspx'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table', id='GridView1')
        if not table: return pd.DataFrame()
        
        rows = table.find_all('tr')[1:21] # 前 20 筆
        bids = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 4: continue
            
            case_no = cols[0].text.strip()
            title = cols[2].text.strip()
            link_tag = cols[2].find('a')
            href_attr = link_tag.get('href', '') if link_tag else ''
            detail_url = f"https://bid.iii.org.tw/bid/list/{href_attr.lstrip('/')}" if 'bid_no=' in href_attr else f"https://bid.iii.org.tw/bid/list/bid_new_list.aspx?bid_no={case_no}"
            
            # 列表頁日期嘗試
            found_dates = []
            for td in cols:
                m = re.search(r'(\d{2,4})/\d{1,2}/\d{1,2}', td.text.strip())
                if m: found_dates.append(td.text.strip())
            pub_date = found_dates[0] if len(found_dates) > 0 else "-"
            end_date = found_dates[1] if len(found_dates) > 1 else "-"
            
            # 進入詳情頁補齊資料
            budget = ''
            try:
                time.sleep(0.5)
                d_resp = requests.get(detail_url, headers=HEADERS, timeout=15)
                d_resp.encoding = 'utf-8'
                detail_soup = BeautifulSoup(d_resp.text, 'html.parser')
                clean_text = detail_soup.get_text(separator=' ', strip=True)
                
                # 1. 抓取預算
                m_budget = re.search(r'預算金額.*?([\d,]{4,12})', clean_text)
                if m_budget: budget = m_budget.group(1).replace(',', '')
                
                # 2. 補齊截止日 (處理：至民國115年3月9日)
                if end_date == "-":
                    m_end = re.search(r'期限.*?至\s*(?:民國)?\s*(\d{2,3})[年/](\d{1,2})[月/](\d{1,2})', clean_text)
                    if m_end: end_date = f"{m_end.group(1)}/{m_end.group(2).zfill(2)}/{m_end.group(3).zfill(2)}"
                
                # 3. 補齊公告日
                if pub_date == "-":
                    m_pub = re.search(r'公告日期.*?(?:民國)?\s*(\d{2,3})[年/](\d{1,2})[月/](\d{1,2})', clean_text)
                    if m_pub: pub_date = f"{m_pub.group(1)}/{m_pub.group(2).zfill(2)}/{m_pub.group(3).zfill(2)}"
            except: pass

            bids.append({
                '來源': '資策會', '案號': case_no, '標題': title, '標題連結': detail_url,
                '預算金額': budget, '公告日': pub_date, '截止日': end_date, '狀態': cols[1].text.strip(),
            })
        return pd.DataFrame(bids)
    except Exception as e:
        print(f"   [FAIL] 資策會錯誤: {e}"); raise e

# ============================================================================
# 工研院爬蟲
# ============================================================================

def scrape_itri():
    print("[GET] 抓取工研院...")
    relay_url = "https://vendor.itri.org.tw/api/JsonRelayHandler.ashx"
    headers = {"Content-Type": "application/json; charset=UTF-8", "RemoteUrl": "https://abpssapi.itri.org.tw/api/ABPSSAPI/GetpublishDocList", "X-Requested-With": "XMLHttpRequest", "Referer": "https://vendor.itri.org.tw/broadBqry2.aspx"}
    payload = {"BidDocStatus": "", "IseBid": "", "currentPage": 1, "PageSize": 100}
    try:
        session = requests.Session(); session.get("https://vendor.itri.org.tw/broadBqry2.aspx", timeout=10)
        resp = session.post(relay_url, headers=headers, json=payload, timeout=TIMEOUT)
        data_obj = json.loads(resp.json()['Data'])
        bids = []
        for item in data_obj.get('Data', []):
            info = json.loads(item['bddata'])
            bid_info = info.get('BidInfo', {})
            case_no, seq = bid_info.get('CNo', ''), info.get('BidDocseqno', '')
            end_date = "-"
            ed = bid_info.get('EndDate', {})
            if isinstance(ed, dict) and ed.get('Year'):
                y = int(ed['Year'])
                if y > 1900: y -= 1911
                end_date = f"{y}/{str(ed['Month']).zfill(2)}/{str(ed['Day']).zfill(2)}"
            bids.append({
                '來源': '工研院', '案號': case_no, '標題': bid_info.get('CName', ''),
                '標題連結': f"https://vendor.itri.org.tw/broadBdetail2.aspx?seq={seq}",
                '預算金額': str(bid_info.get('Budget', bid_info.get('BudgetAmount', ''))),
                '公告日': info.get('LatestPublishdt', ''), '截止日': end_date, '狀態': info.get('BidDocStatus', ''),
            })
        return pd.DataFrame(bids)
    except Exception as e: print(f"   [FAIL] 工研院錯誤: {e}"); raise e

# ============================================================================
# 中研院爬蟲
# ============================================================================

def scrape_sinica():
    print(f"[GET] 抓取中研院...")
    all_dfs = []
    for i in range(SINICA_LOOKBACK_DAYS):
        date = (NOW_TW - timedelta(days=i)).strftime('%Y-%m-%d')
        try:
            r = requests.get(f'https://srp.sinica.edu.tw/InviteBids?searchPubTime={date}', headers=HEADERS, timeout=TIMEOUT)
            soup = BeautifulSoup(r.text, 'html.parser'); table = None
            for t in soup.find_all('table'):
                if '標' in t.get_text() or '採購' in t.get_text(): table = t; break
            if not table: continue
            headers = [th.text.strip() for th in table.find_all('th')]
            rows = table.find_all('tr')[1:]
            bids = []
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 4: continue
                link = row.find('a', href=True); href = link['href'] if link else ''
                if href and not href.startswith('http'): href = f"https://srp.sinica.edu.tw{href}"
                raw = {headers[idx]: col.text.strip() for idx, col in enumerate(cols) if idx < len(headers)}
                bid = {'來源': '中研院', '標題連結': href, '公告日': date}
                for k, v in raw.items():
                    if '案號' in k or '標號' in k: bid['案號'] = v
                    elif '名稱' in k: bid['標題'] = v
                    elif '預算' in k: 
                        m = re.search(r'([\d,]+)', v.replace('$', '')); bid['預算金額'] = m.group(1).replace(',', '') if m else v
                    elif '截止' in k: bid['截止日'] = v
                bids.append(bid)
            if bids: all_dfs.append(pd.DataFrame(bids))
        except: continue
    if not all_dfs: return pd.DataFrame()
    return pd.concat(all_dfs).drop_duplicates(subset=['案號'], keep='first')

# ============================================================================
# 主程式
# ============================================================================

def main():
    print(f"\n{'='*70}\n  三站標案抓取器 v2.9\n{'='*70}")
    status = load_status(); status['attempt'] += 1
    
    all_new_data = []
    for src in SOURCES:
        try:
            scraper_func = {'工研院': scrape_itri, '資策會': scrape_iii, '中研院': scrape_sinica}[src]
            df = scraper_func()
            count = len(df) if not df.empty else 0
            status['sources'][src] = {'status': 'ok', 'count': count, 'time': datetime.now(TW_TZ).strftime('%H:%M:%S'), 'error': ''}
            if count > 0: all_new_data.append(df); add_log(status, f"{src}：成功，{count} 筆")
            else: add_log(status, f"{src}：目前無新標案")
        except Exception as e:
            error_msg = str(e)[:100]; status['sources'][src] = {'status': 'error', 'count': 0, 'time': datetime.now(TW_TZ).strftime('%H:%M:%S'), 'error': error_msg}
            add_log(status, f"{src}：出錯 - {error_msg}")

    new_df = pd.concat(all_new_data) if all_new_data else pd.DataFrame()
    latest_path = 'data/latest.csv'
    old_df = pd.read_csv(latest_path, dtype={'案號': str}) if os.path.exists(latest_path) else pd.DataFrame()
    
    final_df = pd.concat([new_df, old_df], ignore_index=True)
    if not final_df.empty:
        # 優先保留日期不為空的標案
        final_df['tmp_score'] = final_df.apply(lambda x: 1 if x['公告日'] != '-' and x['截止日'] != '-' else 0, axis=1)
        final_df = final_df.sort_values(by=['案號', 'tmp_score'], ascending=[True, False])
        final_df = final_df.drop_duplicates(subset=['案號'], keep='first').drop(columns=['tmp_score']).fillna('')
        
        # 篩選近 3 日並格式化
        def is_recent(d):
            try:
                d_str = str(d).strip().replace('/', '-')
                if len(d_str) == 8 and d_str.isdigit(): d_str = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
                if re.match(r'^\d{2,3}-', d_str): pts = d_str.split('-'); d_str = f"{int(pts[0])+1911}-{pts[1]}-{pts[2]}"
                dt = datetime.strptime(d_str[:10], '%Y-%m-%d').replace(tzinfo=TW_TZ)
                return dt >= (datetime.now(TW_TZ) - timedelta(days=2)).replace(hour=0, minute=0, second=0)
            except: return True
        
        final_df = final_df[final_df['公告日'].apply(is_recent)]
        final_df['公告日'] = final_df['公告日'].apply(format_to_roc)
        final_df['截止日'] = final_df['截止日'].apply(format_to_roc)

    os.makedirs('data', exist_ok=True)
    final_df.to_csv(latest_path, index=False, encoding='utf-8-sig')
    final_df.to_csv(f'data/三站標案_{TODAY}.csv', index=False, encoding='utf-8-sig')
    
    res_json = {
        'update_time': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'), 'date': TODAY_CN, 'total': len(final_df),
        'sources': {s: len(final_df[final_df['來源']==s]) for s in final_df['來源'].unique()} if not final_df.empty else {},
        'data': final_df.to_dict('records') if not final_df.empty else []
    }
    with open('docs/data.json', 'w', encoding='utf-8') as f: json.dump(res_json, f, ensure_ascii=False, indent=2)
    with open(f'docs/data_{TODAY}.json', 'w', encoding='utf-8') as f: json.dump(res_json, f, ensure_ascii=False, indent=2)
    save_status(status); print(f"\n[DONE] 執行完成，共 {len(final_df)} 筆。")

if __name__ == '__main__':
    main()
